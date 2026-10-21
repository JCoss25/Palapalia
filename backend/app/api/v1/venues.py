"""Venues: CRUD del proveedor, búsqueda pública geoespacial, media, favoritos y reseñas del venue."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import AdminUser, CurrentUser, DbSession, MyProvider, PublishedProvider
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Cursor, clamp_limit
from app.core.redis import cache_delete_prefix, cache_get, cache_set
from app.models.enums import (
    AuditAction,
    MediaKind,
    ModerationStatus,
    SlotKind,
    VenueStatus,
)
from app.models.provider import ProviderProfile
from app.models.venue import (
    Address,
    Amenity,
    Category,
    EventType,
    EventTypeVenue,
    Favorite,
    Venue,
    VenueAmenity,
    VenueCategory,
    VenueMedia,
)
from app.schemas.common import ErrorResponse, Paginated, VenueSort
from app.schemas.venue import (
    AddressOut,
    AmenityOut,
    CategoryOut,
    EventTypeOut,
    GeoPoint,
    MediaReorderRequest,
    MediaUploadRequest,
    MediaUploadTicket,
    VenueCreateRequest,
    VenueDetailOut,
    VenueMediaOut,
    VenuePatchRequest,
    VenueSearchParams,
    VenueSummaryOut,
)
from app.services import audit, availability, maps, storage

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _summary(venue: Venue, *, distance_km: float | None = None, is_available: bool | None = None) -> VenueSummaryOut:
    return VenueSummaryOut(
        id=venue.id,
        name=venue.name,
        slug=venue.slug,
        headline=venue.headline,
        city=venue.city,
        state=venue.state,
        neighborhood=venue.neighborhood,
        capacity_min=venue.capacity_min,
        capacity_max=venue.capacity_max,
        base_price_cents=venue.base_price_cents,
        currency=venue.currency,
        rating_avg=float(venue.rating_avg) if venue.rating_avg else None,
        rating_count=venue.rating_count,
        instant_book=venue.instant_book,
        status=venue.status,
        cover_url=venue.cover_url,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
        is_available=is_available,
        price_from_cents=venue.base_price_cents,
        provider_name=venue.provider.business_name if venue.provider else None,
        provider_verified=bool(venue.provider and venue.provider.status == "APPROVED"),
        badges=(
            (["Reserva inmediata"] if venue.instant_book else [])
            + (["Verificado"] if venue.provider and venue.provider.status == "APPROVED" else [])
        ),
    )


async def _load_venue_for_owner(session: DbSession, venue_id: uuid.UUID, provider_id: uuid.UUID) -> Venue:
    """Carga por ID **y** pertenencia: el ID de la ruta nunca autoriza por sí solo."""
    venue = await session.scalar(
        select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
    )
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})
    if venue.provider_id != provider_id:
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="Ese espacio no pertenece a tu cuenta.",
            details={"venue_id": str(venue_id)},
        )
    return venue


def _slugify(name: str, city: str) -> str:
    import re
    import unicodedata

    base = unicodedata.normalize("NFKD", f"{name} {city}").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:180]


# ── CRUD del proveedor ─────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=VenueDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear espacio (proveedor)",
    responses={
        403: {"model": ErrorResponse, "description": "PROVIDER_NOT_APPROVED"},
        422: {"model": ErrorResponse, "description": "ADDRESS_NOT_GEOCODABLE · VALIDATION_ERROR"},
        409: {"model": ErrorResponse, "description": "VENUE_DUPLICATE"},
    },
)
async def create_venue(
    payload: VenueCreateRequest,
    session: DbSession,
    profile: PublishedProvider,
    user: CurrentUser,
    request: Request,
) -> VenueDetailOut:
    """Geocodifica si falta el punto; si el geocoder falla, guarda con pin pendiente (edge case C21)."""
    point = payload.address.point
    geocode_meta: dict[str, object] = {}
    if point is None:
        try:
            result = await maps.geocode(
                payload.address.street or "",
                city=payload.address.city,
                state=payload.address.state,
                postal_code=payload.address.postal_code,
            )
            point = GeoPoint(lat=result.lat, lng=result.lng)
            geocode_meta = {
                "provider": result.provider,
                "confidence": result.confidence,
                "is_approximate": result.is_approximate,
            }
        except AppError as exc:
            if exc.code != ErrorCode.ADDRESS_NOT_GEOCODABLE:
                raise
            geocode_meta = {"provider": None, "confidence": 0, "is_approximate": True}

    address = Address(
        street=payload.address.street,
        exterior_number=payload.address.exterior_number,
        neighborhood=payload.address.neighborhood,
        city=payload.address.city,
        municipality=payload.address.municipality,
        state=payload.address.state,
        postal_code=payload.address.postal_code,
        country_code=payload.address.country_code,
        access_notes=payload.address.access_notes,
        is_approximate=bool(geocode_meta.get("is_approximate", True)),
        geocoded_at=datetime.now(UTC) if point else None,
        geocoding_provider=str(geocode_meta.get("provider")) if geocode_meta.get("provider") else None,
    )
    if point:
        address.point = maps.point_wkt(point.lat, point.lng)
    session.add(address)
    await session.flush()

    slug = _slugify(payload.name, payload.address.city)
    duplicate = await session.scalar(
        select(Venue).where(
            Venue.provider_id == profile.id,
            Venue.name == payload.name,
            Venue.city == payload.address.city,
            Venue.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise AppError(
            ErrorCode.VENUE_DUPLICATE,
            message="Ya tienes un espacio con ese nombre en esa ciudad.",
            details={"existing_venue_id": str(duplicate.id)},
        )

    venue = Venue(
        provider_id=profile.id,
        address_id=address.id,
        name=payload.name,
        slug=f"{slug}-{uuid.uuid4().hex[:6]}",
        headline=payload.headline,
        description=payload.description,
        house_rules=payload.house_rules,
        included_items=payload.included_items,
        city=payload.address.city,
        state=payload.address.state,
        neighborhood=payload.address.neighborhood,
        timezone=payload.timezone,
        capacity_min=payload.capacity_min,
        capacity_max=payload.capacity_max,
        min_hours=payload.min_hours,
        max_hours=payload.max_hours,
        buffer_minutes=payload.buffer_minutes,
        slot_kind_default=payload.slot_kind_default.value,
        allows_multi_day=payload.allows_multi_day,
        crosses_midnight=payload.crosses_midnight,
        curfew_time=payload.curfew_time,
        base_price_cents=payload.base_price_cents,
        currency=payload.currency,
        deposit_percent=payload.deposit_percent,
        security_deposit_cents=payload.security_deposit_cents,
        cleaning_fee_cents=payload.cleaning_fee_cents,
        instant_book=payload.instant_book and profile.payouts_enabled,
        min_notice_hours=payload.min_notice_hours,
        status=VenueStatus.DRAFT.value,
        created_by_id=user.id,
    )
    session.add(venue)
    await session.flush()

    await _sync_taxonomies(session, venue, payload.category_slugs, payload.amenity_slugs, payload.event_type_slugs)
    await audit.record(
        session,
        action=AuditAction.VENUE_CREATED,
        entity="venue",
        entity_id=venue.id,
        actor_id=user.id,
        actor_role=user.role.value,
        after={"name": venue.name, "city": venue.city, "status": venue.status},
        request=request,
    )
    await session.commit()
    return await _detail(session, venue, viewer_id=user.id)


async def _sync_taxonomies(
    session: DbSession,
    venue: Venue,
    category_slugs: list[str] | None,
    amenity_slugs: list[str] | None,
    event_type_slugs: list[str] | None,
) -> None:
    """Reemplaza categorías/amenidades/tipos. Si un slug ya no existe, se avisa en vez de fallar."""
    if category_slugs is not None:
        rows = (
            await session.scalars(
                select(Category).where(Category.slug.in_(category_slugs), Category.deleted_at.is_(None))
            )
        ).all()
        missing = set(category_slugs) - {row.slug for row in rows}
        if missing:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                message=f"Categoría(s) no válida(s): {', '.join(sorted(missing))}",
                details={"field": "category_slugs"},
            )
        for index, row in enumerate(rows):
            session.add(
                VenueCategory(venue_id=venue.id, category_id=row.id, is_primary=index == 0)
            )

    if amenity_slugs is not None:
        rows = (
            await session.scalars(
                select(Amenity).where(Amenity.slug.in_(amenity_slugs), Amenity.deleted_at.is_(None))
            )
        ).all()
        for row in rows:
            session.add(
                VenueAmenity(
                    venue_id=venue.id,
                    amenity_id=row.id,
                    is_included=row.is_included_by_default,
                    extra_price_cents=None if row.is_included_by_default else row.default_price_cents,
                )
            )

    if event_type_slugs is not None:
        rows = (
            await session.scalars(
                select(EventType).where(EventType.slug.in_(event_type_slugs), EventType.deleted_at.is_(None))
            )
        ).all()
        for row in rows:
            session.add(EventTypeVenue(venue_id=venue.id, event_type_id=row.id))
    await session.flush()


async def _detail(session: DbSession, venue: Venue, *, viewer_id: uuid.UUID | None = None) -> VenueDetailOut:
    categories = (
        await session.scalars(
            select(Category)
            .join(VenueCategory, VenueCategory.category_id == Category.id)
            .where(VenueCategory.venue_id == venue.id)
        )
    ).all()
    amenities = (
        await session.scalars(
            select(Amenity)
            .join(VenueAmenity, VenueAmenity.amenity_id == Amenity.id)
            .where(VenueAmenity.venue_id == venue.id)
        )
    ).all()
    event_types = (
        await session.scalars(
            select(EventType)
            .join(EventTypeVenue, EventTypeVenue.event_type_id == EventType.id)
            .where(EventTypeVenue.venue_id == venue.id)
        )
    ).all()
    is_favorite = False
    if viewer_id:
        is_favorite = (
            await session.scalar(
                select(func.count())
                .select_from(Favorite)
                .where(Favorite.venue_id == venue.id, Favorite.user_id == viewer_id)
            )
            or 0
        ) > 0

    base = _summary(venue)
    address = venue.address
    return VenueDetailOut(
        **base.model_dump(),
        description=venue.description,
        house_rules=venue.house_rules,
        included_items=venue.included_items or [],
        address=AddressOut(
            id=address.id,
            street=address.street,
            exterior_number=address.exterior_number,
            neighborhood=address.neighborhood,
            city=address.city,
            municipality=address.municipality,
            state=address.state,
            postal_code=address.postal_code,
            country_code=address.country_code,
            point=None,
            is_approximate=address.is_approximate,
            pin_adjusted_by_provider=address.pin_adjusted_by_provider,
            access_notes=address.access_notes,
        )
        if address
        else None,
        timezone=venue.timezone,
        min_hours=venue.min_hours,
        max_hours=venue.max_hours,
        buffer_minutes=venue.buffer_minutes,
        slot_kind_default=SlotKind(venue.slot_kind_default),
        allows_multi_day=venue.allows_multi_day,
        crosses_midnight=venue.crosses_midnight,
        curfew_time=venue.curfew_time,
        deposit_percent=venue.deposit_percent,
        security_deposit_cents=venue.security_deposit_cents,
        cleaning_fee_cents=venue.cleaning_fee_cents,
        min_notice_hours=venue.min_notice_hours,
        media=[VenueMediaOut.model_validate(item) for item in venue.media],
        categories=[CategoryOut.model_validate(row) for row in categories],
        amenities=[AmenityOut.model_validate(row) for row in amenities],
        event_types=[EventTypeOut.model_validate(row) for row in event_types],
        is_favorite=is_favorite,
        views_count=venue.views_count,
        quality_flags=venue.quality_flags,
    )


@router.get(
    "/{venue_id}",
    response_model=VenueDetailOut,
    summary="Detalle público del espacio",
    responses={404: {"model": ErrorResponse, "description": "VENUE_NOT_FOUND"}},
)
async def get_venue(venue_id: uuid.UUID, session: DbSession) -> VenueDetailOut:
    venue = await session.scalar(
        select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
    )
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})
    if venue.status not in {VenueStatus.PUBLISHED.value, VenueStatus.PAUSED.value}:
        raise AppError(
            ErrorCode.VENUE_NOT_FOUND,
            message="Este espacio no está publicado.",
            details={"status": venue.status},
        )
    venue.views_count += 1
    await session.commit()
    return await _detail(session, venue)


@router.patch(
    "/{venue_id}",
    response_model=VenueDetailOut,
    summary="Editar espacio (proveedor dueño)",
    responses={403: {"model": ErrorResponse, "description": "FORBIDDEN"}},
)
async def patch_venue(
    venue_id: uuid.UUID,
    payload: VenuePatchRequest,
    session: DbSession,
    profile: MyProvider,
    user: CurrentUser,
    request: Request,
) -> VenueDetailOut:
    """Cambios de capacidad/precio no alteran reservas ya confirmadas: el booking guarda snapshot."""
    venue = await _load_venue_for_owner(session, venue_id, profile.id)
    data = payload.model_dump(exclude_unset=True)
    before = {"status": venue.status, "capacity_max": venue.capacity_max, "base_price_cents": venue.base_price_cents}

    if "address" in data and data["address"]:
        address_payload = data["address"]
        address = venue.address or Address(city=venue.city, state=venue.state)
        for field, value in address_payload.items():
            if field == "point":
                if value:
                    address.point = maps.point_wkt(value["lat"], value["lng"])
                    address.pin_adjusted_by_provider = True
                    address.is_approximate = False
                continue
            setattr(address, field, value)
        session.add(address)
        await session.flush()
        venue.address_id = address.id
        venue.city = address.city
        venue.state = address.state
        venue.neighborhood = address.neighborhood

    for field in (
        "name",
        "headline",
        "description",
        "house_rules",
        "included_items",
        "capacity_min",
        "capacity_max",
        "min_hours",
        "max_hours",
        "buffer_minutes",
        "base_price_cents",
        "deposit_percent",
        "security_deposit_cents",
        "cleaning_fee_cents",
    ):
        if field in data:
            setattr(venue, field, data[field])

    if "instant_book" in data:
        if data["instant_book"] and not profile.payouts_enabled:
            raise AppError(
                ErrorCode.PROVIDER_PAYOUTS_NOT_ENABLED,
                message="Activa tu cuenta de pagos para ofrecer reserva inmediata.",
            )
        venue.instant_book = data["instant_book"]

    if "status" in data:
        if data["status"] == "PUBLISHED":
            if profile.status != "APPROVED":
                raise AppError(
                    ErrorCode.PROVIDER_NOT_APPROVED,
                    message="Tu perfil debe estar aprobado para publicar.",
                )
            venue.status = VenueStatus.PUBLISHED.value
            venue.published_at = venue.published_at or datetime.now(UTC)
        else:
            venue.status = VenueStatus(data["status"]).value

    await _sync_taxonomies(
        session,
        venue,
        data.get("category_slugs"),
        data.get("amenity_slugs"),
        data.get("event_type_slugs"),
    )
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="venue",
        entity_id=venue.id,
        actor_id=user.id,
        actor_role=user.role.value,
        before=before,
        after={k: v for k, v in data.items() if k != "address"},
        request=request,
    )
    await cache_delete_prefix("search:")
    await session.commit()
    return await _detail(session, venue, viewer_id=user.id)


@router.delete(
    "/{venue_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archivar espacio (soft-delete)",
    responses={409: {"model": ErrorResponse, "description": "CONFLICT (reservas futuras)"}},
)
async def delete_venue(
    venue_id: uuid.UUID, session: DbSession, profile: MyProvider, user: CurrentUser, request: Request
) -> Response:
    from app.models.booking import Booking

    venue = await _load_venue_for_owner(session, venue_id, profile.id)
    upcoming = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.venue_id == venue.id,
            Booking.start_at > datetime.now(UTC),
            Booking.state.in_(["CONFIRMED", "PENDING_PAYMENT"]),
        )
    )
    if upcoming:
        raise AppError(
            ErrorCode.CONFLICT,
            message=f"Tienes {upcoming} reserva(s) futura(s). Ciérralas antes de archivar el espacio.",
            details={"upcoming_bookings": int(upcoming)},
        )
    venue.deleted_at = datetime.now(UTC)
    venue.deleted_by_id = user.id
    venue.status = VenueStatus.DELETED.value
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="venue",
        entity_id=venue.id,
        actor_id=user.id,
        reason="archivado por el proveedor",
        request=request,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Búsqueda pública ──────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=Paginated[VenueSummaryOut],
    summary="Buscar espacios",
)
async def search_venues(
    session: DbSession,
    q: str | None = Query(None, max_length=120),
    category: list[str] = Query(default_factory=list),
    city: str | None = None,
    state: str | None = None,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_km: float = Query(25, gt=0),
    capacity_min: int | None = Query(None, ge=1),
    capacity_max: int | None = Query(None, ge=1),
    price_min: int | None = Query(None, ge=0),
    price_max: int | None = Query(None, ge=0),
    date_from: date | None = None,
    date_to: date | None = None,
    time_slot: SlotKind | None = None,
    amenities: list[str] = Query(default_factory=list),
    event_types: list[str] = Query(default_factory=list),
    rating_min: float | None = Query(None, ge=0, le=5),
    instant_book: bool | None = None,
    verified_only: bool = False,
    sort: VenueSort = VenueSort.RECOMMENDED,
    page: int = Query(1, ge=1, le=200),
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,
) -> Paginated[VenueSummaryOut]:
    """Búsqueda geoespacial con caché corta y límites explícitos (edge cases F29, F30).

    `is_available` se calcula sólo cuando hay fecha: sin fecha no se promete disponibilidad.
    """
    params = VenueSearchParams(
        q=q, category=category, city=city, state=state, lat=lat, lng=lng, radius_km=radius_km,
        capacity_min=capacity_min, capacity_max=capacity_max, price_min=price_min, price_max=price_max,
        date_from=date_from, date_to=date_to, time_slot=time_slot, amenities=amenities,
        event_types=event_types, rating_min=rating_min, instant_book=instant_book,
        verified_only=verified_only, sort=sort, page=page, limit=limit, cursor=cursor,
    )
    if params.radius_km > settings.search_max_radius_km:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message=f"El radio máximo de búsqueda es {settings.search_max_radius_km} km.",
            details={"field": "radius_km", "max": settings.search_max_radius_km},
        )

    cache_key = f"search:{hash((params.model_dump_json(), page))}"
    cached = await cache_get(cache_key)
    if cached:
        return Paginated[VenueSummaryOut](**cached)

    distance_col = None
    stmt = select(Venue).where(
        Venue.deleted_at.is_(None), Venue.status == VenueStatus.PUBLISHED.value
    )
    if lat is not None and lng is not None:
        point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
        distance_col = func.ST_Distance(Address.point, point)
        stmt = stmt.join(Address, Address.id == Venue.address_id).where(
            func.ST_DWithin(Address.point, point, radius_km * 1000)
        )
    if q:
        # trigram + full text: el `%` es tolerante a errores de dedo
        stmt = stmt.where(
            or_(
                Venue.name.ilike(f"%{q}%"),
                Venue.headline.ilike(f"%{q}%"),
                Venue.city.ilike(f"%{q}%"),
            )
        )
    if city:
        stmt = stmt.where(Venue.city.ilike(city))
    if state:
        stmt = stmt.where(Venue.state.ilike(state))
    if capacity_min:
        stmt = stmt.where(Venue.capacity_max >= capacity_min)
    if capacity_max:
        stmt = stmt.where(Venue.capacity_min <= capacity_max)
    if price_min:
        stmt = stmt.where(Venue.base_price_cents >= price_min)
    if price_max:
        stmt = stmt.where(Venue.base_price_cents <= price_max)
    if rating_min:
        stmt = stmt.where(Venue.rating_avg >= rating_min)
    if instant_book is not None:
        stmt = stmt.where(Venue.instant_book.is_(instant_book))
    if verified_only:
        stmt = stmt.where(
            Venue.provider_id.in_(
                select(ProviderProfile.id).where(ProviderProfile.status == "APPROVED")
            )
        )
    if category:
        stmt = stmt.where(
            Venue.id.in_(
                select(VenueCategory.venue_id)
                .join(Category, Category.id == VenueCategory.category_id)
                .where(Category.slug.in_(category))
            )
        )
    if amenities:
        stmt = stmt.where(
            Venue.id.in_(
                select(VenueAmenity.venue_id)
                .join(Amenity, Amenity.id == VenueAmenity.amenity_id)
                .where(Amenity.slug.in_(amenities))
            )
        )
    if event_types:
        stmt = stmt.where(
            Venue.id.in_(
                select(EventTypeVenue.venue_id)
                .join(EventType, EventType.id == EventTypeVenue.event_type_id)
                .where(EventType.slug.in_(event_types))
            )
        )

    order = {
        VenueSort.PRICE_ASC: Venue.base_price_cents.asc().nulls_last(),
        VenueSort.PRICE_DESC: Venue.base_price_cents.desc().nulls_last(),
        VenueSort.RATING_DESC: Venue.rating_avg.desc().nulls_last(),
        VenueSort.NEWEST: Venue.published_at.desc().nulls_last(),
        VenueSort.DISTANCE_ASC: distance_col.asc() if distance_col is not None else Venue.id.asc(),
        VenueSort.RECOMMENDED: (
            (func.coalesce(Venue.rating_avg, 0) * 10 + func.coalesce(Venue.trust_score, 0) / 10).desc()
        ),
    }[sort]
    stmt = stmt.order_by(order, Venue.id.asc()).limit(clamp_limit(limit) + 1)

    venues = (await session.scalars(stmt)).all()
    items: list[VenueSummaryOut] = []
    for venue in venues[: clamp_limit(limit)]:
        available: bool | None = None
        if date_from:
            window_end = datetime.combine(
                date_to or date_from, datetime.min.time(), tzinfo=UTC
            ) + timedelta(days=1)
            window_start = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
            free, _ = await availability.is_slot_free(
                session, venue, window_start, max(window_end, window_start + timedelta(hours=venue.min_hours))
            )
            available = free
        items.append(_summary(venue, distance_km=None, is_available=available))

    page_out = Paginated[VenueSummaryOut](
        items=items,
        meta={
            "limit": limit,
            "has_more": len(venues) > limit,
            "next_cursor": Cursor(
                sort_value=str(items[-1].id) if items else None, id=str(items[-1].id) if items else ""
            ).encode()
            if len(venues) > limit and items
            else None,
            "total_estimate": None,
        },
    )
    await cache_set(cache_key, page_out.model_dump(mode="json"), ttl_seconds=60)
    return page_out


# ── Media ──────────────────────────────────────────────────────────────────────


@router.post(
    "/{venue_id}/media",
    response_model=MediaUploadTicket,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar URL firmada para subir media",
    responses={
        415: {"model": ErrorResponse, "description": "MEDIA_TYPE_NOT_ALLOWED"},
        409: {"model": ErrorResponse, "description": "MEDIA_LIMIT_REACHED"},
    },
)
async def request_media_upload(
    venue_id: uuid.UUID, payload: MediaUploadRequest, session: DbSession, profile: MyProvider, user: CurrentUser
) -> MediaUploadTicket:
    """Valida tipo/tamaño antes de firmar y crea el registro en `PENDING` de moderación (edge case D20)."""
    venue = await _load_venue_for_owner(session, venue_id, profile.id)
    count = await session.scalar(
        select(func.count()).select_from(VenueMedia).where(VenueMedia.venue_id == venue.id)
    )
    if (count or 0) >= 40:
        raise AppError(
            ErrorCode.MEDIA_LIMIT_REACHED,
            message="Alcanzaste el límite de 40 archivos por espacio.",
            details={"limit": 40, "current": int(count or 0)},
        )
    kind = "video" if payload.kind == MediaKind.VIDEO else "photo"
    storage.validate_media(payload.mime_type, payload.size_bytes, kind=kind)

    key = storage.build_key(storage.PREFIX_MEDIA, venue.id, payload.filename)
    ticket = storage.presign_upload(
        key, payload.mime_type, max_size_bytes=payload.size_bytes + 1024
    )
    media = VenueMedia(
        venue_id=venue.id,
        kind=payload.kind.value,
        url=storage.public_url(key),
        storage_key=key,
        sort_order=int(count or 0),
        alt_text=payload.alt_text,
        moderation_status=ModerationStatus.PENDING.value,
    )
    session.add(media)
    await session.commit()
    return MediaUploadTicket(
        media_id=media.id,
        upload_url=ticket.url,
        storage_key=ticket.storage_key,
        fields=ticket.fields,
        expires_in_seconds=ticket.expires_in,
        max_size_bytes=payload.size_bytes,
    )


@router.patch(
    "/{venue_id}/media/reorder",
    response_model=list[VenueMediaOut],
    summary="Reordenar media y elegir portada",
)
async def reorder_media(
    venue_id: uuid.UUID, payload: MediaReorderRequest, session: DbSession, profile: MyProvider, user: CurrentUser
) -> list[VenueMediaOut]:
    venue = await _load_venue_for_owner(session, venue_id, profile.id)
    media = (
        await session.scalars(select(VenueMedia).where(VenueMedia.venue_id == venue.id))
    ).all()
    by_id = {item.id: item for item in media}
    unknown = set(payload.order) - set(by_id)
    if unknown:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="Hay archivos que no pertenecen a este espacio.",
            details={"media_ids": [str(item) for item in unknown]},
        )
    for index, media_id in enumerate(payload.order):
        by_id[media_id].sort_order = index
        by_id[media_id].is_cover = media_id == payload.cover_media_id
    await session.commit()
    return [VenueMediaOut.model_validate(by_id[media_id]) for media_id in payload.order]


@router.delete(
    "/{venue_id}/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar media",
)
async def delete_media(
    venue_id: uuid.UUID, media_id: uuid.UUID, session: DbSession, profile: MyProvider, user: CurrentUser
) -> Response:
    venue = await _load_venue_for_owner(session, venue_id, profile.id)
    media = await session.scalar(
        select(VenueMedia).where(VenueMedia.id == media_id, VenueMedia.venue_id == venue.id)
    )
    if media is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"media_id": str(media_id)})
    media.deleted_at = datetime.now(UTC)
    media.deleted_by_id = user.id
    storage.delete_object(media.storage_key)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Favoritos ─────────────────────────────────────────────────────────────────


@router.post("/{venue_id}/favorite", status_code=status.HTTP_204_NO_CONTENT, summary="Guardar en favoritos")
async def add_favorite(venue_id: uuid.UUID, session: DbSession, user: CurrentUser) -> Response:
    existing = await session.scalar(
        select(Favorite).where(Favorite.venue_id == venue_id, Favorite.user_id == user.id)
    )
    if existing is None:
        session.add(Favorite(venue_id=venue_id, user_id=user.id))
        venue = await session.get(Venue, venue_id)
        if venue:
            venue.favorites_count += 1
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{venue_id}/favorite", status_code=status.HTTP_204_NO_CONTENT, summary="Quitar de favoritos")
async def remove_favorite(venue_id: uuid.UUID, session: DbSession, user: CurrentUser) -> Response:
    existing = await session.scalar(
        select(Favorite).where(Favorite.venue_id == venue_id, Favorite.user_id == user.id)
    )
    if existing is not None:
        await session.delete(existing)
        venue = await session.get(Venue, venue_id)
        if venue and venue.favorites_count:
            venue.favorites_count -= 1
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Admin ──────────────────────────────────────────────────────────────────────


admin_router = APIRouter()


@admin_router.post("/{venue_id}/suspend", summary="Suspender espacio (admin)")
async def admin_suspend_venue(
    venue_id: uuid.UUID,
    payload: dict,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> dict:
    """Suspender oculta el venue; opcionalmente cancela reservas futuras con reembolso total."""
    from app.models.booking import Booking
    from app.services import bookings as booking_service
    from app.services.state_machine import ADMIN

    reason = str(payload.get("reason", "Incumplimiento de las políticas"))
    cancel_future = bool(payload.get("cancel_future_bookings", False))

    venue = await session.get(Venue, venue_id)
    if venue is None or venue.deleted_at is not None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})

    venue.status = VenueStatus.SUSPENDED.value
    venue.suspended_at = datetime.now(UTC)
    venue.suspension_reason = reason
    cancelled = 0
    if cancel_future:
        upcoming = (
            await session.scalars(
                select(Booking).where(
                    Booking.venue_id == venue.id,
                    Booking.start_at > datetime.now(UTC),
                    Booking.state.in_(["CONFIRMED", "PENDING_PAYMENT"]),
                )
            )
        ).all()
        for booking in upcoming:
            await booking_service.cancel_booking(
                session,
                booking=booking,
                actor=ADMIN,
                actor_id=admin.id,
                reason=f"Suspensión del espacio: {reason}",
                request_refund=True,
                apply_policy=False,
            )
            cancelled += 1

    await audit.record(
        session,
        action=AuditAction.VENUE_SUSPENDED,
        entity="venue",
        entity_id=venue.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        reason=reason,
        after={"cancelled_bookings": cancelled},
        request=request,
    )
    await cache_delete_prefix("search:")
    await session.commit()
    return {"venue_id": str(venue.id), "status": venue.status, "cancelled_bookings": cancelled}
