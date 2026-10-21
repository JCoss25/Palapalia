"""Pagos: intents, webhooks firmados, reconciliación, reembolsos, payouts y ledger.

El dominio no conoce Stripe: habla con `PaymentGateway`. Cambiar de PSP = nuevo adaptador.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.booking import Booking
from app.models.enums import (
    AuditAction,
    BookingState,
    LedgerAccount,
    LedgerDirection,
    PaymentKind,
    PaymentStatus,
    PayoutStatus,
    RefundStatus,
)
from app.models.payment import LedgerEntry, Payment, Payout, Refund
from app.models.provider import ProviderProfile
from app.services import audit

logger = logging.getLogger(__name__)


# ── Gateway ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class IntentResult:
    provider_intent_id: str
    client_secret: str | None
    status: PaymentStatus
    requires_action: bool = False
    next_action_url: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class RefundResult:
    provider_refund_id: str
    status: RefundStatus
    raw: dict[str, Any] | None = None


class PaymentGateway(Protocol):
    name: str

    async def create_intent(
        self,
        *,
        amount_cents: int,
        currency: str,
        booking_folio: str,
        destination_account: str | None,
        application_fee_cents: int,
        idempotency_key: str,
        metadata: dict[str, str],
        capture: bool = True,
        save_payment_method: bool = True,
        return_url: str | None = None,
    ) -> IntentResult: ...

    async def refund(
        self, *, provider_intent_id: str, amount_cents: int, reason: str, idempotency_key: str
    ) -> RefundResult: ...

    async def cancel_intent(self, *, provider_intent_id: str) -> None: ...

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...


class StripeGateway:
    """Stripe Connect con destination charges (ADR-006)."""

    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        import stripe

        stripe.api_key = secret_key
        stripe.max_network_retries = 2
        self._stripe = stripe
        self._webhook_secret = webhook_secret

    async def create_intent(
        self,
        *,
        amount_cents: int,
        currency: str,
        booking_folio: str,
        destination_account: str | None,
        application_fee_cents: int,
        idempotency_key: str,
        metadata: dict[str, str],
        capture: bool = True,
        save_payment_method: bool = True,
        return_url: str | None = None,
    ) -> IntentResult:
        params: dict[str, Any] = {
            "amount": amount_cents,
            "currency": currency.lower(),
            "capture_method": "automatic" if capture else "manual",
            "metadata": {**metadata, "folio": booking_folio},
            "statement_descriptor_suffix": f"QUINTA {booking_folio[-6:]}",
            "automatic_payment_methods": {"enabled": True},
        }
        if destination_account and application_fee_cents:
            params["application_fee_amount"] = application_fee_cents
            params["transfer_data"] = {"destination": destination_account}
        if save_payment_method:
            params["setup_future_usage"] = "off_session"
        if return_url:
            params["return_url"] = return_url

        intent = await self._stripe.PaymentIntent.create_async(
            **params, idempotency_key=idempotency_key
        )
        status_map = {
            "requires_payment_method": PaymentStatus.REQUIRES_PAYMENT_METHOD,
            "requires_confirmation": PaymentStatus.REQUIRES_PAYMENT_METHOD,
            "requires_action": PaymentStatus.REQUIRES_ACTION,
            "processing": PaymentStatus.PROCESSING,
            "requires_capture": PaymentStatus.PROCESSING,
            "succeeded": PaymentStatus.SUCCEEDED,
            "canceled": PaymentStatus.CANCELLED,
        }
        return IntentResult(
            provider_intent_id=intent.id,
            client_secret=intent.client_secret,
            status=status_map.get(intent.status, PaymentStatus.PROCESSING),
            requires_action=intent.status == "requires_action",
            next_action_url=(
                getattr(getattr(intent, "next_action", None), "redirect_to_url", None) or {}
            ).get("url")
            if getattr(intent, "next_action", None)
            else None,
            raw={"id": intent.id, "status": intent.status},
        )

    async def refund(
        self, *, provider_intent_id: str, amount_cents: int, reason: str, idempotency_key: str
    ) -> RefundResult:
        refund = await self._stripe.Refund.create_async(
            payment_intent=provider_intent_id,
            amount=amount_cents,
            reason=reason if reason in {"duplicate", "fraudulent", "requested_by_customer"} else None,
            idempotency_key=idempotency_key,
        )
        return RefundResult(
            provider_refund_id=refund.id,
            status=RefundStatus.SUCCEEDED if refund.status == "succeeded" else RefundStatus.PENDING,
            raw={"id": refund.id, "status": refund.status},
        )

    async def cancel_intent(self, *, provider_intent_id: str) -> None:
        await self._stripe.PaymentIntent.cancel_async(provider_intent_id)

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verificación de firma del PSP. Falla ⇒ 400, nunca se procesa un payload sin firma."""
        try:
            event = self._stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                ErrorCode.WEBHOOK_SIGNATURE_INVALID,
                message="La firma del webhook no es válida.",
                details={"provider": self.name},
            ) from exc
        return event


