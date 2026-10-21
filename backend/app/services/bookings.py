"""Servicio de reservas: apartado atómico, conversión, transiciones, cancelación y operación.

Invariantes que este módulo garantiza:
1. Nunca dos eventos solapados en un venue (constraint de exclusión + lock asesor).
2. Toda transición pasa por la máquina de estados y deja `BookingStatusHistory` + `AuditLog`.
3. El precio del hold/booking viene del snapshot, nunca del cliente.
"""

from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.availability import SeasonalRate
from app.models.booking import Booking, BookingHold, BookingStatusHistory
from app.models.enums import (
    AuditAction,
    BookingState,
    HoldStatus,
    Role,
)
from app.models.notification import Coupon, CouponRedemption
from app.models.payment import Refund
from app.models.provider import ProviderProfile
from app.models.review import Review
from app.models.venue import Venue, VenueAmenity
from app.schemas.booking import HoldCreateRequest
from app.services import audit, availability, pricing
from app.services.state_machine import (
    ADMIN,
    CLIENT,
    PROVIDER,
    SYSTEM,
    GuardContext,
    assert_transition,
    is_terminal,
)

FOLIO_ALPHABET = string.ascii_uppercase + string.digits
EXCLUSION_VIOLATION = "23P01"
DEADLOCK = "40P01"


# ── Utilidades ─────────────────────────────────────────────────────────────────


def new_folio(prefix: str) -> str:
    """Folio legible para soporte: HD-7K3M21 / QX-4F2A91. Sin caracteres ambiguos."""
    safe = FOLIO_ALPHABET.replace("O", "").replace("I", "").replace("0", "").replace("1", "")
    body = "".join(secrets.choice(safe) for _ in range(6))
    return f"{prefix}-{body}"


def _is_exclusion_violation(exc: DBAPIError) -> bool:
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == EXCLUSION_VIOLATION


