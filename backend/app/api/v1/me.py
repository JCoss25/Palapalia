"""Perfil propio: GET/PATCH/DELETE /me, favoritos, exportación de datos."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import normalize_email, normalize_phone, verify_password
from app.models.enums import AuditAction, UserStatus
from app.models.user import User, VerificationDocument
from app.models.venue import Favorite, Venue
from app.schemas.auth import DeleteAccountRequest, DeleteAccountResponse, UserOut, UserUpdateRequest
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.venue import VenueSummaryOut
from app.services import audit

router = APIRouter()


def _out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        full_name=user.full_name,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        phone_e164=user.phone_e164,
        email_verified_at=user.email_verified_at,
        phone_verified_at=user.phone_verified_at,
        locale=user.locale,
        timezone=user.timezone,
        created_at=user.created_at,
        has_provider_profile=user.provider_profile is not None,
    )


@router.get("/me", response_model=UserOut, summary="Mi perfil")
async def get_me(user: CurrentUser) -> UserOut:
    return _out(user)


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Actualizar mi perfil",
    responses={409: {"model": ErrorResponse, "description": "PHONE_ALREADY_REGISTERED"}},
)
async def patch_me(payload: UserUpdateRequest, session: DbSession, user: CurrentUser, request: Request) -> UserOut:
    """Cambiar el teléfono exige reverificar: se limpia `phone_verified_at`."""
    changes: dict[str, object] = {}
    data = payload.model_dump(exclude_unset=True)

    if "phone" in data and data["phone"]:
        phone = normalize_phone(str(data["phone"]))
        clash = await session.scalar(
            select(User).where(User.phone_e164 == phone, User.id != user.id)
        )
        if clash is not None:
            raise AppError(ErrorCode.PHONE_ALREADY_REGISTERED)
        if phone != user.phone_e164:
            user.phone_verified_at = None
        user.phone_e164 = phone
        changes["phone_e164"] = "***"

    for field in ("full_name", "display_name", "avatar_url", "locale", "timezone", "marketing_opt_in"):
        if field in data:
            setattr(user, field, data[field])
            changes[field] = data[field]

    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        actor_role=user.role.value,
        after=changes,
        request=request,
    )
    await session.commit()
    return _out(user)


@router.delete(
    "/me",
    response_model=DeleteAccountResponse,
    summary="Eliminar mi cuenta (soft-delete + anonimización)",
    responses={409: {"model": ErrorResponse, "description": "CONFLICT (reservas futuras activas)"}},
)
async def delete_me(
    payload: DeleteAccountRequest, session: DbSession, user: CurrentUser, request: Request
) -> DeleteAccountResponse:
    """LFPDPPP: se anonimiza la PII y se conservan los registros fiscales (ADR-010).

    Bloquea el borrado si hay reservas futuras confirmadas: primero hay que cancelarlas.
    """
    from app.models.booking import Booking
    from app.models.enums import BookingState

    if user.hashed_password and payload.password and not verify_password(payload.password, user.hashed_password):
        raise AppError(ErrorCode.INVALID_CREDENTIALS, message="La contraseña no coincide.")

    upcoming = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.client_id == user.id,
            Booking.state.in_([BookingState.CONFIRMED.value, BookingState.PENDING_PAYMENT.value]),
            Booking.start_at > datetime.now(UTC),
        )
    )
    if upcoming:
        raise AppError(
            ErrorCode.CONFLICT,
            message=f"Tienes {upcoming} reserva(s) por delante. Cancélalas antes de eliminar tu cuenta.",
            details={"upcoming_bookings": upcoming, "hint": "GET /me/bookings?status=CONFIRMED"},
        )

    now = datetime.now(UTC)
    retained = ["invoices", "payments", "payouts", "ledger_entries", "audit_logs"]
    user.status = UserStatus.DELETED
    user.deleted_at = now
    user.deleted_by_id = user.id
    user.delete_reason = payload.reason
    user.email = f"deleted+{user.id}@quinta.invalid"
    user.email_normalized = normalize_email(user.email)
    user.phone_e164 = None
    user.full_name = None
    user.display_name = "Cuenta eliminada"
    user.avatar_url = None
    user.hashed_password = None
    user.pii_erased_at = now
    user.retention_until = now + timedelta(days=365 * settings.data_retention_invoice_years)

    docs = (
        await session.scalars(
            select(VerificationDocument).where(VerificationDocument.provider_id == user.id)
        )
    ).all()
    for document in docs:
        document.status = "EXPIRED"

    await audit.record(
        session,
        action=AuditAction.DATA_ERASED,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        reason=payload.reason,
        after={"retained": retained, "pii_erased_at": now.isoformat()},
        request=request,
    )
    await session.commit()
    return DeleteAccountResponse(
        deleted_at=now,
        pii_erased_at=now,
        retained_records=retained,
        purge_scheduled_for=user.retention_until,
    )


@router.get("/me/export", summary="Exportar mis datos (portabilidad)")
async def export_me(session: DbSession, user: CurrentUser, request: Request) -> Response:
    """Genera un JSON con los datos del usuario. Se encola y se entrega por URL firmada."""
    from app.tasks.jobs import build_data_export

    user.data_export_requested_at = datetime.now(UTC)
    await audit.record(
        session,
        action=AuditAction.DATA_EXPORTED,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        request=request,
    )
    await session.commit()
    build_data_export.delay(str(user.id))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get("/me/favorites", response_model=Paginated[VenueSummaryOut], summary="Mis favoritos")
async def list_favorites(
    session: DbSession,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,
) -> Paginated[VenueSummaryOut]:
    stmt = (
        select(Venue)
        .join(Favorite, Favorite.venue_id == Venue.id)
        .where(Favorite.user_id == user.id, Venue.deleted_at.is_(None))
        .order_by(Favorite.created_at.desc())
        .limit(limit)
    )
    venues = (await session.scalars(stmt)).all()
    items = [
        VenueSummaryOut(
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
        )
        for venue in venues
    ]
    return Paginated[VenueSummaryOut](
        items=items,
        meta={"limit": limit, "has_more": len(items) == limit, "next_cursor": None},
    )
