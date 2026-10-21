"""Reservas: apartado, confirmación, consulta, cancelación, reprogramación y operación del día.

Todas las escrituras críticas exigen `Idempotency-Key` y devuelven la misma respuesta al reintentar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    IdempotencyKey,
    ProviderUser,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import create_qr_checkin_token
from app.models.booking import Booking, BookingHold, BookingStatusHistory, Contract
from app.models.enums import AuditAction, BookingState, HoldStatus, Role
from app.models.payment import Dispute
from app.schemas.booking import (
    AvailabilityCheckOut,
    BookingCreateRequest,
    BookingDetailOut,
    BookingSummaryOut,
    CancelBookingRequest,
    CancelBookingResponse,
    CheckInRequest,
    CheckOutRequest,
    CompleteRequest,
    DisputeCreateRequest,
    DisputeResolveRequest,
    HoldCreateRequest,
    HoldCreateResponse,
    HoldOut,
    NoShowRequest,
    PaymentSummaryOut,
    RescheduleRequest,
    StatusHistoryOut,
)
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.payment import RefundPolicyPreviewOut
from app.services import audit, availability, storage
from app.services import bookings as svc
from app.services import payments as payment_svc
from app.services.idempotency import run_idempotent
from app.services.state_machine import ADMIN, CLIENT, PROVIDER, actor_from_role, allowed_transitions

router = APIRouter()


# ── Helpers de presentación ───────────────────────────────────────────────────


def _summary(booking: Booking, *, role: str = Role.CLIENT.value) -> BookingSummaryOut:
    now = datetime.now(UTC)
    state = BookingState(booking.state)
    can_cancel = state in {BookingState.HOLD, BookingState.PENDING_PAYMENT, BookingState.CONFIRMED} and (
        booking.start_at > now
    )
    can_review = state == BookingState.COMPLETED
    can_pay = booking.balance_cents > 0 and state in {
        BookingState.HOLD,
        BookingState.PENDING_PAYMENT,
        BookingState.CONFIRMED,
    }
    next_action = "none"
    if state == BookingState.HOLD:
        next_action = "pay_deposit"
    elif state == BookingState.CONFIRMED and booking.balance_cents > 0:
        next_action = "pay_balance"
    elif state == BookingState.CONFIRMED and booking.check_in_at is None and booking.start_at <= now:
        next_action = "check_in"
    elif can_review:
        next_action = "review"

    return BookingSummaryOut(
        id=booking.id,
        folio=booking.folio,
        state=state,
        venue_id=booking.venue_id,
        venue_name=booking.venue.name if booking.venue else None,
        venue_cover_url=booking.venue.cover_url if booking.venue else None,
        city=booking.venue.city if booking.venue else None,
        start_at=booking.start_at,
        end_at=booking.end_at,
        venue_timezone=booking.venue_timezone,
        guests=booking.guests,
        total_cents=booking.total_cents,
        paid_cents=booking.paid_cents,
        balance_cents=booking.balance_cents,
        currency=booking.currency,
        created_at=booking.created_at,
        can_cancel=can_cancel,
        can_reschedule=state == BookingState.CONFIRMED and booking.reschedule_count < 2,
        can_review=can_review,
        can_pay_balance=can_pay,
        next_action=next_action,
        provider_name=booking.venue.provider.business_name if booking.venue and booking.venue.provider else None,
    )


async def _detail(session: DbSession, booking: Booking, *, viewer_id: uuid.UUID, role: str) -> BookingDetailOut:
    history = (
        await session.scalars(
            select(BookingStatusHistory)
            .where(BookingStatusHistory.booking_id == booking.id)
            .order_by(BookingStatusHistory.created_at.desc())
            .limit(30)
        )
    ).all()
    dispute = await session.scalar(
        select(Dispute).where(
            Dispute.booking_id == booking.id, Dispute.status.in_(["OPEN", "UNDER_REVIEW"])
        )
    )
    qr_token = None
    if booking.client_id == viewer_id and BookingState(booking.state) == BookingState.CONFIRMED:
        qr_token = create_qr_checkin_token(booking.id)

    base = _summary(booking, role=role)
    return BookingDetailOut(
        **base.model_dump(),
        previous_state=BookingState(booking.previous_state) if booking.previous_state else None,
        slot_kind=booking.slot_kind,
        event_type_slug=booking.event_type_slug,
        special_requests=booking.special_requests,
        blocked_from=booking.blocked_from,
        blocked_to=booking.blocked_to,
        subtotal_cents=booking.subtotal_cents,
        extras_cents=booking.extras_cents,
        cleaning_cents=booking.cleaning_cents,
        discount_cents=booking.discount_cents,
        service_fee_cents=booking.service_fee_cents,
        tax_cents=booking.tax_cents,
        security_deposit_cents=booking.security_deposit_cents,
        deposit_cents=booking.deposit_cents,
        refunded_cents=booking.refunded_cents,
        coupon_code=booking.coupon_code,
        quote_snapshot=booking.quote_snapshot,
        rules_snapshot=booking.rules_snapshot,
        check_in_at=booking.check_in_at,
        check_out_at=booking.check_out_at,
        qr_token=qr_token,
        cancelled_at=booking.cancelled_at,
        cancellation_reason=booking.cancellation_reason,
        refund_tier_percent=booking.refund_tier_percent,
        contract_url=(
            storage.presign_download(booking.contract_key) if booking.contract_key else None
        ),
        payments=[PaymentSummaryOut.model_validate(item) for item in booking.payments],
        status_history=[StatusHistoryOut.model_validate(item) for item in history],
        dispute_id=dispute.id if dispute else None,
    )


async def _get_booking_for_actor(
    session: DbSession, booking_id: uuid.UUID, user
) -> tuple[Booking, str]:
    """Carga la reserva y determina el actor real. El ID de la ruta **no** autoriza."""
    booking = await session.scalar(select(Booking).where(Booking.id == booking_id))
    if booking is None or booking.deleted_at is not None:
        raise AppError(ErrorCode.BOOKING_NOT_FOUND, details={"booking_id": str(booking_id)})

    is_owner = booking.client_id == user.id
    is_provider = False
    if user.role == Role.PROVIDER.value:
        from app.models.provider import ProviderProfile

        profile_id = await session.scalar(
            select(ProviderProfile.id).where(ProviderProfile.user_id == user.id)
        )
        is_provider = profile_id is not None and profile_id == booking.provider_id
    if not (is_owner or is_provider or user.role in {Role.ADMIN.value, Role.SUPPORT.value}):
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="Esa reserva no es tuya.",
            details={"booking_id": str(booking_id)},
        )
    actor = actor_from_role(user.role, is_owner=is_owner, is_provider=is_provider)
    return booking, actor


# ── Apartado ──────────────────────────────────────────────────────────────────


@router.post(
    "/hold",
    response_model=HoldCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Apartar un horario (hold con TTL)",
    responses={
        400: {"model": ErrorResponse, "description": "IDEMPOTENCY_KEY_REQUIRED"},
        409: {
            "model": ErrorResponse,
            "description": "VENUE_NOT_AVAILABLE · IDEMPOTENCY_KEY_REUSED · QUOTE_EXPIRED",
        },
        429: {"model": ErrorResponse, "description": "HOLD_LIMIT_REACHED"},
    },
)
async def create_hold(
    payload: HoldCreateRequest,
    session: DbSession,
    user: CurrentUser,
    idempotency_key: IdempotencyKey,
    request: Request,
) -> HoldCreateResponse:
    """Reserva atómica del slot. Dos clientes al mismo tiempo ⇒ uno gana, el otro recibe 409."""
    endpoint = "POST /bookings/hold"

    async def operation() -> tuple[dict, str, str]:
        hold, quote = await svc.create_hold(session, client_id=user.id, payload=payload, endpoint=endpoint)
        body = HoldCreateResponse(
            id=hold.id,
            folio=hold.folio,
            venue_id=hold.venue_id,
            status=HoldStatus(hold.status),
            start_at=hold.start_at,
            end_at=hold.end_at,
            venue_timezone=hold.venue_timezone,
            guests=hold.guests,
            slot_kind=hold.slot_kind,
            total_cents=hold.total_cents,
            deposit_cents=hold.deposit_cents,
            currency=hold.currency,
            expires_at=hold.expires_at,
            seconds_remaining=int((hold.expires_at - datetime.now(UTC)).total_seconds()),
            can_extend=hold.extended_count < 1,
            quote_snapshot=hold.quote_snapshot,
            venue_name=hold.venue.name if hold.venue else None,
            venue_cover_url=hold.venue.cover_url if hold.venue else None,
            quote=quote,
        ).model_dump(mode="json")
        return body, "booking_hold", str(hold.id)

    body, replay = await run_idempotent(
        session,
        user_id=user.id,
        endpoint=endpoint,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        status_code=201,
        operation=operation,
    )
    return HoldCreateResponse(**{**body, "idempotent_replay": replay})


@router.get(
    "/holds/{hold_id}",
    response_model=HoldOut,
    summary="Consultar apartado",
    responses={410: {"model": ErrorResponse, "description": "HOLD_EXPIRED"}},
)
async def get_hold(hold_id: uuid.UUID, session: DbSession, user: CurrentUser) -> HoldOut:
    hold = await session.scalar(select(BookingHold).where(BookingHold.id == hold_id))
    if hold is None or hold.deleted_at is not None:
        raise AppError(ErrorCode.HOLD_NOT_FOUND, details={"hold_id": str(hold_id)})
    if hold.client_id != user.id and user.role not in {Role.ADMIN.value, Role.SUPPORT.value}:
        raise AppError(ErrorCode.FORBIDDEN)

    remaining = int((hold.expires_at - datetime.now(UTC)).total_seconds())
    if hold.status == HoldStatus.ACTIVE.value and remaining <= 0:
        # El estado real lo actualiza el job; aquí se reporta con precisión al cliente
        raise AppError(
            ErrorCode.HOLD_EXPIRED,
            message="Tu apartado venció y el horario se liberó. Vuelve a apartarlo.",
            details={"hold_id": str(hold.id), "folio": hold.folio},
        )
    return HoldOut(
        id=hold.id,
        folio=hold.folio,
        venue_id=hold.venue_id,
        status=HoldStatus(hold.status),
        start_at=hold.start_at,
        end_at=hold.end_at,
        venue_timezone=hold.venue_timezone,
        guests=hold.guests,
        slot_kind=hold.slot_kind,
        total_cents=hold.total_cents,
        deposit_cents=hold.deposit_cents,
        currency=hold.currency,
        expires_at=hold.expires_at,
        seconds_remaining=max(remaining, 0),
        can_extend=hold.extended_count < 1 and hold.status == HoldStatus.ACTIVE.value,
        quote_snapshot=hold.quote_snapshot,
        venue_name=hold.venue.name if hold.venue else None,
        venue_cover_url=hold.venue.cover_url if hold.venue else None,
    )


@router.post(
    "/holds/{hold_id}/extend",
    response_model=HoldOut,
    summary="Extender apartado (una sola vez)",
    responses={410: {"model": ErrorResponse, "description": "HOLD_EXPIRED"}},
)
async def extend_hold(hold_id: uuid.UUID, session: DbSession, user: CurrentUser) -> HoldOut:
    hold = await svc.extend_hold(session, hold_id=hold_id, client_id=user.id)
    return await get_hold(hold_id, session, user)


@router.post(
    "/holds/{hold_id}/release",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Liberar apartado (el cliente se arrepiente)",
)
async def release_hold(hold_id: uuid.UUID, session: DbSession, user: CurrentUser) -> Response:
    hold = await session.scalar(select(BookingHold).where(BookingHold.id == hold_id))
    if hold is None:
        raise AppError(ErrorCode.HOLD_NOT_FOUND)
    if hold.client_id != user.id:
        raise AppError(ErrorCode.FORBIDDEN)
    if hold.status == HoldStatus.ACTIVE.value:
        await svc.release_hold(session, hold, reason="client_released")
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Confirmación ──────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=BookingDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Confirmar reserva desde un apartado",
    responses={
        410: {"model": ErrorResponse, "description": "HOLD_EXPIRED"},
        409: {"model": ErrorResponse, "description": "INVALID_STATE_TRANSITION · PAYMENT_DUPLICATED"},
    },
)
async def create_booking(
    payload: BookingCreateRequest,
    session: DbSession,
    user: CurrentUser,
    idempotency_key: IdempotencyKey,
) -> BookingDetailOut:
    """Convierte el hold en booking. Con pago en línea, el webhook confirma; aquí se registra el intent."""
    if not payload.accept_contract:
        raise AppError(
            ErrorCode.TOS_NOT_ACCEPTED,
            message="Debes aceptar el contrato de apartado para continuar.",
        )
    endpoint = "POST /bookings"

    async def operation() -> tuple[dict, str, str]:
        hold = await session.scalar(select(BookingHold).where(BookingHold.id == payload.hold_id))
        if hold is None:
            raise AppError(ErrorCode.HOLD_NOT_FOUND, details={"hold_id": str(payload.hold_id)})
        if hold.client_id != user.id:
            raise AppError(ErrorCode.FORBIDDEN)
        if hold.status == HoldStatus.ACTIVE.value and hold.expires_at <= datetime.now(UTC):
            raise AppError(ErrorCode.HOLD_EXPIRED, details={"expired_at": hold.expires_at.isoformat()})

        venue = await svc.get_venue_or_404(session, hold.venue_id)
        provider_payouts = bool(venue.provider and venue.provider.payouts_enabled)
        offline = payload.payment_method == "cash_at_venue"

        if payload.payment_method == "card" and not provider_payouts:
            raise AppError(
                ErrorCode.PROVIDER_PAYOUTS_NOT_ENABLED,
                message="El anfitrión no puede cobrar en línea todavía. Elige pagar directo con él.",
                details={"alternative_methods": ["cash_at_venue", "spei"]},
            )

        target_state = (
            BookingState.CONFIRMED
            if (offline or venue.instant_book or payload.payment_method == "spei")
            else BookingState.PENDING_PAYMENT
        )
        booking = await svc.confirm_from_hold(
            session,
            hold_id=hold.id,
            client_id=user.id,
            actor=CLIENT,
            reason=f"pago:{payload.payment_method}",
            state=target_state,
        )

        if payload.payment_method == "card":
            await payment_svc.create_payment_intent(
                session,
                booking=booking,
                client_id=user.id,
                kind=payment_svc.PaymentKind.DEPOSIT,
                amount_cents=None,
                method="card",
                idempotency_key=f"{idempotency_key}:intent",
                return_url=None,
            )

        detail = await _detail(session, booking, viewer_id=user.id, role=user.role.value)
        return detail.model_dump(mode="json"), "booking", str(booking.id)

    body, replay = await run_idempotent(
        session,
        user_id=user.id,
        endpoint=endpoint,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        status_code=201,
        operation=operation,
    )
    return BookingDetailOut(**body)


# ── Consulta ──────────────────────────────────────────────────────────────────


@router.get("/{booking_id}", response_model=BookingDetailOut, summary="Detalle de una reserva")
async def get_booking(booking_id: uuid.UUID, session: DbSession, user: CurrentUser) -> BookingDetailOut:
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    return await _detail(session, booking, viewer_id=user.id, role=actor)


@router.get("/{booking_id}/contract", summary="Contrato en PDF")
async def get_contract(booking_id: uuid.UUID, session: DbSession, user: CurrentUser) -> dict:
    booking, _ = await _get_booking_for_actor(session, booking_id, user)
    if not booking.contract_key:
        raise AppError(
            ErrorCode.CONTRACT_NOT_READY,
            message="El contrato se genera al confirmar la reserva. Recarga en unos segundos.",
        )
    from app.services import storage

    return {"url": storage.presign_download(booking.contract_key), "expires_in": settings.presign_ttl_seconds}


@router.get(
    "/{booking_id}/refund-preview",
    response_model=RefundPolicyPreviewOut,
    summary="Simular el reembolso si cancelo ahora",
)
async def refund_preview(booking_id: uuid.UUID, session: DbSession, user: CurrentUser) -> RefundPolicyPreviewOut:
    booking, _ = await _get_booking_for_actor(session, booking_id, user)
    tier, explanation, days = svc.refund_tier_for(booking)
    base = max(booking.paid_cents - booking.security_deposit_cents, 0)
    return RefundPolicyPreviewOut(
        tier_percent=tier,
        eligible_refund_cents=base * tier // 100,
        fee_retained_cents=booking.service_fee_cents if tier < 100 else 0,
        security_deposit_refund_cents=booking.security_deposit_cents,
        days_until_event=round(days, 2),
        policy_explanation=explanation,
        next_tier_deadline=(booking.start_at - timedelta(days=settings.cancel_half_refund_days)).date(),
    )


# ── Cancelación y reprogramación ──────────────────────────────────────────────


@router.post(
    "/{booking_id}/cancel",
    response_model=CancelBookingResponse,
    summary="Cancelar reserva",
    responses={409: {"model": ErrorResponse, "description": "INVALID_STATE_TRANSITION · REFUND_NOT_ALLOWED"}},
)
async def cancel_booking(
    booking_id: uuid.UUID,
    payload: CancelBookingRequest,
    session: DbSession,
    user: CurrentUser,
    idempotency_key: IdempotencyKey,
    request: Request,
) -> CancelBookingResponse:
    """Aplica la política escalonada y materializa el reembolso. Idempotente por llave."""
    if not payload.acknowledge_policy:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="Debes confirmar que leíste la política de cancelación.",
            details={"field": "acknowledge_policy"},
        )
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    endpoint = f"POST /bookings/{booking_id}/cancel"

    async def operation() -> tuple[dict, str, str]:
        outcome = await svc.cancel_booking(
            session,
            booking=booking,
            actor=actor,
            actor_id=user.id,
            reason=f"{payload.reason_code}: {payload.reason}",
            request_refund=payload.request_refund,
            apply_policy=actor != PROVIDER,
        )
        refund_id = None
        refund_status = None
        if payload.request_refund and outcome.refund_cents > 0:
            payment = next(
                (p for p in booking.payments if p.status == "SUCCEEDED"), None
            )
            if payment is not None:
                refund = await payment_svc.create_refund(
                    session,
                    payment=payment,
                    amount_cents=outcome.refund_cents,
                    reason="provider_cancel" if actor == PROVIDER else "client_cancel",
                    initiated_by=user.id,
                    idempotency_key=f"{idempotency_key}:refund",
                )
                refund_id = refund.id
                refund_status = refund.status
        result = CancelBookingResponse(
            booking_id=booking.id,
            state=BookingState(booking.state),
            refund_cents=outcome.refund_cents,
            refund_tier_percent=outcome.tier_percent,
            refund_id=refund_id,
            refund_status=refund_status,
            message="Reserva cancelada. La fecha quedó libre para otros eventos.",
            policy_explanation=outcome.explanation,
        )
        return result.model_dump(mode="json"), "booking", str(booking.id)

    body, _ = await run_idempotent(
        session,
        user_id=user.id,
        endpoint=endpoint,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        status_code=200,
        operation=operation,
    )
    await audit.record(
        session,
        action=AuditAction.BOOKING_STATE_CHANGED,
        entity="booking",
        entity_id=booking.id,
        actor_id=user.id,
        actor_role=actor,
        reason=payload.reason,
        request=request,
    )
    await session.commit()
    return CancelBookingResponse(**body)


@router.post(
    "/{booking_id}/reschedule",
    response_model=BookingDetailOut,
    summary="Reprogramar reserva",
    responses={409: {"model": ErrorResponse, "description": "RESCHEDULE_TARGET_UNAVAILABLE"}},
)
async def reschedule_booking(
    booking_id: uuid.UUID,
    payload: RescheduleRequest,
    session: DbSession,
    user: CurrentUser,
) -> BookingDetailOut:
    """Conserva el precio original (snapshot). Si la nueva fecha tiene tarifa mayor, no se recobra."""
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    if actor not in {CLIENT, PROVIDER, ADMIN}:
        raise AppError(ErrorCode.FORBIDDEN)
    await svc.reschedule_booking(
        session,
        booking=booking,
        new_start_at=payload.new_start_at,
        new_end_at=payload.new_end_at,
        actor=actor,
        actor_id=user.id,
        reason=payload.reason,
    )
    return await _detail(session, booking, viewer_id=user.id, role=actor)


# ── Operación del día ─────────────────────────────────────────────────────────


@router.post("/{booking_id}/check-in", response_model=BookingDetailOut, summary="Check-in del evento")
async def check_in(
    booking_id: uuid.UUID, payload: CheckInRequest, session: DbSession, user: CurrentUser
) -> BookingDetailOut:
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    await svc.check_in(
        session,
        booking=booking,
        actor_id=user.id,
        qr_token=payload.qr_token,
        lat=payload.lat,
        lng=payload.lng,
    )
    return await _detail(session, booking, viewer_id=user.id, role=actor)


@router.post("/{booking_id}/check-out", response_model=BookingDetailOut, summary="Check-out y evidencia")
async def check_out(
    booking_id: uuid.UUID, payload: CheckOutRequest, session: DbSession, user: CurrentUser
) -> BookingDetailOut:
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    await svc.check_out(
        session,
        booking=booking,
        actor_id=user.id,
        damages_cents=payload.damages_cents,
        damage_notes=payload.damage_notes,
        evidence_keys=payload.evidence_keys,
        release_security_deposit=payload.release_security_deposit,
    )
    return await _detail(session, booking, viewer_id=user.id, role=actor)


@router.post(
    "/{booking_id}/complete",
    response_model=BookingDetailOut,
    summary="Marcar evento como completado (proveedor)",
)
async def complete(
    booking_id: uuid.UUID, payload: CompleteRequest, session: DbSession, user: CurrentUser
) -> BookingDetailOut:
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    if actor not in {PROVIDER, ADMIN}:
        raise AppError(ErrorCode.FORBIDDEN, message="Sólo el anfitrión puede cerrar el evento.")
    await svc.complete_booking(
        session, booking=booking, actor_id=user.id, note=payload.note, actual_guests=payload.actual_guests
    )
    from app.tasks.jobs import on_booking_completed

    on_booking_completed.delay(str(booking.id))
    return await _detail(session, booking, viewer_id=user.id, role=actor)


@router.post(
    "/{booking_id}/no-show",
    response_model=BookingDetailOut,
    summary="Marcar no-show (proveedor, con evidencia obligatoria)",
    responses={422: {"model": ErrorResponse, "description": "VALIDATION_ERROR (falta evidencia)"}},
)
async def no_show(
    booking_id: uuid.UUID, payload: NoShowRequest, session: DbSession, user: CurrentUser
) -> BookingDetailOut:
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    if actor not in {PROVIDER, ADMIN}:
        raise AppError(ErrorCode.FORBIDDEN)
    await svc.mark_no_show(
        session,
        booking=booking,
        actor_id=user.id,
        reason=payload.reason,
        evidence_keys=payload.evidence_keys,
        wait_minutes=payload.wait_minutes,
        witnesses=payload.witnesses,
    )
    return await _detail(session, booking, viewer_id=user.id, role=actor)


# ── Disputas ──────────────────────────────────────────────────────────────────


@router.post(
    "/{booking_id}/dispute",
    status_code=status.HTTP_201_CREATED,
    summary="Abrir disputa",
    responses={409: {"model": ErrorResponse, "description": "DISPUTE_ALREADY_OPEN"}},
)
async def open_dispute(
    booking_id: uuid.UUID, payload: DisputeCreateRequest, session: DbSession, user: CurrentUser
) -> dict:
    """Abrir disputa congela el payout del proveedor hasta la resolución (edge case B11)."""
    booking, actor = await _get_booking_for_actor(session, booking_id, user)
    existing = await session.scalar(
        select(Dispute).where(Dispute.booking_id == booking.id, Dispute.status.in_(["OPEN", "UNDER_REVIEW"]))
    )
    if existing is not None:
        raise AppError(
            ErrorCode.DISPUTE_ALREADY_OPEN,
            details={"dispute_id": str(existing.id), "status": existing.status},
        )

    dispute = Dispute(
        booking_id=booking.id,
        opened_by_id=user.id,
        against_provider_id=booking.provider_id,
        kind=payload.kind,
        description=payload.description,
        amount_claimed_cents=payload.amount_claimed_cents,
        deadline_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add(dispute)
    await session.flush()

    frozen = await payment_svc.freeze_payouts_for_provider(
        session,
        provider_id=booking.provider_id,
        dispute_id=dispute.id,
        reason=f"Disputa {payload.kind} en la reserva {booking.folio}",
    )
    dispute.frozen_payout_id = None
    await svc.transition(
        session,
        booking=booking,
        to_state=BookingState.DISPUTED,
        actor=actor,
        actor_id=user.id,
        reason=payload.description[:200],
        context={"dispute_kind": payload.kind, "frozen_payouts": frozen},
    )
    await audit.record(
        session,
        action=AuditAction.DISPUTE_RESOLVED,
        entity="dispute",
        entity_id=dispute.id,
        actor_id=user.id,
        after={"kind": payload.kind, "frozen_payouts": frozen},
    )
    await session.commit()
    return {"dispute_id": str(dispute.id), "status": dispute.status, "frozen_payouts": frozen}


me_router = APIRouter()


@me_router.get(
    "",
    response_model=Paginated[BookingSummaryOut],
    summary="Mis reservas (cliente o proveedor)",
)
async def my_bookings(
    session: DbSession,
    user: CurrentUser,
    role: str = Query("client", pattern="^(client|provider)$"),
    state_filter: list[BookingState] | None = Query(default=None, alias="status"),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,
) -> Paginated[BookingSummaryOut]:
    """El filtro por rol decide el conjunto; nunca se acepta un `user_id` del cliente."""
    if role == "provider" and user.role not in {Role.PROVIDER.value, Role.ADMIN.value, Role.SUPPORT.value}:
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="No tienes perfil de anfitrión para ver esa bandeja.",
        )
    stmt = svc.bookings_query(
        user_id=user.id,
        role=Role.PROVIDER.value if role == "provider" else Role.CLIENT.value,
        states=state_filter,
        from_date=from_date,
        to_date=to_date,
    ).limit(limit + 1)
    rows = (await session.scalars(stmt)).all()
    items = [_summary(booking, role=role) for booking in rows[:limit]]
    return Paginated[BookingSummaryOut](
        items=items,
        meta={
            "limit": limit,
            "has_more": len(rows) > limit,
            "next_cursor": None,
            "total_estimate": None,
        },
    )


@me_router.get("/upcoming", response_model=list[BookingSummaryOut], summary="Próximos eventos")
async def upcoming(session: DbSession, user: CurrentUser) -> list[BookingSummaryOut]:
    rows = await svc.upcoming_for_user(session, user_id=user.id, limit=3)
    return [_summary(booking) for booking in rows]


admin_router = APIRouter()


@admin_router.patch("/{booking_id}/resolve", summary="Resolver disputa (admin)")
async def resolve_dispute(
    booking_id: uuid.UUID,
    payload: DisputeResolveRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> dict:
    """La resolución ajusta dinero: reembolso al cliente y/o liberación del payout."""
    dispute = await session.scalar(
        select(Dispute).where(Dispute.booking_id == booking_id, Dispute.status.in_(["OPEN", "UNDER_REVIEW"]))
    )
    if dispute is None:
        raise AppError(ErrorCode.DISPUTE_NOT_FOUND, details={"booking_id": str(booking_id)})
    if dispute.deadline_at and dispute.deadline_at < datetime.now(UTC) and payload.resolution == "RESOLVED_CLIENT":
        raise AppError(
            ErrorCode.DISPUTE_DEADLINE_PASSED,
            message="El plazo del banco para este contracargo ya venció.",
            details={"deadline_at": dispute.deadline_at.isoformat()},
        )

    booking = await session.scalar(select(Booking).where(Booking.id == booking_id))
    assert booking is not None
    dispute.status = payload.resolution
    dispute.amount_awarded_cents = payload.amount_to_client_cents
    dispute.resolution_notes = payload.notes
    dispute.resolved_by_id = admin.id
    dispute.resolved_at = datetime.now(UTC)

    if payload.amount_to_client_cents > 0:
        payment = next((p for p in booking.payments if p.status in {"SUCCEEDED", "PARTIALLY_REFUNDED"}), None)
        if payment is not None:
            await payment_svc.create_refund(
                session,
                payment=payment,
                amount_cents=payload.amount_to_client_cents,
                reason="dispute_resolved",
                initiated_by=admin.id,
                notes=payload.notes,
            )
    if payload.unfreeze_payout and booking.provider_id:
        from app.models.payment import Payout
        from app.models.enums import PayoutStatus

        payouts = (
            await session.scalars(
                select(Payout).where(
                    Payout.provider_id == booking.provider_id,
                    Payout.status == PayoutStatus.FROZEN.value,
                )
            )
        ).all()
        for payout in payouts:
            payout.status = PayoutStatus.PENDING.value
            payout.frozen_reason = None

    target = (
        BookingState.COMPLETED
        if payload.resolution in {"RESOLVED_PROVIDER", "RESOLVED_SPLIT"}
        else BookingState.REFUNDED
    )
    await svc.transition(
        session,
        booking=booking,
        to_state=target,
        actor=ADMIN,
        actor_id=admin.id,
        reason=payload.notes[:200],
    )
    await audit.record(
        session,
        action=AuditAction.DISPUTE_RESOLVED,
        entity="dispute",
        entity_id=dispute.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        after={
            "resolution": payload.resolution,
            "to_client": payload.amount_to_client_cents,
            "to_provider": payload.amount_to_provider_cents,
        },
        request=request,
    )
    await session.commit()
    return {"dispute_id": str(dispute.id), "status": dispute.status, "booking_state": booking.state}