async def advisory_venue_lock(session: AsyncSession, venue_id: uuid.UUID) -> None:
    """Lock asesor por venue: reduce el ruido de conflictos. La garantía es la constraint."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:venue_id, 0))"),
        {"venue_id": str(venue_id)},
    )


@dataclass(slots=True)
class TransitionResult:
    booking: Booking
    transition: Any
    idempotent_replay: bool = False


# ── Carga de contexto ──────────────────────────────────────────────────────────


async def get_venue_or_404(session: AsyncSession, venue_id: uuid.UUID) -> Venue:
    venue = await session.scalar(
        select(Venue)
        .where(Venue.id == venue_id, Venue.deleted_at.is_(None))
        .options(*[])
    )
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})
    return venue


async def load_venue_for_quote(session: AsyncSession, venue_id: uuid.UUID) -> Venue:
    """Carga el venue con reglas de precio, temporadas, amenidades y reglas de apertura."""
    venue = await session.scalar(
        select(Venue)
        .where(Venue.id == venue_id, Venue.deleted_at.is_(None))
        .options(
            selectinload(Venue.pricing_rules),
            selectinload(Venue.availability_rules),
            selectinload(Venue.amenities),
        )
    )
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})
    return venue


async def evaluate_coupon(
    session: AsyncSession,
    *,
    code: str | None,
    venue: Venue,
    user_id: uuid.UUID,
    subtotal_cents: int,
) -> pricing.CouponEvaluation:
    """Valida el cupón contra: vigencia, usos, exclusiones, primer evento y no acumulación."""
    if not code:
        return pricing.CouponEvaluation()

    coupon = await session.scalar(
        select(Coupon).where(func.upper(Coupon.code) == code.upper(), Coupon.deleted_at.is_(None))
    )
    if coupon is None or not coupon.is_active:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_NOT_FOUND.value)

    now = datetime.now(UTC)
    if coupon.valid_from and now < coupon.valid_from:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_EXPIRED.value)
    if coupon.valid_to and now > coupon.valid_to:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_EXPIRED.value)
    if coupon.max_redemptions is not None and coupon.redemptions_count >= coupon.max_redemptions:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_EXHAUSTED.value)
    if str(venue.id) in {str(v) for v in (coupon.excluded_venue_ids or [])}:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_NOT_APPLICABLE.value)
    if coupon.venue_ids and str(venue.id) not in {str(v) for v in coupon.venue_ids}:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_NOT_APPLICABLE.value)
    if coupon.min_subtotal_cents and subtotal_cents < coupon.min_subtotal_cents:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_NOT_APPLICABLE.value)

    used = await session.scalar(
        select(func.count())
        .select_from(CouponRedemption)
        .where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.user_id == user_id,
            CouponRedemption.released_at.is_(None),
        )
    )
    if (used or 0) >= coupon.per_user_limit:
        return pricing.CouponEvaluation(reason=ErrorCode.COUPON_ALREADY_REDEEMED.value)

    if coupon.first_booking_only:
        prior = await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.client_id == user_id,
                Booking.state.in_(
                    [
                        BookingState.CONFIRMED.value,
                        BookingState.COMPLETED.value,
                        BookingState.PENDING_PAYMENT.value,
                    ]
                ),
            )
        )
        if prior:
            return pricing.CouponEvaluation(reason=ErrorCode.COUPON_NOT_APPLICABLE.value)

    if coupon.kind == "PERCENT":
        discount = subtotal_cents * coupon.value // 100
        if coupon.max_discount_cents:
            discount = min(discount, coupon.max_discount_cents)
    else:
        discount = coupon.value
    return pricing.CouponEvaluation(
        coupon_id=coupon.id, code=coupon.code, kind=coupon.kind, discount_cents=discount
    )


async def build_venue_quote(
    session: AsyncSession,
    *,
    venue: Venue,
    start_at: datetime,
    end_at: datetime,
    guests: int,
    extras: list[str],
    user_id: uuid.UUID | None = None,
    coupon_code: str | None = None,
) -> Any:
    pricing_rules = list(venue.pricing_rules)
    seasonal = list(
        await session.scalars(
            select(SeasonalRate).where(
                SeasonalRate.venue_id == venue.id, SeasonalRate.deleted_at.is_(None)
            )
        )
    )
    amenity_rows = list(
        await session.scalars(
            select(VenueAmenity)
            .where(VenueAmenity.venue_id == venue.id)
            .options(selectinload(VenueAmenity.amenity))
        )
    )
    coupon = pricing.CouponEvaluation()
    if coupon_code and user_id:
        coupon = await evaluate_coupon(
            session,
            code=coupon_code,
            venue=venue,
            user_id=user_id,
            subtotal_cents=venue.base_price_cents or 0,
        )
        if coupon.reason:
            raise AppError(
                ErrorCode(coupon.reason),
                details={"code": coupon_code, "reason": coupon.reason},
            )

    return pricing.build_quote(
        pricing.QuoteInput(
            venue=venue,
            start_at=start_at,
            end_at=end_at,
            guests=guests,
            extras=extras,
            pricing_rules=pricing_rules,
            seasonal_rates=seasonal,
            amenities=amenity_rows,
            coupon=coupon,
        )
    )


# ── Apartado (hold) ────────────────────────────────────────────────────────────


async def create_hold(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    payload: HoldCreateRequest,
    endpoint: str = "/bookings/hold",
) -> tuple[BookingHold, Any]:
    """Crea el apartado con cotización congelada. Atómico y resistente a carreras.

    Orden: lock asesor por venue → validar reglas → cotizar → INSERT (la constraint decide).
    """
    venue = await load_venue_for_quote(session, payload.venue_id)
    if venue.status != "PUBLISHED":
        raise AppError(
            ErrorCode.VENUE_SUSPENDED,
            message="Este espacio no está disponible para reservar en este momento.",
            details={"venue_id": str(venue.id), "status": venue.status},
        )
    if venue.provider and venue.provider.user_id == client_id:
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="No puedes reservar tu propio espacio.",
        )

    active_holds = await session.scalar(
        select(func.count())
        .select_from(BookingHold)
        .where(
            BookingHold.client_id == client_id,
            BookingHold.status == HoldStatus.ACTIVE.value,
            BookingHold.expires_at > datetime.now(UTC),
        )
    )
    if (active_holds or 0) >= settings.hold_max_active_per_user:
        raise AppError(
            ErrorCode.HOLD_LIMIT_REACHED,
            details={"active": active_holds, "max": settings.hold_max_active_per_user},
        )

    quote = await build_venue_quote(
        session,
        venue=venue,
        start_at=payload.start_at,
        end_at=payload.end_at,
        guests=payload.guests,
        extras=payload.extras,
        user_id=client_id,
        coupon_code=payload.coupon_code,
    )
    if payload.quote_total_cents is not None:
        pricing.verify_snapshot_total(quote.snapshot, payload.quote_total_cents)

    availability.assert_within_rules(venue, payload.start_at, payload.end_at)
    free, conflicts = await availability.is_slot_free(
        session, venue, payload.start_at, payload.end_at
    )
    if not free:
        raise AppError(
            ErrorCode.VENUE_NOT_AVAILABLE,
            details={
                "conflicts": [
                    {"source": c.source, "start_at": c.start_at.isoformat(), "end_at": c.end_at.isoformat()}
                    for c in conflicts
                ],
                "suggested": await _suggest_alternatives(session, venue, payload),
            },
        )

    await advisory_venue_lock(session, venue.id)

    buffer = timedelta(minutes=venue.buffer_minutes)
    hold = BookingHold(
        folio=new_folio("HD"),
        venue_id=venue.id,
        client_id=client_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        venue_timezone=venue.timezone,
        slot_kind=payload.slot_kind.value,
        guests=payload.guests,
        event_type_slug=payload.event_type_slug,
        quote_snapshot=quote.snapshot,
        total_cents=quote.total_cents,
        deposit_cents=quote.deposit_cents,
        currency=quote.currency,
        coupon_code=quote.coupon.code if quote.coupon else None,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.hold_ttl_seconds),
        status=HoldStatus.ACTIVE.value,
    )
    session.add(hold)
    try:
        await session.flush()
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        if isinstance(exc, DBAPIError) and _is_exclusion_violation(exc):
            # Otra persona ganó el slot entre la validación y el INSERT (edge case A1)
            raise AppError(
                ErrorCode.VENUE_NOT_AVAILABLE,
                message="Alguien más apartó ese horario hace un segundo. Elige otra fecha u otro espacio.",
                details={"venue_id": str(venue.id), "suggested": []},
            ) from exc
        raise

    if quote.coupon and quote.coupon.coupon_id:
        session.add(
            CouponRedemption(
                coupon_id=quote.coupon.coupon_id,
                user_id=client_id,
                hold_id=hold.id,
                discount_cents=quote.coupon.discount_cents,
            )
        )

    await audit.record(
        session,
        action=AuditAction.HOLD_CREATED,
        entity="booking_hold",
        entity_id=hold.id,
        actor_id=client_id,
        actor_role=Role.CLIENT.value,
        after={
            "folio": hold.folio,
            "venue_id": str(venue.id),
            "start_at": hold.start_at,
            "end_at": hold.end_at,
            "total_cents": hold.total_cents,
            "expires_at": hold.expires_at,
        },
    )
    await session.commit()
    return hold, quote


async def _suggest_alternatives(
    session: AsyncSession, venue: Venue, payload: HoldCreateRequest, limit: int = 3
) -> list[dict[str, Any]]:
    """Alternativas útiles en el mismo venue (±3 días) para el mensaje de error."""
    suggestions: list[dict[str, Any]] = []
    for offset in (1, -1, 2, -2, 3, -3):
        candidate_start = payload.start_at + timedelta(days=offset)
        candidate_end = payload.end_at + timedelta(days=offset)
        if candidate_start < datetime.now(UTC):
            continue
        free, _ = await availability.is_slot_free(session, venue, candidate_start, candidate_end)
        if free:
            suggestions.append(
                {
                    "start_at": candidate_start.isoformat(),
                    "end_at": candidate_end.isoformat(),
                    "label": f"{offset:+d} día(s)",
                }
            )
        if len(suggestions) >= limit:
            break
    return suggestions


async def extend_hold(
    session: AsyncSession, *, hold_id: uuid.UUID, client_id: uuid.UUID
) -> BookingHold:
    """Una sola extensión por apartado: evita holds infinitos que bloquean el calendario."""
    hold = await _get_hold(session, hold_id)
    if hold.client_id != client_id:
        raise AppError(ErrorCode.FORBIDDEN)
    if hold.status != HoldStatus.ACTIVE.value:
        raise AppError(ErrorCode.HOLD_EXPIRED, details={"status": hold.status})
    if hold.extended_count >= 1:
        raise AppError(
            ErrorCode.HOLD_EXPIRED,
            message="Este apartado ya se extendió una vez. Completa el pago para conservarlo.",
        )
    if hold.expires_at < datetime.now(UTC):
        raise AppError(ErrorCode.HOLD_EXPIRED)

    hold.expires_at = hold.expires_at + timedelta(seconds=settings.hold_ttl_seconds)
    hold.extended_count += 1
    await session.commit()
    return hold


async def _get_hold(session: AsyncSession, hold_id: uuid.UUID) -> BookingHold:
    hold = await session.get(BookingHold, hold_id)
    if hold is None or hold.deleted_at is not None:
        raise AppError(ErrorCode.HOLD_NOT_FOUND, details={"hold_id": str(hold_id)})
    return hold


async def release_hold(
    session: AsyncSession, hold: BookingHold, *, reason: str, status: HoldStatus = HoldStatus.RELEASED
) -> None:
    hold.status = status.value
    hold.released_reason = reason
    await session.flush()


async def expire_holds(session: AsyncSession, batch: int = 200) -> int:
    """Job `expire_holds` (cada minuto). Libera slots y avisa al cliente (edge case A2)."""
    now = datetime.now(UTC)
    holds = (
        await session.scalars(
            select(BookingHold)
            .where(
                BookingHold.status == HoldStatus.ACTIVE.value,
                BookingHold.expires_at <= now,
            )
            .order_by(BookingHold.expires_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()

    for hold in holds:
        hold.status = HoldStatus.EXPIRED.value
        hold.released_reason = "ttl_expired"
        await audit.record(
            session,
            action=AuditAction.HOLD_EXPIRED,
            entity="booking_hold",
            entity_id=hold.id,
            actor_role=SYSTEM,
            after={"folio": hold.folio, "expired_at": now},
        )
    if holds:
        await session.commit()
    return len(holds)


# ── Conversión hold → booking ─────────────────────────────────────────────────


async def confirm_from_hold(
    session: AsyncSession,
    *,
    hold_id: uuid.UUID,
    client_id: uuid.UUID,
    actor: str = SYSTEM,
    reason: str | None = None,
    state: BookingState = BookingState.CONFIRMED,
) -> Booking:
    """Convierte el apartado en reserva **en una sola transacción** (ADR-003).

    Si el hold expiró, no se convierte: el pago se reconcilia con reembolso (edge case A2).
    """
    hold = await session.scalar(
        select(BookingHold).where(BookingHold.id == hold_id).with_for_update()
    )
    if hold is None:
        raise AppError(ErrorCode.HOLD_NOT_FOUND, details={"hold_id": str(hold_id)})
    if hold.client_id != client_id:
        raise AppError(ErrorCode.FORBIDDEN)
    if hold.status == HoldStatus.CONVERTED.value:
        existing = await session.scalar(
            select(Booking).where(Booking.hold_id == hold.id)
        )
        if existing:
            return existing  # idempotente
        raise AppError(ErrorCode.HOLD_ALREADY_CONVERTED)
    if hold.status != HoldStatus.ACTIVE.value or hold.expires_at <= datetime.now(UTC):
        raise AppError(
            ErrorCode.HOLD_EXPIRED,
            details={"hold_id": str(hold.id), "expired_at": hold.expires_at.isoformat()},
        )

    venue = await get_venue_or_404(session, hold.venue_id)
    await advisory_venue_lock(session, venue.id)

    buffer = timedelta(minutes=venue.buffer_minutes)
    snapshot = hold.quote_snapshot or {}
    totals = snapshot.get("totals", {})

    booking = Booking(
        folio=new_folio("QX"),
        hold_id=hold.id,
        venue_id=hold.venue_id,
        provider_id=venue.provider_id,
        client_id=hold.client_id,
        state=state.value,
        previous_state=BookingState.HOLD.value,
        start_at=hold.start_at,
        end_at=hold.end_at,
        blocked_from=hold.start_at - buffer,
        blocked_to=hold.end_at + buffer,
        venue_timezone=hold.venue_timezone,
        slot_kind=hold.slot_kind,
        guests=hold.guests,
        event_type_slug=hold.event_type_slug,
        quote_snapshot=snapshot,
        rules_snapshot={
            "buffer_minutes": venue.buffer_minutes,
            "deposit_percent": venue.deposit_percent,
            "cancellation_policy": venue.provider.cancellation_policy if venue.provider else "TIERED_STANDARD",
            "engine_version": snapshot.get("engine_version"),
        },
        subtotal_cents=int(totals.get("subtotal_cents", hold.total_cents)),
        extras_cents=int(totals.get("extras_cents", 0)),
        cleaning_cents=int(totals.get("cleaning_cents", 0)),
        discount_cents=int(totals.get("discount_cents", 0)),
        service_fee_cents=int(totals.get("service_fee_cents", 0)),
        tax_cents=int(totals.get("tax_cents", 0)),
        security_deposit_cents=int(totals.get("security_deposit_cents", 0)),
        total_cents=hold.total_cents,
        deposit_cents=hold.deposit_cents,
        currency=hold.currency,
        coupon_code=hold.coupon_code,
    )
    session.add(booking)

    # El hold se marca convertido; su constraint sólo aplica a `ACTIVE`, así que el slot
    # queda cubierto por la constraint de `bookings` sin ventana de solape.
    hold.status = HoldStatus.CONVERTED.value
    hold.converted_booking_id = booking.id if booking.id else None
    try:
        await session.flush()
    except DBAPIError as exc:
        await session.rollback()
        if _is_exclusion_violation(exc):
            raise AppError(ErrorCode.VENUE_NOT_AVAILABLE) from exc
        raise

    hold.converted_booking_id = booking.id
    session.add(
        BookingStatusHistory(
            booking_id=booking.id,
            from_state=BookingState.HOLD.value,
            to_state=state.value,
            actor_id=client_id,
            actor_role=actor,
            reason=reason or "hold_convertido",
            context={"hold_folio": hold.folio},
        )
    )
    await audit.record(
        session,
        action=AuditAction.BOOKING_STATE_CHANGED,
        entity="booking",
        entity_id=booking.id,
        actor_id=client_id,
        actor_role=actor,
        after={"state": state.value, "folio": booking.folio, "total_cents": booking.total_cents},
    )
    await session.commit()
    return booking


# ── Transiciones genéricas ────────────────────────────────────────────────────


async def transition(
    session: AsyncSession,
    *,
    booking: Booking,
    to_state: BookingState,
    actor: str,
    actor_id: uuid.UUID | None = None,
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    idempotent_replay: bool = False,
    extra_guards: dict[str, Any] | None = None,
) -> TransitionResult:
    """Aplica la transición validada por la máquina de estados y la audita."""
    guard_kwargs: dict[str, Any] = {
        "now": datetime.now(UTC),
        "actor": actor,
        "paid_cents": booking.paid_cents,
        "total_cents": booking.total_cents,
        "start_at": booking.start_at,
        "end_at": booking.end_at,
        "check_in_at": booking.check_in_at,
        "reason": reason,
    }
    guard_kwargs.update(extra_guards or {})
    ctx = GuardContext(**guard_kwargs)

    from_state = BookingState(booking.state)
    transition_def = assert_transition(from_state, to_state, ctx)

    booking.previous_state = from_state
    booking.state = to_state.value
    if to_state in {
        BookingState.CANCELLED_BY_CLIENT,
        BookingState.CANCELLED_BY_PROVIDER,
    }:
        booking.cancelled_at = datetime.now(UTC)
        booking.cancelled_by_id = actor_id
        booking.cancellation_reason = reason

    session.add(
        BookingStatusHistory(
            booking_id=booking.id,
            from_state=from_state.value,
            to_state=to_state.value,
            actor_id=actor_id,
            actor_role=actor,
            reason=reason,
            idempotent_replay=idempotent_replay,
            context={**(context or {}), "effects": list(transition_def.effects)},
        )
    )
    await audit.record(
        session,
        action=AuditAction.BOOKING_STATE_CHANGED,
        entity="booking",
        entity_id=booking.id,
        actor_id=actor_id,
        actor_role=actor,
        before={"state": from_state.value},
        after={"state": to_state.value, "effects": list(transition_def.effects)},
        reason=reason,
    )
    return TransitionResult(booking=booking, transition=transition_def)


def guard_context_for(booking: Booking, actor: str, **overrides: Any) -> GuardContext:
    kwargs: dict[str, Any] = {
        "now": datetime.now(UTC),
        "actor": actor,
        "paid_cents": booking.paid_cents,
        "total_cents": booking.total_cents,
        "start_at": booking.start_at,
        "end_at": booking.end_at,
        "check_in_at": booking.check_in_at,
    }
    kwargs.update(overrides)
    return GuardContext(**kwargs)


# ── Cancelación y reembolso ───────────────────────────────────────────────────


@dataclass(slots=True)
class CancellationOutcome:
    booking: Booking
    refund_cents: int
    tier_percent: int
    explanation: str
    refund: Refund | None = None


def refund_tier_for(booking: Booking, cancel_at: datetime | None = None) -> tuple[int, str, float]:
    when = cancel_at or datetime.now(UTC)
    days = (booking.start_at - when).total_seconds() / 86400
    tier, explanation = pricing.refund_tier(days)
    return tier, explanation, days


async def cancel_booking(
    session: AsyncSession,
    *,
    booking: Booking,
    actor: str,
    actor_id: uuid.UUID | None,
    reason: str,
    request_refund: bool = True,
    apply_policy: bool = True,
) -> CancellationOutcome:
    """Cancela aplicando la política escalonada. El depósito de garantía siempre se libera."""
    if is_terminal(BookingState(booking.state)):
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message="Esta reserva ya está cerrada.",
            details={"state": booking.state},
        )

    tier, explanation, days = refund_tier_for(booking)
    to_state = (
        BookingState.CANCELLED_BY_CLIENT if actor == CLIENT else BookingState.CANCELLED_BY_PROVIDER
    )
    if actor == ADMIN and reason.lower().startswith("provider"):
        to_state = BookingState.CANCELLED_BY_PROVIDER

    refundable_base = max(booking.paid_cents - booking.security_deposit_cents, 0)
    refund_cents = refundable_base if not apply_policy else refundable_base * tier // 100
    if actor == PROVIDER:
        refund_cents = booking.paid_cents  # el proveedor cancela: reembolso total
        tier = 100
        explanation = "El anfitrión canceló: se reembolsa el total pagado."

    result = await transition(
        session,
        booking=booking,
        to_state=to_state,
        actor=actor,
        actor_id=actor_id,
        reason=reason,
        context={"refund_cents": refund_cents, "tier_percent": tier},
    )
    booking.refund_tier_percent = tier

    refund: Refund | None = None
    if request_refund and refund_cents > 0:
        # El reembolso se materializa en el servicio de pagos (aquí sólo se registra el importe)
        booking.refunded_cents += refund_cents

    await session.commit()
    return CancellationOutcome(
        booking=result.booking,
        refund_cents=refund_cents,
        tier_percent=tier,
        explanation=explanation,
        refund=refund,
    )


async def reschedule_booking(
    session: AsyncSession,
    *,
    booking: Booking,
    new_start_at: datetime,
    new_end_at: datetime,
    actor: str,
    actor_id: uuid.UUID | None,
    reason: str | None,
) -> Booking:
    """Reprograma conservando el snapshot de precio y volviendo a validar disponibilidad."""
    if BookingState(booking.state) != BookingState.CONFIRMED:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message="Sólo se puede reprogramar una reserva confirmada.",
            details={"state": booking.state},
        )
    if booking.reschedule_count >= 2:
        raise AppError(
            ErrorCode.RESCHEDULE_TARGET_UNAVAILABLE,
            message="Esta reserva ya se reprogramó dos veces. Contacta a soporte.",
        )

    venue = await get_venue_or_404(session, booking.venue_id)
    await advisory_venue_lock(session, venue.id)

    free, conflicts = await availability.is_slot_free(
        session,
        venue,
        new_start_at,
        new_end_at,
        exclude_booking_id=booking.id,
    )
    if not free:
        raise AppError(
            ErrorCode.RESCHEDULE_TARGET_UNAVAILABLE,
            details={
                "conflicts": [c.source for c in conflicts],
                "hint": "Prueba otra fecha; el sistema sugiere alternativas en el mismo mes.",
            },
        )

    pricing.validate_window(venue, new_start_at, new_end_at)
    buffer = timedelta(minutes=venue.buffer_minutes)
    booking.start_at = new_start_at
    booking.end_at = new_end_at
    booking.blocked_from = new_start_at - buffer
    booking.blocked_to = new_end_at + buffer
    booking.reschedule_count += 1

    session.add(
        BookingStatusHistory(
            booking_id=booking.id,
            from_state=booking.state,
            to_state=booking.state,
            actor_id=actor_id,
            actor_role=actor,
            reason=reason or "reprogramación",
            context={
                "new_start_at": new_start_at.isoformat(),
                "new_end_at": new_end_at.isoformat(),
                "count": booking.reschedule_count,
            },
        )
    )
    await audit.record(
        session,
        action=AuditAction.BOOKING_RESCHEDULED,
        entity="booking",
        entity_id=booking.id,
        actor_id=actor_id,
        actor_role=actor,
        after={"start_at": new_start_at, "end_at": new_end_at},
        reason=reason,
    )
    try:
        await session.flush()
    except DBAPIError as exc:
        await session.rollback()
        if _is_exclusion_violation(exc):
            raise AppError(ErrorCode.RESCHEDULE_TARGET_UNAVAILABLE) from exc
        raise
    await session.commit()
    return booking


# ── Operación del día ─────────────────────────────────────────────────────────


async def check_in(
    session: AsyncSession,
    *,
    booking: Booking,
    actor_id: uuid.UUID,
    qr_token: str | None,
    lat: float | None = None,
    lng: float | None = None,
) -> Booking:
    if BookingState(booking.state) != BookingState.CONFIRMED:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message="Sólo se puede hacer check-in en una reserva confirmada.",
            details={"state": booking.state},
        )
    ctx = guard_context_for(booking, CLIENT if actor_id == booking.client_id else PROVIDER)
    if ctx.starts_in_hours and ctx.starts_in_hours > ctx.checkin_window_minutes / 60:
        raise AppError(
            ErrorCode.CHECK_IN_NOT_ALLOWED_YET,
            details={"starts_in_hours": round(ctx.starts_in_hours, 2)},
        )
    if qr_token:
        from app.core.security import TokenType, decode_token

        payload = decode_token(qr_token, expected=TokenType.QR_CHECKIN)
        if payload.sub != str(booking.id):
            raise AppError(ErrorCode.QR_TOKEN_INVALID)

    booking.check_in_at = datetime.now(UTC)
    session.add(
        BookingStatusHistory(
            booking_id=booking.id,
            from_state=booking.state,
            to_state=booking.state,
            actor_id=actor_id,
            actor_role=CLIENT if actor_id == booking.client_id else PROVIDER,
            reason="check-in",
            context={"lat": lat, "lng": lng, "qr": bool(qr_token)},
        )
    )
    await session.commit()
    return booking


async def check_out(
    session: AsyncSession,
    *,
    booking: Booking,
    actor_id: uuid.UUID,
    damages_cents: int | None,
    damage_notes: str | None,
    evidence_keys: list[str],
    release_security_deposit: bool,
) -> Booking:
    if booking.check_in_at is None:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message="No hay check-in registrado para esta reserva.",
        )
    booking.check_out_at = datetime.now(UTC)

    if damages_cents:
        # Daños: se documentan y se abre disputa automática si hay evidencia (edge case B14)
        booking.no_show_evidence = {
            **(booking.no_show_evidence or {}),
            "damages_cents": damages_cents,
            "damage_notes": damage_notes,
            "evidence_keys": evidence_keys,
        }
        if not evidence_keys:
            raise AppError(
                ErrorCode.EVIDENCE_LIMIT_REACHED,
                message="Para cobrar daños necesitas al menos una foto de evidencia.",
                details={"field": "evidence_keys"},
            )
    await session.commit()
    return booking


async def complete_booking(
    session: AsyncSession,
    *,
    booking: Booking,
    actor_id: uuid.UUID,
    note: str | None,
    actual_guests: int | None,
) -> Booking:
    result = await transition(
        session,
        booking=booking,
        to_state=BookingState.COMPLETED,
        actor=PROVIDER,
        actor_id=actor_id,
        reason=note or "evento finalizado",
        context={"actual_guests": actual_guests},
    )
    await session.commit()
    return result.booking


async def mark_no_show(
    session: AsyncSession,
    *,
    booking: Booking,
    actor_id: uuid.UUID,
    reason: str,
    evidence_keys: list[str],
    wait_minutes: int,
    witnesses: list[str],
) -> Booking:
    """No-show con evidencia obligatoria: sin evidencia, no se retiene el anticipo (C17/B14)."""
    if not evidence_keys:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="Para marcar no-show necesitas al menos una evidencia (foto o nota firmada).",
            details={"field": "evidence_keys"},
        )
    ctx = guard_context_for(booking, PROVIDER, has_evidence=True, reason=reason)
    if ctx.starts_in_hours is not None and ctx.starts_in_hours > 0:
        raise AppError(
            ErrorCode.BOOKING_ALREADY_STARTED,
            message="Aún no comienza el evento; no se puede marcar no-show.",
            details={"starts_in_hours": round(ctx.starts_in_hours, 2)},
        )
    result = await transition(
        session,
        booking=booking,
        to_state=BookingState.NO_SHOW,
        actor=PROVIDER,
        actor_id=actor_id,
        reason=reason,
        context={"wait_minutes": wait_minutes, "witnesses": witnesses},
        extra_guards={"has_evidence": True},
    )
    booking.no_show_evidence = {
        "reason": reason,
        "evidence_keys": evidence_keys,
        "wait_minutes": wait_minutes,
        "witnesses": witnesses,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    await session.commit()
    return result.booking


# ── Consultas ─────────────────────────────────────────────────────────────────


def bookings_query(
    *,
    user_id: uuid.UUID,
    role: str,
    states: list[BookingState] | None = None,
    venue_id: uuid.UUID | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> Select[tuple[Booking]]:
    """Filtro por rol: el cliente ve las suyas, el proveedor las de sus venues. Nunca por ID de ruta."""
    stmt = select(Booking).where(Booking.deleted_at.is_(None))
    if role == Role.PROVIDER.value:
        stmt = stmt.where(
            Booking.provider_id.in_(
                select(ProviderProfile.id).where(ProviderProfile.user_id == user_id)
            )
        )
    else:
        stmt = stmt.where(Booking.client_id == user_id)
    if states:
        stmt = stmt.where(Booking.state.in_([s.value for s in states]))
    if venue_id:
        stmt = stmt.where(Booking.venue_id == venue_id)
    if from_date:
        stmt = stmt.where(Booking.start_at >= from_date)
    if to_date:
        stmt = stmt.where(Booking.start_at <= to_date)
    return stmt.order_by(Booking.start_at.desc())


async def upcoming_for_user(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = 3
) -> list[Booking]:
    return list(
        await session.scalars(
            select(Booking)
            .where(
                Booking.client_id == user_id,
                Booking.state == BookingState.CONFIRMED.value,
                Booking.start_at >= datetime.now(UTC),
            )
            .order_by(Booking.start_at.asc())
            .limit(limit)
        )
    )


async def bookings_needing_reminder(
    session: AsyncSession, *, hours_ahead: int, limit: int = 500
) -> list[Booking]:
    """Candidatos a recordatorio 24 h / 2 h. La deduplicación la hace la tabla de notificaciones."""
    now = datetime.now(UTC)
    window_start = now + timedelta(hours=hours_ahead, minutes=-15)
    window_end = now + timedelta(hours=hours_ahead, minutes=15)
    return list(
        await session.scalars(
            select(Booking)
            .where(
                Booking.state == BookingState.CONFIRMED.value,
                Booking.start_at.between(window_start, window_end),
            )
            .limit(limit)
        )
    )


async def completed_without_review(
    session: AsyncSession, *, days_after: int = 1, limit: int = 500
) -> list[Booking]:
    """Job de solicitud de reseña: completadas hace N días y sin reseña."""
    cutoff = datetime.now(UTC) - timedelta(days=days_after)
    return list(
        await session.scalars(
            select(Booking)
            .where(
                Booking.state == BookingState.COMPLETED.value,
                Booking.end_at <= cutoff,
                ~select(Review.id).where(Review.booking_id == Booking.id).exists(),
            )
            .limit(limit)
        )
    )