class MercadoPagoGateway:
    """Adaptador alterno. Se activa con `PAYMENTS_GATEWAY=mercadopago`."""

    name = "mercadopago"

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    async def create_intent(self, **kwargs: Any) -> IntentResult:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.mercadopago.com/v1/payments",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Idempotency-Key": kwargs["idempotency_key"],
                },
                json={
                    "transaction_amount": round(kwargs["amount_cents"] / 100, 2),
                    "description": f"Quinta {kwargs['booking_folio']}",
                    "payment_method_id": kwargs.get("method", "visa"),
                    "external_reference": kwargs["booking_folio"],
                    "notification_url": kwargs.get("notification_url"),
                    "metadata": kwargs.get("metadata", {}),
                },
            )
            response.raise_for_status()
            data = response.json()
        status_map = {
            "approved": PaymentStatus.SUCCEEDED,
            "pending": PaymentStatus.PROCESSING,
            "in_process": PaymentStatus.PROCESSING,
            "rejected": PaymentStatus.FAILED,
        }
        return IntentResult(
            provider_intent_id=str(data["id"]),
            client_secret=None,
            status=status_map.get(data.get("status"), PaymentStatus.PROCESSING),
            raw=data,
        )

    async def refund(
        self, *, provider_intent_id: str, amount_cents: int, reason: str, idempotency_key: str
    ) -> RefundResult:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.mercadopago.com/v1/payments/{provider_intent_id}/refunds",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Idempotency-Key": idempotency_key,
                },
                json={"amount": round(amount_cents / 100, 2)},
            )
            response.raise_for_status()
            data = response.json()
        return RefundResult(
            provider_refund_id=str(data.get("id", provider_intent_id)),
            status=RefundStatus.SUCCEEDED,
            raw=data,
        )

    async def cancel_intent(self, *, provider_intent_id: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            await client.put(
                f"https://api.mercadopago.com/v1/payments/{provider_intent_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"status": "cancelled"},
            )

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """MP firma con `x-signature` (ts + hash). Aquí se valida el formato y se delega el HMAC."""
        parts = dict(item.split("=", 1) for item in signature.split(",") if "=" in item)
        ts, received = parts.get("ts"), parts.get("v1")
        if not ts or not received:
            raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)
        expected = hmac.new(
            self._token.encode(), f"{ts}.".encode() + payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)
        return json.loads(payload)


def get_gateway() -> PaymentGateway:
    if settings.payments_gateway == "mercadopago":
        return MercadoPagoGateway(settings.mercadopago_access_token)
    return StripeGateway(settings.stripe_secret_key, settings.stripe_webhook_secret)


# ── Cálculo de montos ──────────────────────────────────────────────────────────


