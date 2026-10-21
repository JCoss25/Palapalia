"""Jobs de pagos: reconciliación de holds vencidos, payouts y depósitos de garantía."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.booking import Booking, BookingHold
from app.models.enums import BookingState, HoldStatus, NotificationKind, PaymentKind, PaymentStatus, PayoutStatus
from app.models.payment import Payment, Payout
from app.models.user import User
from app.models.venue import Venue
from app.services import notifications as notification_service
from app.services import payments as payment_service
from app.tasks.celery_app import celery_app

log = get_logger("tasks.payments")


@celery_app.task(name="app.tasks.payments.reconcile_expired_hold_payment", bind=True, max_retries=3)
def reconcile_expired_hold_payment(self, payment_id: str) -> dict:  # type: ignore[no-untyped-def]
    """El hold venció pero el pago entró: reembolso automático + aviso (edge case A2).

    Nunca se confirma la reserva: el slot pudo venderse a otra persona.
    """
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            payment = await session.get(Payment, uuid.UUID(payment_id))
            if payment is None:
                return {"status": "not_found"}
            if payment.reconciled_at is not None:
                return {"status": "already_reconciled", "action": payment.reconciliation_action}

            refund = await payment_service.create_refund(
                session,
                payment=payment,
                amount_cents=None,
                reason="hold_expired",
                initiated_by=None,
                notes="El apartado venció antes de que se confirmara el pago.",
                is_automatic=True,
                idempotency_key=f"reconcile:{payment.id}",
            )
            payment.reconciled_at = datetime.now(UTC)
            payment.reconciliation_action = "refund_hold_expired"

            booking = await session.get(Booking, payment.booking_id)
            if booking is not None:
                booking.state = BookingState.REFUNDED.value
                user = await session.get(User, booking.client_id)
                venue = await session.get(Venue, booking.venue_id)
                if user is not None:
                    await notification_service.notify(
                        session,
                        user=user,
                        kind=NotificationKind.REFUND_ISSUED,
                        title="Te devolvimos tu pago",
                        body=(
                            "Tu apartado venció antes de confirmarse, así que reembolsamos el cobro "
                            f"({payment.amount_cents / 100:,.2f} {payment.currency}). "
                            f"{'Vuelve a apartar ' + venue.name + ' si sigues interesado.' if venue else ''}"
                        ).strip(),
                        deep_link=f"quinta://bookings/{booking.id}",
                        dedupe_key=f"refund-hold-expired:{payment.id}",
                        urgent=True,
                    )
            await session.commit()
            return {"payment_id": payment_id, "refund_id": str(refund.id), "status": refund.status}

    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        log.exception("reconcile_failed", payment_id=payment_id)
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1)) from exc


@celery_app.task(name="app.tasks.payments.reconcile_orphan_payments")
def reconcile_orphan_payments() -> dict:
    """Barrido de seguridad: pagos cobrados cuyo booking quedó en HOLD/REFUNDED (edge case A8)."""
    import asyncio

    async def run() -> dict:
        reconciled = 0
        async with SessionLocal() as session:
            payments = (
                await session.scalars(
                    select(Payment).where(
                        Payment.status == PaymentStatus.SUCCEEDED.value,
                        Payment.reconciled_at.is_(None),
                        Payment.created_at <= datetime.now(UTC) - timedelta(minutes=20),
                    )
                )
            ).all()
            for payment in payments:
                booking = await session.get(Booking, payment.booking_id)
                if booking is None:
                    continue
                if booking.state == BookingState.HOLD.value:
                    hold = (
                        await session.get(BookingHold, booking.hold_id)
                        if booking.hold_id
                        else None
                    )
                    if hold is None or hold.expires_at <= datetime.now(UTC):
                        reconcile_expired_hold_payment.delay(str(payment.id))
                        reconciled += 1
                elif booking.state == BookingState.PENDING_PAYMENT.value:
                    booking.state = BookingState.CONFIRMED.value
                    booking.previous_state = BookingState.PENDING_PAYMENT.value
                    reconciled += 1
            await session.commit()
        return {"reconciled": reconciled}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.payments.release_security_deposits")
def release_security_deposits() -> dict:
    """Libera depósitos de garantía 48 h después del evento si no hay daños ni disputa (B14)."""
    import asyncio

    from app.models.payment import Dispute

    async def run() -> dict:
        released = 0
        async with SessionLocal() as session:
            candidates = (
                await session.scalars(
                    select(Payment).where(
                        Payment.is_security_deposit.is_(True),
                        Payment.status == PaymentStatus.SUCCEEDED.value,
                        Payment.released_at.is_(None),
                        Payment.held_until.isnot(None),
                        Payment.held_until <= datetime.now(UTC),
                    )
                )
            ).all()
            for payment in candidates:
                dispute = await session.scalar(
                    select(Dispute).where(
                        Dispute.booking_id == payment.booking_id,
                        Dispute.status.in_(["OPEN", "UNDER_REVIEW"]),
                    )
                )
                if dispute is not None:
                    continue
                booking = await session.get(Booking, payment.booking_id)
                damages = (booking.no_show_evidence or {}).get("damages_cents") if booking else 0
                if damages:
                    continue
                payment.released_at = datetime.now(UTC)
                payment.captured_cents = 0
                released += 1
            await session.commit()
        return {"released": released}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.payments.mark_payouts_in_transit")
def mark_payouts_in_transit() -> dict:
    """Marca payouts aprobados como en tránsito hacia la cuenta del proveedor."""
    import asyncio

    async def run() -> dict:
        moved = 0
        async with SessionLocal() as session:
            payouts = (
                await session.scalars(
                    select(Payout).where(Payout.status == PayoutStatus.PENDING.value)
                )
            ).all()
            for payout in payouts:
                payout.status = PayoutStatus.IN_TRANSIT.value
                moved += 1
            await session.commit()
        return {"in_transit": moved}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.payments.refund_duplicate_payment")
def refund_duplicate_payment(payment_id: str) -> dict:
    """Reembolsa un cobro duplicado detectado (edge case A7)."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            payment = await session.get(Payment, uuid.UUID(payment_id))
            if payment is None:
                return {"status": "not_found"}
            refund = await payment_service.create_refund(
                session,
                payment=payment,
                amount_cents=None,
                reason="duplicate_payment",
                initiated_by=None,
                notes="Cobro duplicado detectado por conciliación.",
                is_automatic=True,
                idempotency_key=f"duplicate:{payment.id}",
            )
            await session.commit()
            return {"payment_id": payment_id, "refund_id": str(refund.id)}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.payments.notify_payment_failure")
def notify_payment_failure(payment_id: str) -> dict:
    """Avisa al cliente de un cobro rechazado con la acción concreta a seguir."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            payment = await session.get(Payment, uuid.UUID(payment_id))
            if payment is None:
                return {"status": "not_found"}
            user = await session.get(User, payment.client_id)
            if user is None:
                return {"status": "no_user"}
            code = payment.failure_code or "card_declined"
            body = (
                "El banco rechazó el cobro. Prueba con otra tarjeta o paga por transferencia SPEI."
                if code in {"card_declined", "insufficient_funds"}
                else "No pudimos completar el cobro. Revisa tu método de pago e intenta de nuevo."
            )
            await notification_service.notify(
                session,
                user=user,
                kind=NotificationKind.PAYMENT_FAILED,
                title="No pudimos cobrar tu reserva",
                body=body,
                payload={"payment_id": str(payment.id), "code": code},
                deep_link=f"quinta://bookings/{payment.booking_id}/pay",
                dedupe_key=f"payment-failed:{payment.id}",
                urgent=True,
            )
            await session.commit()
            return {"notified": True}

    return asyncio.run(run())
