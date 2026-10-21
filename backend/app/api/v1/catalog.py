"""Catálogos públicos (categorías, amenidades, tipos de evento), sugerencias y home destacado."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import AdminUser, DbSession
from app.core.errors import AppError, ErrorCode
from app.core.redis import cache_get, cache_set
from app.models.enums import AuditAction, VenueStatus
from app.models.venue import Amenity, Category, EventType, Venue, VenueCategory
from app.schemas.common import Paginated
from app.schemas.venue import (
    AmenityOut,
    AmenityUpsert,
    CategoryOut,
    CategoryUpsert,
    EventTypeOut,
    EventTypeUpsert,
    HomeFeaturedOut,
    SearchSuggestionOut,
    VenueSummaryOut,
)
from app.services import audit

router = APIRouter()


@router.get("/categories", response_model=list[CategoryOut], summary="Categorías")
async def list_categories(session: DbSession) -> list[CategoryOut]:
    cached = await cache_get("catalog:categories")
    if cached:
        return [CategoryOut(**item) for item in cached]
    rows = (
        await session.scalars(
            select(Category)
            .where(Category.deleted_at.is_(None), Category.is_active.is_(True))
            .order_by(Category.sort_order)
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(VenueCategory.category_id, func.count())
                .join(Venue, Venue.id == VenueCategory.venue_id)
                .where(Venue.status == VenueStatus.PUBLISHED.value)
                .group_by(VenueCategory.category_id)
            )
        ).all()
    )
    out = [
        CategoryOut(
            id=row.id,
            slug=row.slug,
            name=row.name,
            description=row.description,
            icon_key=row.icon_key,
            sort_order=row.sort_order,
            venues_count=int(counts.get(row.id, 0)),
        )
        for row in rows
    ]
    await cache_set("catalog:categories", [item.model_dump(mode="json") for item in out], ttl_seconds=600)
    return out


@router.get("/amenities", response_model=list[AmenityOut], summary="Amenidades")
async def list_amenities(
    session: DbSession, category: str | None = None
) -> list[AmenityOut]:
    stmt = select(Amenity).where(Amenity.deleted_at.is_(None), Amenity.is_active.is_(True))
    if category:
        stmt = stmt.where(Amenity.category == category)
    rows = (await session.scalars(stmt.order_by(Amenity.category, Amenity.name))).all()
    return [AmenityOut.model_validate(row) for row in rows]


@router.get("/event-types", response_model=list[EventTypeOut], summary="Tipos de evento")
async def list_event_types(session: DbSession) -> list[EventTypeOut]:
    rows = (
        await session.scalars(
            select(EventType)
            .where(EventType.deleted_at.is_(None), EventType.is_active.is_(True))
            .order_by(EventType.name)
        )
    ).all()
    return [EventTypeOut.model_validate(row) for row in rows]


@router.get(
    "/search/suggestions",
    response_model=list[SearchSuggestionOut],
    summary="Sugerencias de búsqueda",
)
async def suggestions(
    session: DbSession, q: str = Query(..., min_length=2, max_length=60), limit: int = Query(8, ge=1, le=20)
) -> list[SearchSuggestionOut]:
    """Mezcla venues, ciudades, categorías y tipos de evento. Se cachea 60 s por consulta."""
    cache_key = f"suggest:{q.lower()}:{limit}"
    cached = await cache_get(cache_key)
    if cached:
        return [SearchSuggestionOut(**item) for item in cached]

    term = f"%{q.lower()}%"
    out: list[SearchSuggestionOut] = []

    venues = (
        await session.scalars(
            select(Venue)
            .where(
                Venue.deleted_at.is_(None),
                Venue.status == VenueStatus.PUBLISHED.value,
                or_(Venue.name.ilike(term), Venue.city.ilike(term), Venue.headline.ilike(term)),
            )
            .limit(limit)
        )
    ).all()
    for venue in venues:
        out.append(
            SearchSuggestionOut(
                kind="venue",
                text=venue.name,
                subtitle=f"{venue.city}, {venue.state} · hasta {venue.capacity_max} personas",
                venue_id=venue.id,
                score=1.0,
            )
        )

    cities = (
        await session.execute(
            select(Venue.city, Venue.state, func.count())
            .where(
                Venue.deleted_at.is_(None),
                Venue.status == VenueStatus.PUBLISHED.value,
                Venue.city.ilike(term),
            )
            .group_by(Venue.city, Venue.state)
            .order_by(func.count().desc())
            .limit(3)
        )
    ).all()
    for city, state, count in cities:
        out.append(
            SearchSuggestionOut(
                kind="city", text=city, subtitle=f"{state} · {count} espacios", score=0.9
            )
        )

    categories = (
        await session.scalars(
            select(Category).where(Category.name.ilike(term), Category.deleted_at.is_(None)).limit(3)
        )
    ).all()
    for category in categories:
        out.append(
            SearchSuggestionOut(
                kind="category", text=category.name, category_slug=category.slug, score=0.8
            )
        )

    event_types = (
        await session.scalars(
            select(EventType).where(EventType.name.ilike(term), EventType.deleted_at.is_(None)).limit(3)
        )
    ).all()
    for event_type in event_types:
        out.append(SearchSuggestionOut(kind="event_type", text=event_type.name, score=0.7))

    result = out[:limit]
    await cache_set(cache_key, [item.model_dump(mode="json") for item in result], ttl_seconds=60)
    return result


@router.get(
    "/home/featured",
    response_model=list[HomeFeaturedOut],
    summary="Secciones del inicio",
)
async def home_featured(
    session: DbSession,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    city: str | None = None,
) -> list[HomeFeaturedOut]:
    """La sección `sponsored` **siempre** lleva la etiqueta `Patrocinado` (requisito de transparencia)."""
    sections: list[HomeFeaturedOut] = []

    sponsored = (
        await session.scalars(
            select(Venue)
            .where(
                Venue.deleted_at.is_(None),
                Venue.status == VenueStatus.PUBLISHED.value,
                Venue.quality_flags["sponsored"].astext == "true",
            )
            .limit(4)
        )
    ).all()
    if sponsored:
        sections.append(
            HomeFeaturedOut(
                section="sponsored",
                title="Destacados",
                subtitle="Espacios que están promocionando su disponibilidad",
                venues=[_summary(venue, sponsored=True) for venue in sponsored],
                disclosure="Patrocinado · estos espacios pagaron por aparecer aquí",
            )
        )

    trending = (
        await session.scalars(
            select(Venue)
            .where(
                Venue.deleted_at.is_(None),
                Venue.status == VenueStatus.PUBLISHED.value,
                Venue.rating_avg.isnot(None),
            )
            .order_by(Venue.rating_avg.desc(), Venue.bookings_count.desc())
            .limit(6)
        )
    ).all()
    sections.append(
        HomeFeaturedOut(
            section="trending",
            title="Mejor calificados",
            subtitle="Con más eventos completados y reseñas verificadas",
            venues=[_summary(venue) for venue in trending],
        )
    )

    last_minute = (
        await session.scalars(
            select(Venue)
            .where(
                Venue.deleted_at.is_(None),
                Venue.status == VenueStatus.PUBLISHED.value,
                Venue.min_notice_hours <= 48,
            )
            .limit(6)
        )
    ).all()
    sections.append(
        HomeFeaturedOut(
            section="last_minute",
            title="Para esta semana",
            subtitle="Espacios que aceptan reservas con poca anticipación",
            venues=[_summary(venue) for venue in last_minute],
        )
    )

    newest = (
        await session.scalars(
            select(Venue)
            .where(Venue.deleted_at.is_(None), Venue.status == VenueStatus.PUBLISHED.value)
            .order_by(Venue.published_at.desc().nulls_last())
            .limit(6)
        )
    ).all()
    sections.append(
        HomeFeaturedOut(
            section="new",
            title="Recién publicados",
            subtitle="Espacios nuevos en Quinta",
            venues=[_summary(venue) for venue in newest],
        )
    )
    return sections


def _summary(venue: Venue, *, sponsored: bool = False) -> VenueSummaryOut:
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
        price_from_cents=venue.base_price_cents,
        provider_name=venue.provider.business_name if venue.provider else None,
        provider_verified=bool(venue.provider and venue.provider.status == "APPROVED"),
        sponsored=sponsored,
    )


# ── Admin de catálogos ────────────────────────────────────────────────────────


admin_router = APIRouter()


@admin_router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def upsert_category(
    payload: CategoryUpsert, session: DbSession, admin: AdminUser, request: Request
) -> CategoryOut:
    category = await session.scalar(select(Category).where(Category.slug == payload.slug))
    if category is None:
        category = Category(**payload.model_dump())
        session.add(category)
    else:
        for field, value in payload.model_dump().items():
            setattr(category, field, value)
        category.deleted_at = None
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="category",
        entity_id=category.id,
        actor_id=admin.id,
        after=payload.model_dump(),
        request=request,
    )
    await session.commit()
    return CategoryOut.model_validate(category)


@admin_router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID, session: DbSession, admin: AdminUser, request: Request
) -> None:
    """Soft-delete con migración: los venues que la usaban se reasignan a la categoría por defecto."""
    from fastapi import Response

    category = await session.get(Category, category_id)
    if category is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"category_id": str(category_id)})
    fallback = await session.scalar(
        select(Category).where(Category.slug == "salon", Category.id != category.id)
    )
    migrated = 0
    if fallback is not None:
        links = (
            await session.scalars(
                select(VenueCategory).where(VenueCategory.category_id == category.id)
            )
        ).all()
        for link in links:
            exists = await session.scalar(
                select(VenueCategory).where(
                    VenueCategory.venue_id == link.venue_id,
                    VenueCategory.category_id == fallback.id,
                )
            )
            if exists is None:
                session.add(
                    VenueCategory(venue_id=link.venue_id, category_id=fallback.id, is_primary=link.is_primary)
                )
            await session.delete(link)
            migrated += 1
    category.deleted_at = datetime.now(UTC)
    category.is_active = False
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="category",
        entity_id=category.id,
        actor_id=admin.id,
        reason=f"soft-delete; migrados {migrated} venues a {fallback.slug if fallback else 'n/a'}",
        request=request,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post("/amenities", response_model=AmenityOut, status_code=status.HTTP_201_CREATED)
async def upsert_amenity(
    payload: AmenityUpsert, session: DbSession, admin: AdminUser, request: Request
) -> AmenityOut:
    amenity = await session.scalar(select(Amenity).where(Amenity.slug == payload.slug))
    if amenity is None:
        amenity = Amenity(**payload.model_dump())
        session.add(amenity)
    else:
        for field, value in payload.model_dump().items():
            setattr(amenity, field, value)
        amenity.deleted_at = None
    await session.commit()
    return AmenityOut.model_validate(amenity)


@admin_router.post("/event-types", response_model=EventTypeOut, status_code=status.HTTP_201_CREATED)
async def upsert_event_type(
    payload: EventTypeUpsert, session: DbSession, admin: AdminUser, request: Request
) -> EventTypeOut:
    event_type = await session.scalar(select(EventType).where(EventType.slug == payload.slug))
    if event_type is None:
        event_type = EventType(**payload.model_dump())
        session.add(event_type)
    else:
        for field, value in payload.model_dump().items():
            setattr(event_type, field, value)
        event_type.deleted_at = None
    await session.commit()
    return EventTypeOut.model_validate(event_type)