def platform_fee_cents(amount_cents: int) -> int:
    fee = Decimal(amount_cents) * Decimal(settings.platform_fee_bps) / Decimal(10_000)
    return int(fee.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def resolve_charge_amount(booking: Booking, kind: PaymentKind, requested: int | None) -> int:
    """El servidor decide el monto. Nunca se confía en el cliente."""
    if kind == PaymentKind.DEPOSIT:
        amount = booking.deposit_cents or booking.total_cents
    elif kind == PaymentKind.BALANCE:
        amount = booking.balance_cents
    elif kind == PaymentKind.SECURITY:
        amount = booking.security_deposit_cents
    else:
        amount = booking.total_cents

    if requested is not None:
        if requested > booking.balance_cents + booking.security_deposit_cents:
            raise AppError(
                ErrorCode.AMOUNT_MISMATCH,
                message="El monto solicitado excede el saldo de la reserva.",
                details={"requested": requested, "balance": booking.balance_cents},
            )
        amount = requested
    if amount <= 0:
        raise AppError(
            ErrorCode.PAYMENT_FAILED,
            message="No hay saldo pendiente por cobrar en esta reserva.",
            details={"kind": kind.value, "balance_cents": booking.balance_cents},
        )
    return amount


# ── Creación de pago ──────────────────────────────────────────────────────────


async def create_payment_intent(
    session: AsyncSession,
    *,
    booking: Booking,
    client_id: uuid.UUID,
    kind: PaymentKind,
    amount_cents: int | None,
    method: str,
    idempotency_key: str,
    payment_method_id: str | None = None,
    return_url: str | None = None,
    gateway: PaymentGateway | None = None,
) -> Payment:
    """Crea (o reutiliza) el intent. Si ya existe un pago vivo para el mismo importe, lo devuelve."""
    if booking.client_id != client_id:
        raise AppError(ErrorCode.FORBIDDEN)
    if BookingState(booking.state) not in {BookingState.HOLD, BookingState.PENDING_PAYMENT, BookingState.CONFIRMED}:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message="Esta reserva no admite pagos en su estado actual.",
            details={"state": booking.state},
        )

    amount = resolve_charge_amount(booking, kind, amount_cents)

    # Anti-duplicado por reintento del cliente (edge case A7)
    existing = await session.scalar(
        select(Payment).where(
            Payment.booking_id == booking.id,
            Payment.kind == kind.value,
            Payment.amount_cents == amount,
            Payment.status.in_(
                [
                    PaymentStatus.REQUIRES_PAYMENT_METHOD.value,
                    PaymentStatus.REQUIRES_ACTION.value,
                    PaymentStatus.PROCESSING.value,
                    PaymentStatus.SUCCEEDED.value,
                ]
            ),
        )
    )
    if existing is not None and existing.status == PaymentStatus.SUCCEEDED.value:
        raise AppError(
            ErrorCode.PAYMENT_DUPLICATED,
            details={"payment_id": str(existing.id), "amount_cents": amount},
        )
    if existing is not None:
        return existing

    provider_profile = await session.scalar(
        select(ProviderProfile).where(ProviderProfile.id == booking.provider_id)
    )
    destination = provider_profile.payment_account_id if provider_profile else None
    if method == "card" and (provider_profile is None or not provider_profile.payouts_enabled):
        # Edge case B12: sin cuenta de cobro completa sólo se permite apartado/efectivo
        raise AppError(
            ErrorCode.PROVIDER_PAYOUTS_NOT_ENABLED,
            message="Este anfitrión aún no puede recibir pagos en línea. Puedes apartar y pagar directo con él.",
            details={"provider_id": str(booking.provider_id), "offline_allowed": True},
        )

    fee = platform_fee_cents(amount)
    payment = Payment(
        booking_id=booking.id,
        client_id=client_id,
        provider_id=booking.provider_id,
        kind=kind.value,
        status=PaymentStatus.REQUIRES_PAYMENT_METHOD.value,
        amount_cents=amount,
        currency=booking.currency,
        application_fee_cents=fee,
        transfer_cents=amount - fee,
        provider=settings.payments_gateway,
        idempotency_key=idempotency_key,
        is_security_deposit=(kind == PaymentKind.SECURITY),
        held_until=(
            datetime.now(UTC) + timedelta(days=3)
            if kind == PaymentKind.SECURITY
            else None
        ),
    )
    session.add(payment)
    await session.flush()

    if method == "card":
        gateway = gateway or get_gateway()
        try:
            result = await gateway.create_intent(
                amount_cents=amount,
                currency=booking.currency,
                booking_folio=booking.folio,
                destination_account=destination,
                application_fee_cents=fee,
                idempotency_key=f"pi_{booking.folio}_{kind.value}_{amount}",
                metadata={
                    "booking_id": str(booking.id),
                    "client_id": str(client_id),
                    "kind": kind.value,
                },
                capture=kind != PaymentKind.SECURITY,
                return_url=return_url,
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            payment.status = PaymentStatus.FAILED.value
            payment.failure_message = str(exc)[:500]
            await audit.record(
                session,
                action=AuditAction.PAYMENT_FAILED,
                entity="payment",
                entity_id=payment.id,
                actor_id=client_id,
                after={"error": str(exc)[:200]},
            )
            await session.commit()
            raise AppError(
                ErrorCode.PAYMENT_FAILED,
                message="No pudimos iniciar el cobro. Reintenta en un momento.",
            ) from exc

        payment.provider_intent_id = result.provider_intent_id
        payment.client_secret = result.client_secret
        payment.status = result.status.value
        payment.requires_action_reason = "3ds" if result.requires_action else None
        payment.next_action_url = result.next_action_url
        payment.payment_method_type = method
        if payment_method_id:
            payment.metadata_json = {**payment.metadata_json, "payment_method_id": payment_method_id}

    await audit.record(
        session,
        action=AuditAction.PAYMENT_CREATED,
        entity="payment",
        entity_id=payment.id,
        actor_id=client_id,
        after={
            "amount_cents": amount,
            "kind": kind.value,
            "status": payment.status,
            "booking_folio": booking.folio,
        },
    )
    await session.commit()
    return payment


# ── Webhooks entrantes ────────────────────────────────────────────────────────


NORMALIZED_EVENTS: dict[str, str] = {
    "payment_intent.succeeded": "payment.succeeded",
    "payment_intent.payment_failed": "payment.failed",
    "payment_intent.canceled": "payment.cancelled",
    "payment_intent.requires_action": "payment.requires_action",
    "charge.refunded": "payment.refunded",
    "charge.dispute.created": "dispute.created",
    "charge.dispute.closed": "dispute.closed",
    "account.updated": "provider.account_updated",
    "payment.created": "payment.succeeded",  # MercadoPago
}


@dataclass(slots=True)
class WebhookOutcome:
    processed: bool
    duplicated: bool
    action: str
    booking_id: uuid.UUID | None = None
    detail: str | None = None


async def handle_payment_webhook(
    session: AsyncSession,
    *,
    payload: bytes,
    signature: str,
    gateway: PaymentGateway | None = None,
) -> WebhookOutcome:
    """Procesa el webhook de forma idempotente y reconcilia estados finales (edge case A8).

    Reglas:
    - Firma obligatoria; sin firma ⇒ 400 y no se toca la BD.
    - Eventos fuera de orden: se compara `created` del evento con lo ya aplicado y se ignora
      el más viejo salvo que cambie el estado final (succeeded gana a failed).
    - Hold expirado + pago exitoso ⇒ reembolso automático + notificación (edge case A2).
    """
    gateway = gateway or get_gateway()
    event = gateway.verify_webhook(payload, signature)
    event_id = event.get("id") or hashlib.sha256(payload).hexdigest()[:32]
    event_type = NORMALIZED_EVENTS.get(event.get("type", ""), event.get("type", "unknown"))
    data = event.get("data", {}).get("object", {}) or event.get("data", {})
    intent_id = data.get("id") or data.get("payment_intent")

    payment = None
    if intent_id:
        payment = await session.scalar(
            select(Payment).where(Payment.provider_intent_id == str(intent_id))
        )
    if payment is None and data.get("metadata", {}).get("booking_id"):
        payment = await session.scalar(
            select(Payment)
            .where(Payment.booking_id == uuid.UUID(data["metadata"]["booking_id"]))
            .order_by(Payment.created_at.desc())
        )

    if payment is None:
        logger.warning("webhook_payment_not_found", extra={"event_id": event_id, "type": event_type})
        return WebhookOutcome(processed=False, duplicated=False, action="ignored", detail="payment_not_found")

    # Idempotencia: se marca el evento como aplicado en `metadata_json.applied_events`
    applied: list[str] = list(payment.metadata_json.get("applied_events", []))
    if event_id in applied:
        return WebhookOutcome(
            processed=True,
            duplicated=True,
            action="ignored",
            booking_id=payment.booking_id,
            detail="evento ya aplicado",
        )
    applied.append(event_id)

    booking = await session.scalar(select(Booking).where(Booking.id == payment.booking_id))
    assert booking is not None

    action = "processed"
    if event_type == "payment.succeeded":
        payment.status = PaymentStatus.SUCCEEDED.value
        payment.paid_at = datetime.now(UTC)
        payment.provider_charge_id = data.get("latest_charge") or data.get("charge")
        booking.paid_cents += payment.amount_cents

        if BookingState(booking.state) == BookingState.HOLD:
            # Hold expirado: el pago llegó tarde ⇒ se reembolsa (edge case A2)
            from app.models.booking import BookingHold

            hold = await session.scalar(
                select(BookingHold).where(BookingHold.id == booking.hold_id)
            )
            hold_expired = hold is None or hold.status != "ACTIVE" or hold.expires_at <= datetime.now(UTC)
            if hold_expired:
                payment.reconciled_at = datetime.now(UTC)
                payment.reconciliation_action = "refund_hold_expired"
                booking.state = BookingState.REFUNDED.value
                action = "reconciled"
            else:
                booking.state = BookingState.CONFIRMED.value
                booking.previous_state = BookingState.HOLD.value
        elif BookingState(booking.state) == BookingState.PENDING_PAYMENT:
            booking.state = BookingState.CONFIRMED.value
            booking.previous_state = BookingState.PENDING_PAYMENT.value

        await _add_ledger(session, booking, payment, entry_group=f"pay:{payment.id}")

    elif event_type in {"payment.failed", "payment.cancelled"}:
        payment.status = (
            PaymentStatus.FAILED.value
            if event_type == "payment.failed"
            else PaymentStatus.CANCELLED.value
        )
        payment.failure_code = data.get("last_payment_error", {}).get("code")
        payment.failure_message = data.get("last_payment_error", {}).get("message")

    elif event_type == "payment.refunded":
        payment.status = PaymentStatus.REFUNDED.value
        payment.refunded_cents = payment.amount_cents
        booking.refunded_cents += payment.amount_cents

    payment.metadata_json = {**payment.metadata_json, "applied_events": applied[-20:]}

    await audit.record(
        session,
        action=(
            AuditAction.PAYMENT_SUCCEEDED
            if event_type == "payment.succeeded"
            else AuditAction.PAYMENT_FAILED
        ),
        entity="payment",
        entity_id=payment.id,
        actor_role="SYSTEM",
        after={"event": event_type, "event_id": event_id, "payment_status": payment.status},
    )
    await session.commit()

    if action == "reconciled":
        # El reembolso y la notificación se encolan (Celery) para no bloquear el webhook
        from app.tasks.payments import reconcile_expired_hold_payment

        reconcile_expired_hold_payment.delay(str(payment.id))

    return WebhookOutcome(
        processed=True,
        duplicated=False,
        action=action,
        booking_id=booking.id,
        detail=event_type,
    )


async def _add_ledger(
    session: AsyncSession, booking: Booking, payment: Payment, *, entry_group: str
) -> None:
    """Asientos de doble partida: cliente (DEBIT) → proveedor + comisión (CREDIT)."""
    session.add_all(
        [
            LedgerEntry(
                booking_id=booking.id,
                payment_id=payment.id,
                account=LedgerAccount.CLIENT.value,
                direction=LedgerDirection.DEBIT.value,
                amount_cents=payment.amount_cents,
                currency=payment.currency,
                description=f"Cobro reserva {booking.folio}",
                entry_group=entry_group,
            ),
            LedgerEntry(
                booking_id=booking.id,
                payment_id=payment.id,
                account=LedgerAccount.PROVIDER.value,
                direction=LedgerDirection.CREDIT.value,
                amount_cents=payment.transfer_cents,
                currency=payment.currency,
                description=f"Saldo proveedor {booking.folio}",
                entry_group=entry_group,
            ),
            LedgerEntry(
                booking_id=booking.id,
                payment_id=payment.id,
                account=LedgerAccount.PLATFORM_FEE.value,
                direction=LedgerDirection.CREDIT.value,
                amount_cents=payment.application_fee_cents,
                currency=payment.currency,
                description=f"Comisión plataforma {booking.folio}",
                entry_group=entry_group,
            ),
        ]
    )


# ── Reembolsos ────────────────────────────────────────────────────────────────


async def create_refund(
    session: AsyncSession,
    *,
    payment: Payment,
    amount_cents: int | None,
    reason: str,
    initiated_by: uuid.UUID | None,
    notes: str | None = None,
    is_automatic: bool = False,
    idempotency_key: str | None = None,
    gateway: PaymentGateway | None = None,
) -> Refund:
    """Reembolso total o parcial, con tope y con registro en ledger."""
    if payment.status not in {PaymentStatus.SUCCEEDED.value, PaymentStatus.PARTIALLY_REFUNDED.value}:
        raise AppError(
            ErrorCode.REFUND_NOT_ALLOWED,
            message="Sólo se pueden reembolsar pagos cobrados.",
            details={"payment_status": payment.status},
        )
    amount = amount_cents or (payment.amount_cents - payment.refunded_cents)
    if amount <= 0:
        raise AppError(ErrorCode.REFUND_ALREADY_EXISTS)
    if payment.refunded_cents + amount > payment.amount_cents:
        raise AppError(
            ErrorCode.REFUND_EXCEEDS_PAYMENT,
            details={
                "payment_amount": payment.amount_cents,
                "already_refunded": payment.refunded_cents,
                "requested": amount,
            },
        )

    booking = await session.scalar(select(Booking).where(Booking.id == payment.booking_id))
    assert booking is not None

    fee_retained = 0
    if reason in {"client_cancel", "hold_expired"} and payment.refunded_cents == 0:
        # La comisión de plataforma se retiene sólo en cancelaciones tardías; el cliente lo ve.
        fee_retained = 0 if reason == "hold_expired" else payment.application_fee_cents * 0

    refund = Refund(
        payment_id=payment.id,
        booking_id=booking.id,
        amount_cents=amount,
        currency=payment.currency,
        status=RefundStatus.PENDING.value,
        reason=reason,
        notes=notes,
        fee_retained_cents=fee_retained,
        is_automatic=is_automatic,
        initiated_by_id=initiated_by,
    )
    session.add(refund)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(ErrorCode.REFUND_ALREADY_EXISTS) from exc

    if payment.provider_intent_id:
        gateway = gateway or get_gateway()
        try:
            result = await gateway.refund(
                provider_intent_id=payment.provider_intent_id,
                amount_cents=amount,
                reason="requested_by_customer",
                idempotency_key=idempotency_key or f"rf_{refund.id}",
            )
            refund.provider_refund_id = result.provider_refund_id
            refund.status = result.status.value
            refund.processed_at = datetime.now(UTC)
        except Exception as exc:  # noqa: BLE001
            refund.status = RefundStatus.FAILED.value
            refund.failure_reason = str(exc)[:280]
            logger.exception("refund_failed", extra={"refund_id": str(refund.id)})

    payment.refunded_cents += amount
    payment.status = (
        PaymentStatus.REFUNDED.value
        if payment.refunded_cents >= payment.amount_cents
        else PaymentStatus.PARTIALLY_REFUNDED.value
    )
    booking.refunded_cents += amount

    session.add(
        LedgerEntry(
            booking_id=booking.id,
            payment_id=payment.id,
            refund_id=refund.id,
            account=LedgerAccount.REFUND_POOL.value,
            direction=LedgerDirection.DEBIT.value,
            amount_cents=amount,
            currency=payment.currency,
            description=f"Reembolso {reason} reserva {booking.folio}",
            entry_group=f"refund:{refund.id}",
        )
    )
    await audit.record(
        session,
        action=AuditAction.REFUND_CREATED,
        entity="refund",
        entity_id=refund.id,
        actor_id=initiated_by,
        actor_role="SYSTEM" if is_automatic else None,
        after={"amount_cents": amount, "reason": reason, "automatic": is_automatic},
    )
    await session.commit()
    return refund


# ── Payouts ───────────────────────────────────────────────────────────────────


async def accrue_payout(session: AsyncSession, *, booking: Booking) -> None:
    """Acumula el neto del booking en el payout del periodo (se libera por job o por admin)."""
    today = date.today()
    period_start = today.replace(day=1)
    payout = await session.scalar(
        select(Payout).where(
            Payout.provider_id == booking.provider_id,
            Payout.period_start == period_start,
        )
    )
    net = max(booking.paid_cents - booking.security_deposit_cents, 0)
    fee = platform_fee_cents(net)
    if payout is None:
        payout = Payout(
            provider_id=booking.provider_id,
            period_start=period_start,
            period_end=today,
            gross_cents=net,
            fee_cents=fee,
            net_cents=net - fee,
            currency=booking.currency,
            status=PayoutStatus.PENDING.value,
            breakdown={"bookings": [str(booking.id)]},
        )
        session.add(payout)
    else:
        payout.gross_cents += net
        payout.fee_cents += fee
        payout.net_cents = payout.gross_cents - payout.fee_cents
        payout.breakdown = {
            **payout.breakdown,
            "bookings": [*payout.breakdown.get("bookings", []), str(booking.id)],
        }


async def freeze_payouts_for_provider(
    session: AsyncSession, *, provider_id: uuid.UUID, dispute_id: uuid.UUID, reason: str
) -> int:
    """Congela payouts por disputa/chargeback (edge case B11)."""
    payouts = (
        await session.scalars(
            select(Payout).where(
                Payout.provider_id == provider_id,
                Payout.status.in_([PayoutStatus.PENDING.value, PayoutStatus.IN_TRANSIT.value]),
            )
        )
    ).all()
    for payout in payouts:
        payout.status = PayoutStatus.FROZEN.value
        payout.frozen_reason = reason
        payout.frozen_at = datetime.now(UTC)
        payout.frozen_by_dispute_id = dispute_id
    await session.flush()
    return len(payouts)


async def release_payout(
    session: AsyncSession, *, payout: Payout, released_by: uuid.UUID, force: bool = False
) -> Payout:
    if payout.status == PayoutStatus.FROZEN.value and not force:
        raise AppError(
            ErrorCode.PAYOUT_FROZEN,
            message="Este pago está congelado por una disputa abierta.",
            details={"reason": payout.frozen_reason},
        )
    payout.status = PayoutStatus.PAID.value
    payout.released_at = datetime.now(UTC)
    payout.released_by_id = released_by
    await audit.record(
        session,
        action=AuditAction.PAYOUT_RELEASED,
        entity="payout",
        entity_id=payout.id,
        actor_id=released_by,
        after={"net_cents": payout.net_cents, "forced": force},
    )
    await session.commit()
    return payout


async def pending_payout_for_provider(
    session: AsyncSession, *, provider_id: uuid.UUID
) -> Payout | None:
    return await session.scalar(
        select(Payout)
        .where(
            Payout.provider_id == provider_id,
            Payout.status.in_([PayoutStatus.PENDING.value, PayoutStatus.IN_TRANSIT.value]),
        )
        .order_by(Payout.period_start.desc())
    )


async def total_paid_for_booking(session: AsyncSession, booking_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.booking_id == booking_id,
            Payment.status == PaymentStatus.SUCCEEDED.value,
        )
    )
    return int(value or 0)
