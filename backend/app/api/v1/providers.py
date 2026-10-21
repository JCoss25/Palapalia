"""Proveedores: solicitud, perfil público, verificación documental, dashboard y aprobación admin."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    MyProvider,
    ProviderUser,
    PublishedProvider,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.booking import Booking
from app.models.enums import (
    AuditAction,
    BookingState,
    ProviderStatus,
    Role,
    VerificationDocType,
    VerificationStatus,
)
from app.models.provider import ProviderProfile
from app.models.user import User, VerificationDocument
from app.models.venue import Venue
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.venue import (
    ProviderApplyRequest,
    ProviderApproveRequest,
    ProviderDashboardOut,
    ProviderOut,
    ProviderPatchRequest,
    ProviderPublicOut,
    VerificationDocumentCreate,
    VerificationDocumentOut,
    VerificationStatusOut,
)
from app.services import audit, storage

router = APIRouter()

REQUIRED_DOCS = (
    VerificationDocType.ID_OFFICIAL,
    VerificationDocType.PROOF_OF_ADDRESS,
    VerificationDocType.TAX_CERTIFICATE,
    VerificationDocType.PROPERTY_DEED,
)


@router.post(
    "/apply",
    response_model=ProviderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar ser anfitrión",
    responses={409: {"model": ErrorResponse, "description": "VERIFICATION_ALREADY_PENDING"}},
)
async def apply_provider(
    payload: ProviderApplyRequest, session: DbSession, user: CurrentUser, request: Request
) -> ProviderOut:
    if not payload.accepts_tos_provider:
        raise AppError(
            ErrorCode.TOS_NOT_ACCEPTED,
            message="Debes aceptar el contrato de anfitrión para continuar.",
        )

    profile = await session.scalar(
        select(ProviderProfile).where(ProviderProfile.user_id == user.id)
    )
    if profile is not None and profile.status == ProviderStatus.PENDING_REVIEW:
        raise AppError(ErrorCode.VERIFICATION_ALREADY_PENDING)

    if profile is None:
        profile = ProviderProfile(user_id=user.id, business_name=payload.business_name)
        session.add(profile)
    profile.business_name = payload.business_name
    profile.legal_name = payload.legal_name
    profile.tax_id = payload.tax_id
    profile.description = payload.description
    profile.phone_e164 = payload.phone
    profile.whatsapp_e164 = payload.whatsapp
    profile.website_url = str(payload.website_url) if payload.website_url else None
    profile.city = payload.city
    profile.state = payload.state
    profile.cancellation_policy = payload.cancellation_policy
    profile.status = ProviderStatus.PENDING_REVIEW.value
    profile.submitted_at = datetime.now(UTC)

    if user.role == Role.CLIENT:
        user.role = Role.PROVIDER

    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="provider_profile",
        entity_id=profile.id,
        actor_id=user.id,
        actor_role=user.role.value,
        after={"status": profile.status, "business_name": profile.business_name},
        request=request,
    )
    await session.commit()
    return ProviderOut.model_validate(profile)


@router.get("/me", response_model=ProviderOut, summary="Mi perfil de anfitrión")
async def get_my_profile(profile: MyProvider) -> ProviderOut:
    return ProviderOut.model_validate(profile)


@router.patch(
    "/me",
    response_model=ProviderOut,
    summary="Actualizar mi perfil de anfitrión",
    responses={409: {"model": ErrorResponse, "description": "PROVIDER_NOT_APPROVED"}},
)
async def patch_my_profile(
    payload: ProviderPatchRequest, session: DbSession, profile: MyProvider, user: CurrentUser
) -> ProviderOut:
    """`instant_book` sólo se puede activar con cuenta de cobros lista (edge case B12)."""
    data = payload.model_dump(exclude_unset=True)
    if data.get("instant_book_enabled") and not profile.payouts_enabled:
        raise AppError(
            ErrorCode.PROVIDER_PAYOUTS_NOT_ENABLED,
            message="Para activar reserva inmediata necesitas completar tu cuenta de pagos.",
        )
    for field, value in data.items():
        setattr(profile, field, str(value) if field == "website_url" and value else value)
    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="provider_profile",
        entity_id=profile.id,
        actor_id=user.id,
        after={k: v for k, v in data.items() if k != "tax_id"},
    )
    await session.commit()
    return ProviderOut.model_validate(profile)


@router.get(
    "/me/verification-status",
    response_model=VerificationStatusOut,
    summary="Estado de verificación y bloqueos",
)
async def verification_status(
    session: DbSession, profile: MyProvider
) -> VerificationStatusOut:
    docs = (
        await session.scalars(
            select(VerificationDocument).where(VerificationDocument.provider_id == profile.id)
        )
    ).all()
    submitted_types = {doc.doc_type for doc in docs if doc.status != VerificationStatus.REJECTED}
    missing = [doc for doc in REQUIRED_DOCS if doc not in submitted_types]

    blocking: list[str] = []
    if profile.status != ProviderStatus.APPROVED:
        blocking.append("Tu expediente está en revisión.")
    if not profile.payouts_enabled:
        blocking.append("Completa tu cuenta de pagos para recibir dinero en línea.")
    if missing:
        blocking.append(f"Faltan {len(missing)} documento(s) obligatorios.")

    return VerificationStatusOut(
        provider_status=profile.status,
        payouts_enabled=profile.payouts_enabled,
        offline_booking_only=profile.offline_booking_only,
        required_documents=list(REQUIRED_DOCS),
        submitted_documents=[VerificationDocumentOut.model_validate(doc) for doc in docs],
        missing_documents=missing,
        blocking_reasons=blocking,
        can_publish=profile.status == ProviderStatus.APPROVED,
        can_receive_payments=profile.payouts_enabled,
    )


@router.post(
    "/me/verification-documents",
    response_model=VerificationDocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar documento de verificación (tras subirlo con URL firmada)",
    responses={422: {"model": ErrorResponse, "description": "VERIFICATION_DOCUMENT_INVALID"}},
)
async def add_verification_document(
    payload: VerificationDocumentCreate,
    session: DbSession,
    profile: MyProvider,
    user: CurrentUser,
) -> VerificationDocumentOut:
    """El archivo ya se subió con URL pre-firmada; aquí se valida tipo, hash y duplicados (edge case C15)."""
    storage.validate_document(payload.mime_type, size_bytes=1)

    duplicate_users = await session.scalar(
        select(func.count(func.distinct(VerificationDocument.provider_id))).where(
            VerificationDocument.file_sha256 == payload.file_sha256
        )
    )
    document = VerificationDocument(
        provider_id=profile.id,
        doc_type=payload.doc_type.value,
        file_key=payload.file_key,
        file_sha256=payload.file_sha256,
        mime_type=payload.mime_type,
        expires_on=payload.expires_on,
        status=VerificationStatus.PENDING.value,
        fraud_signals={"duplicate_users": int(duplicate_users or 0)},
    )
    session.add(document)
    if (duplicate_users or 0) > 0:
        profile.risk_score = float(profile.risk_score) + 20
        profile.risk_flags = {
            **profile.risk_flags,
            "duplicate_document": True,
        }
    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="verification_document",
        entity_id=document.id,
        actor_id=user.id,
        after={"doc_type": payload.doc_type.value, "duplicate_users": int(duplicate_users or 0)},
    )
    await session.commit()
    return VerificationDocumentOut.model_validate(document)


@router.get(
    "/me/dashboard",
    response_model=ProviderDashboardOut,
    summary="Métricas del panel del anfitrión",
)
async def dashboard(
    session: DbSession,
    profile: PublishedProvider,
    period: str = Query("30d", pattern="^(30d|90d|mtd|ytd)$"),
) -> ProviderDashboardOut:
    """Ocupación real: días vendidos / días publicables del periodo. Sin gráficas decorativas."""
    now = datetime.now(UTC)
    spans = {"30d": 30, "90d": 90, "mtd": now.day, "ytd": now.timetuple().tm_yday}
    days = spans[period]
    since = now - timedelta(days=days)

    venue_ids = [
        row
        for row in await session.scalars(select(Venue.id).where(Venue.provider_id == profile.id))
    ]

    bookings = (
        await session.scalars(
            select(Booking).where(
                Booking.provider_id == profile.id,
                Booking.start_at >= since,
            )
        )
    ).all()

    confirmed = [b for b in bookings if b.state == BookingState.CONFIRMED.value]
    completed = [b for b in bookings if b.state == BookingState.COMPLETED.value]
    revenue = sum(b.paid_cents for b in bookings if b.state in {BookingState.CONFIRMED.value, BookingState.COMPLETED.value})
    sold_days = len({b.start_at.date() for b in bookings if b.state != BookingState.CANCELLED_BY_CLIENT.value})
    publishable_days = max(len(venue_ids), 1) * days

    payout = await session.scalar(
        select(func.coalesce(func.sum(Booking.paid_cents - Booking.security_deposit_cents), 0)).where(
            Booking.provider_id == profile.id,
            Booking.state == BookingState.COMPLETED.value,
            Booking.start_at >= since,
        )
    )

    pending_tasks: list[dict] = []
    if not profile.payouts_enabled:
        pending_tasks.append(
            {"kind": "payouts", "label": "Completa tu cuenta de pagos", "severity": "blocking"}
        )
    if profile.status != ProviderStatus.APPROVED:
        pending_tasks.append({"kind": "verification", "label": "Expediente en revisión", "severity": "info"})
    unanswered = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.provider_id == profile.id,
            Booking.state == BookingState.HOLD.value,
        )
    )
    if unanswered:
        pending_tasks.append(
            {"kind": "requests", "label": f"{unanswered} solicitud(es) por responder", "severity": "action"}
        )

    return ProviderDashboardOut(
        provider_id=profile.id,
        period=period,
        occupancy_rate=round(min(sold_days / publishable_days, 1.0), 3),
        revenue_cents=revenue,
        revenue_currency="MXN",
        confirmed_bookings=len(confirmed) + len(completed),
        pending_requests=int(unanswered or 0),
        upcoming_events=len(
            [b for b in confirmed if b.start_at >= now]
        ),
        cancelled_by_client=len([b for b in bookings if b.state == BookingState.CANCELLED_BY_CLIENT.value]),
        cancelled_by_provider=len([b for b in bookings if b.state == BookingState.CANCELLED_BY_PROVIDER.value]),
        avg_ticket_cents=int(revenue / max(len(confirmed) + len(completed), 1)),
        rating_avg=float(profile.rating_avg) if profile.rating_avg else None,
        response_time_minutes=profile.response_time_minutes,
        next_payout_cents=int(payout or 0),
        pending_tasks=pending_tasks,
    )


@router.get(
    "/{provider_id}",
    response_model=ProviderPublicOut,
    summary="Perfil público del anfitrión",
    responses={404: {"model": ErrorResponse, "description": "NOT_FOUND"}},
)
async def get_public_provider(session: DbSession, provider_id: uuid.UUID) -> ProviderPublicOut:
    profile = await session.scalar(
        select(ProviderProfile).where(
            ProviderProfile.id == provider_id, ProviderProfile.deleted_at.is_(None)
        )
    )
    if profile is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"provider_id": str(provider_id)})
    count = await session.scalar(
        select(func.count())
        .select_from(Venue)
        .where(Venue.provider_id == provider_id, Venue.deleted_at.is_(None))
    )
    public = ProviderPublicOut.model_validate(profile)
    return public.model_copy(update={"venues_count": int(count or 0)})


@router.get(
    "/{provider_id}/rating-summary",
    summary="Resumen de calificaciones del anfitrión",
)
async def provider_rating_summary(session: DbSession, provider_id: uuid.UUID) -> dict:
    from app.models.review import Review

    rows = (
        await session.execute(
            select(Review.rating, func.count())
            .where(Review.provider_id == provider_id, Review.status == "PUBLISHED")
            .group_by(Review.rating)
        )
    ).all()
    distribution = {str(star): 0 for star in range(1, 6)}
    total = 0
    weighted = 0
    for rating, count in rows:
        distribution[str(rating)] = int(count)
        total += int(count)
        weighted += int(rating) * int(count)
    return {
        "provider_id": str(provider_id),
        "rating_avg": round(weighted / total, 2) if total else None,
        "rating_count": total,
        "distribution": distribution,
    }


# ── Admin ──────────────────────────────────────────────────────────────────────


admin_router = APIRouter()


@admin_router.get(
    "",
    response_model=Paginated[ProviderOut],
    summary="Listar proveedores por estado (admin)",
)
async def admin_list_providers(
    session: DbSession,
    admin: AdminUser,
    status_filter: ProviderStatus | None = Query(default=None, alias="status"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> Paginated[ProviderOut]:
    stmt = select(ProviderProfile).where(ProviderProfile.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(ProviderProfile.status == status_filter.value)
    profiles = (
        await session.scalars(stmt.order_by(ProviderProfile.submitted_at.desc()).limit(limit).offset(offset))
    ).all()
    return Paginated[ProviderOut](
        items=[ProviderOut.model_validate(profile) for profile in profiles],
        meta={"limit": limit, "has_more": len(profiles) == limit, "next_cursor": None},
    )


@admin_router.post(
    "/{provider_id}/approve",
    response_model=ProviderOut,
    summary="Aprobar o rechazar proveedor (admin)",
)
async def admin_approve_provider(
    provider_id: uuid.UUID,
    payload: ProviderApproveRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> ProviderOut:
    """Aprobar habilita publicar; `payouts_enabled` es independiente (depende de Stripe/KYC)."""
    profile = await session.get(ProviderProfile, provider_id)
    if profile is None or profile.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, details={"provider_id": str(provider_id)})

    before = {"status": profile.status, "payouts_enabled": profile.payouts_enabled}
    if payload.approve:
        profile.status = ProviderStatus.APPROVED.value
        profile.approved_at = datetime.now(UTC)
        profile.approved_by_id = admin.id
        profile.status_reason = None
        if payload.payouts_enabled is not None:
            profile.payouts_enabled = payload.payouts_enabled
        action = AuditAction.PROVIDER_APPROVED
    else:
        profile.status = ProviderStatus.REJECTED.value
        profile.status_reason = payload.reason or "No cumple los requisitos de verificación"
        action = AuditAction.PROVIDER_REJECTED

    await audit.record(
        session,
        action=action,
        entity="provider_profile",
        entity_id=profile.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        before=before,
        after={"status": profile.status, "reason": payload.reason},
        request=request,
    )
    from app.tasks.notifications import notify_provider_verification

    notify_provider_verification.delay(str(profile.id), payload.approve, payload.reason)
    await session.commit()
    return ProviderOut.model_validate(profile)
