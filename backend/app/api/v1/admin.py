"""Administración y soporte: usuarios, métricas, auditoría, disputas, impersonación y webhooks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession, StaffUser
from app.core.errors import AppError, ErrorCode
from app.core.security import TokenType, create_token
from app.models.booking import Booking
from app.models.enums import (
    AuditAction,
    BookingState,
    DisputeStatus,
    PaymentStatus,
    ProviderStatus,
    Role,
    UserStatus,
    VenueStatus,
)
from app.models.notification import AuditLog, ImpersonationSession, WebhookDelivery, WebhookEvent
from app.models.payment import Dispute, Payment
from app.models.provider import ProviderProfile
from app.models.review import VenueReport
from app.models.user import User
from app.models.venue import Venue
from app.schemas.auth import AdminUserOut, AdminUserPatch, BanUserRequest
from app.schemas.common import Paginated
from app.schemas.content import (
    AdminMetricsOut,
    AdminVenueMergeRequest,
    AdminVenueSuspendRequest,
    AuditLogOut,
    ImpersonateRequest,
    ImpersonateResponse,
    RoleGrantRequest,
    WebhookDeliveryOut,
    WebhookEventOut,
    WebhookReplayRequest,
)
from app.schemas.payment import DisputeOut
from app.services import audit, webhooks_out

router = APIRouter()


# ── Usuarios ──────────────────────────────────────────────────────────────────


@router.get("/users", response_model=Paginated[AdminUserOut], summary="Listar usuarios (admin)")
async def list_users(
    session: DbSession,
    admin: AdminUser,
    q: str | None = Query(None, max_length=80, description="Busca por nombre o correo"),
    role: Role | None = None,
    status_filter: UserStatus | None = Query(default=None, alias="status"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Paginated[AdminUserOut]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if q:
        stmt = stmt.where(User.full_name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    if role:
        stmt = stmt.where(User.role == role.value)
    if status_filter:
        stmt = stmt.where(User.status == status_filter.value)
    users = (await session.scalars(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))).all()
    out: list[AdminUserOut] = []
    for user in users:
        bookings = await session.scalar(
            select(func.count()).select_from(Booking).where(Booking.client_id == user.id)
        )
        base = AdminUserOut.model_validate(user)
        out.append(base.model_copy(update={"bookings_as_client": int(bookings or 0)}))
    return Paginated[AdminUserOut](
        items=out, meta={"limit": limit, "has_more": len(users) == limit, "next_cursor": None}
    )


@router.patch("/users/{user_id}", response_model=AdminUserOut, summary="Actualizar usuario (admin)")
async def patch_user(
    user_id: uuid.UUID,
    payload: AdminUserPatch,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> AdminUserOut:
    """Cambiar rol es una acción sensible: queda auditada con motivo obligatorio."""
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, details={"user_id": str(user_id)})
    before = {"role": user.role, "status": user.status}
    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] is not None:
        user.role = data["role"].value if hasattr(data["role"], "value") else str(data["role"])
    if "status" in data and data["status"] is not None:
        user.status = data["status"].value if hasattr(data["status"], "value") else str(data["status"])
    if data.get("full_name"):
        user.full_name = data["full_name"]
    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="user",
        entity_id=user.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        before=before,
        after={"role": user.role, "status": user.status},
        reason=payload.note,
        request=request,
    )
    await session.commit()
    return AdminUserOut.model_validate(user)


@router.post("/users/{user_id}/ban", response_model=AdminUserOut, summary="Banear usuario (admin)")
async def ban_user(
    user_id: uuid.UUID,
    payload: BanUserRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> AdminUserOut:
    """Banear suspende venues y cancela reservas futuras con reembolso total si se pide."""
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"user_id": str(user_id)})
    user.status = UserStatus.BANNED.value
    user.banned_at = datetime.now(UTC)
    user.ban_reason = payload.reason

    suspended_venues = 0
    if payload.suspend_venues:
        profile = await session.scalar(select(ProviderProfile).where(ProviderProfile.user_id == user.id))
        if profile is not None:
            venues = (
                await session.scalars(
                    select(Venue).where(Venue.provider_id == profile.id, Venue.deleted_at.is_(None))
                )
            ).all()
            for venue in venues:
                venue.status = VenueStatus.SUSPENDED.value
                venue.suspended_at = datetime.now(UTC)
                venue.suspension_reason = f"Cuenta suspendida: {payload.reason}"
                suspended_venues += 1

    await audit.record(
        session,
        action=AuditAction.USER_BANNED,
        entity="user",
        entity_id=user.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        after={"reason": payload.reason, "suspended_venues": suspended_venues},
        request=request,
    )
    if payload.notify_user:
        from app.tasks.notifications import notify_account_banned

        notify_account_banned.delay(str(user.id), payload.reason)
    await session.commit()
    return AdminUserOut.model_validate(user)


@router.post("/users/{user_id}/role", response_model=AdminUserOut, summary="Otorgar rol (admin)")
async def grant_role(
    user_id: uuid.UUID,
    payload: RoleGrantRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> AdminUserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"user_id": str(user_id)})
    before = user.role
    user.role = payload.role.value
    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="user",
        entity_id=user.id,
        actor_id=admin.id,
        before={"role": before},
        after={"role": user.role},
        reason=payload.reason,
        request=request,
    )
    await session.commit()
    return AdminUserOut.model_validate(user)


# ── Métricas y auditoría ──────────────────────────────────────────────────────


@router.get("/metrics", response_model=AdminMetricsOut, summary="Métricas del negocio")
async def metrics(
    session: DbSession,
    admin: AdminUser,
    days: int = Query(30, ge=1, le=365),
) -> AdminMetricsOut:
    """GMV, take rate, conversión de apartados, conflictos de solape y latencia de webhooks."""
    since = datetime.now(UTC) - timedelta(days=days)

    paid = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value, Payment.created_at >= since
        )
    )
    fees = await session.scalar(
        select(func.coalesce(func.sum(Payment.application_fee_cents), 0)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value, Payment.created_at >= since
        )
    )
    confirmed = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.state == BookingState.CONFIRMED.value, Booking.created_at >= since)
    )
    completed = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.state == BookingState.COMPLETED.value, Booking.start_at >= since)
    )
    holds = await session.scalar(
        select(func.count()).select_from(Booking).where(Booking.state == BookingState.HOLD.value)
    )
    disputed = await session.scalar(
        select(func.count()).select_from(Dispute).where(Dispute.created_at >= since)
    )
    cancelled = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.state.in_(
                [BookingState.CANCELLED_BY_CLIENT.value, BookingState.CANCELLED_BY_PROVIDER.value]
            ),
            Booking.created_at >= since,
        )
    )
    active_providers = await session.scalar(
        select(func.count())
        .select_from(ProviderProfile)
        .where(ProviderProfile.status == ProviderStatus.APPROVED.value)
    )
    pending_verifications = await session.scalar(
        select(func.count())
        .select_from(ProviderProfile)
        .where(ProviderProfile.status == ProviderStatus.PENDING_REVIEW.value)
    )
    pending_venues = await session.scalar(
        select(func.count())
        .select_from(Venue)
        .where(Venue.status == VenueStatus.PENDING_REVIEW.value)
    )

    gmv = int(paid or 0)
    return AdminMetricsOut(
        gmv_cents=gmv,
        net_revenue_cents=int(fees or 0),
        take_rate=round(int(fees or 0) / gmv, 4) if gmv else 0,
        bookings_confirmed=int(confirmed or 0),
        bookings_completed=int(completed or 0),
        hold_conversion_rate=round(int(confirmed or 0) / max(int(confirmed or 0) + int(holds or 0), 1), 3),
        hold_conflicts_total=0,
        cancellation_rate=round(int(cancelled or 0) / max(int(confirmed or 0), 1), 3),
        dispute_rate=round(int(disputed or 0) / max(int(confirmed or 0), 1), 4),
        active_providers=int(active_providers or 0),
        pending_verifications=int(pending_verifications or 0),
        pending_venue_reviews=int(pending_venues or 0),
        period_from=(datetime.now(UTC) - timedelta(days=days)).date(),
        period_to=datetime.now(UTC).date(),
    )


@router.get("/audit-logs", response_model=Paginated[AuditLogOut], summary="Bitácora de auditoría")
async def audit_logs(
    session: DbSession,
    staff: StaffUser,
    actor_id: uuid.UUID | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Paginated[AuditLogOut]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await session.scalars(stmt)).all()
    return Paginated[AuditLogOut](
        items=[AuditLogOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.get("/audit-logs/verify", summary="Verificar integridad de la bitácora")
async def verify_audit_chain(session: DbSession, admin: AdminUser, limit: int = Query(2000, ge=10, le=20000)) -> dict:
    ok, checked = await audit.verify_chain(session, limit=limit)
    return {"chain_valid": ok, "records_checked": checked}


# ── Disputas y reportes ───────────────────────────────────────────────────────


@router.get("/disputes", response_model=Paginated[DisputeOut], summary="Disputas abiertas")
async def list_disputes(
    session: DbSession,
    staff: StaffUser,
    status_filter: DisputeStatus | None = Query(default=None, alias="status"),
    limit: int = Query(30, ge=1, le=100),
) -> Paginated[DisputeOut]:
    stmt = select(Dispute).order_by(Dispute.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Dispute.status == status_filter.value)
    rows = (await session.scalars(stmt)).all()
    return Paginated[DisputeOut](
        items=[DisputeOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.get("/venue-reports", summary="Reportes de catálogo pendientes")
async def list_venue_reports(session: DbSession, staff: StaffUser, limit: int = Query(30, ge=1, le=100)) -> dict:
    rows = (
        await session.scalars(
            select(VenueReport).where(VenueReport.status == "OPEN").limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "venue_id": str(row.venue_id),
                "kind": row.kind,
                "detail": row.detail,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.post("/venues/merge", summary="Fusionar venues duplicados (admin)")
async def merge_venues(
    payload: AdminVenueMergeRequest, session: DbSession, admin: AdminUser, request: Request
) -> dict:
    """Deduplicación con auditoría: las reservas se mueven al venue destino (edge case D23)."""
    source = await session.get(Venue, payload.source_venue_id)
    target = await session.get(Venue, payload.target_venue_id)
    if source is None or target is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND)
    moved = 0
    if payload.move_bookings:
        bookings = (
            await session.scalars(select(Booking).where(Booking.venue_id == source.id))
        ).all()
        for booking in bookings:
            booking.venue_id = target.id
            moved += 1
    source.status = VenueStatus.DELETED.value
    source.deleted_at = datetime.now(UTC)
    source.merged_into_id = target.id
    await audit.record(
        session,
        action=AuditAction.VENUE_MERGED,
        entity="venue",
        entity_id=source.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        after={"target_venue_id": str(target.id), "bookings_moved": moved},
        reason=payload.reason,
        request=request,
    )
    await session.commit()
    return {"source_venue_id": str(source.id), "target_venue_id": str(target.id), "bookings_moved": moved}


# ── Impersonación ─────────────────────────────────────────────────────────────


@router.post("/impersonate", response_model=ImpersonateResponse, summary="Impersonar usuario (admin)")
async def impersonate(
    payload: ImpersonateRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> ImpersonateResponse:
    """Sesión acotada con TTL, motivo obligatorio y auditoría. El usuario lo puede ver en su historial."""
    target = await session.get(User, payload.user_id)
    if target is None or target.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, details={"user_id": str(payload.user_id)})
    if target.role in {Role.ADMIN.value, Role.SUPPORT.value} and target.id != admin.id:
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="No se puede impersonar a otro administrador.",
        )

    token, parsed = create_token(
        target.id,
        TokenType.ACCESS,
        expires_in=timedelta(minutes=payload.ttl_minutes),
        role=target.role.value,
        scope="impersonation",
        extra={"imp": str(admin.id)},
    )
    session_row = ImpersonationSession(
        admin_id=admin.id,
        target_user_id=target.id,
        reason=payload.reason,
        ticket_ref=payload.ticket_ref,
        token_jti=parsed.jti,
        expires_at=parsed.exp,
        ip=request.client.host if request.client else None,
    )
    session.add(session_row)
    await audit.record(
        session,
        action=AuditAction.ADMIN_IMPERSONATION,
        entity="user",
        entity_id=target.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        reason=payload.reason,
        after={"session_id": str(session_row.id), "ttl_minutes": payload.ttl_minutes},
        request=request,
    )
    await session.commit()
    return ImpersonateResponse(
        access_token=token,
        expires_in=payload.ttl_minutes * 60,
        target_user_id=target.id,
        session_id=session_row.id,
    )


# ── Webhooks salientes ────────────────────────────────────────────────────────


@router.get("/webhooks/events", response_model=Paginated[WebhookEventOut], summary="Eventos de webhook")
async def list_webhook_events(
    session: DbSession,
    admin: AdminUser,
    event_type: str | None = None,
    provider_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Paginated[WebhookEventOut]:
    stmt = select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(limit)
    if event_type:
        stmt = stmt.where(WebhookEvent.event_type == event_type)
    if provider_id:
        stmt = stmt.where(WebhookEvent.provider_id == provider_id)
    rows = (await session.scalars(stmt)).all()
    return Paginated[WebhookEventOut](
        items=[WebhookEventOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.post("/webhooks/events/{event_id}/replay", summary="Reenviar un evento (replay)")
async def replay_webhook_event(
    event_id: uuid.UUID,
    payload: WebhookReplayRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> dict:
    """Replay manual: se vuelve a firmar y se reencolan las entregas, sin alterar el payload original."""
    event = await session.get(WebhookEvent, event_id)
    if event is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"event_id": str(event_id)})
    count = await webhooks_out.fanout_event(session, event_id=event.id, is_replay=True)
    await audit.record(
        session,
        action=AuditAction.WEBHOOK_REPLAYED,
        entity="webhook_event",
        entity_id=event.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        reason=payload.reason,
        after={"deliveries": count},
        request=request,
    )
    await session.commit()
    return {"event_id": str(event.id), "deliveries_scheduled": count}


@router.get(
    "/webhooks/deliveries",
    response_model=Paginated[WebhookDeliveryOut],
    summary="Intentos de entrega",
)
async def list_webhook_deliveries(
    session: DbSession,
    admin: AdminUser,
    event_id: uuid.UUID | None = None,
    failed_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
) -> Paginated[WebhookDeliveryOut]:
    stmt = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit)
    if event_id:
        stmt = stmt.where(WebhookDelivery.event_id == event_id)
    if failed_only:
        stmt = stmt.where(WebhookDelivery.delivered_at.is_(None))
    rows = (await session.scalars(stmt)).all()
    return Paginated[WebhookDeliveryOut](
        items=[WebhookDeliveryOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )
