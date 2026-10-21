"""Integración: webhooks de pago (firma, duplicados, desorden y reconciliación)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode
from app.models.booking import Booking, BookingHold
from app.models.enums import BookingState, PaymentKind, PaymentStatus
from app.models.payment import Payment
from app.services import payments as payment_service


class FakeGateway:
    """Gateway de pruebas: firma HMAC propia, sin red."""

    name = "fake"

    def __init__(self) -> None:
        self.secret = "whsec_test_123"

    async def create_intent(self, **kwargs):  # type: ignore[no-untyped-def]
        return payment_service.IntentResult(
            provider_intent_id=f"pi_test_{uuid.uuid4().hex[:10]}",
            client_secret="pi_secret_test",
            status=PaymentStatus.REQUIRES_PAYMENT_METHOD,
        )

    async def refund(self, *, provider_intent_id: str, amount_cents: int, reason: str, idempotency_key: str):  # type: ignore[no-untyped-def]
        return payment_service.RefundResult(
            provider_refund_id=f"re_test_{uuid.uuid4().hex[:10]}", status=payment_service.RefundStatus.SUCCEEDED
        )

    async def cancel_intent(self, *, provider_intent_id: str) -> None:
        return None

    def verify_webhook(self, payload: bytes, signature: str) -> dict:
        from app.core.security import verify_signature

        if not verify_signature(self.secret, payload, signature):
            from app.core.errors import AppError

            raise AppError(ErrorCode.WEBHOOK_SIGNATURE_INVALID)
        return json.loads(payload)


def signed_payload(gateway: FakeGateway, body: dict) -> tuple[bytes, str]:
    from app.core.security import sign_payload

    raw = json.dumps(body).encode()
    return raw, sign_payload(gateway.secret, raw)


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


async def _booking_in_hold(client: AsyncClient, venue, auth_headers: dict, days: int = 45) -> dict:
    start = (datetime.now(UTC) + timedelta(days=days)).replace(hour=15, minute=0, second=0, microsecond=0)
    created = await client.post(
        "/api/v1/bookings/hold",
        json={
            "venue_id": str(venue.id),
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=11)).isoformat(),
            "guests": 80,
            "slot_kind": "FULL_DAY",
            "extras": [],
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    return created.json()


async def test_webhook_sin_firma_es_rechazado(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/payments/webhook",
        content=b'{"type":"payment_intent.succeeded"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.WEBHOOK_SIGNATURE_INVALID.value


async def test_webhook_con_firma_incorrecta_es_rechazado(client: AsyncClient, fake_gateway: FakeGateway) -> None:
    response = await client.post(
        "/api/v1/payments/webhook",
        content=b'{"type":"payment_intent.succeeded"}',
        headers={"Content-Type": "application/json", "Stripe-Signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 400


async def test_pago_confirmado_por_webhook_confirma_la_reserva(
    client: AsyncClient,
    venue,
    auth_headers: dict,
    session: AsyncSession,
    fake_gateway: FakeGateway,
) -> None:
    """El flujo real: intent creado por la API, confirmación por webhook firmado."""
    hold = await _booking_in_hold(client, venue, auth_headers, days=45)
    booking = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold["id"], "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = uuid.UUID(booking.json()["id"])

    # Se simula el intent ya creado en el PSP
    payment = Payment(
        booking_id=booking_id,
        client_id=(await session.get(Booking, booking_id)).client_id,
        provider_id=(await session.get(Booking, booking_id)).provider_id,
        kind=PaymentKind.DEPOSIT.value,
        status=PaymentStatus.PROCESSING.value,
        amount_cents=350_000,
        application_fee_cents=26_250,
        transfer_cents=323_750,
        provider_intent_id="pi_test_confirm",
    )
    session.add(payment)
    target = await session.get(Booking, booking_id)
    target.state = BookingState.PENDING_PAYMENT.value
    await session.commit()

    body = {
        "id": "evt_test_1",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_test_confirm", "latest_charge": "ch_test_1"}},
    }
    raw, signature = signed_payload(fake_gateway, body)
    response = await client.post(
        "/api/v1/payments/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "Stripe-Signature": signature},
    )
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "processed"

    await session.refresh(target)
    assert target.state == BookingState.CONFIRMED.value
    assert target.paid_cents == 350_000


async def test_webhook_duplicado_no_cobra_dos_veces(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession, fake_gateway: FakeGateway
) -> None:
    """Edge case A8: el mismo evento entregado dos veces se ignora la segunda vez."""
    hold = await _booking_in_hold(client, venue, auth_headers, days=50)
    booking = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold["id"], "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = uuid.UUID(booking.json()["id"])
    target = await session.get(Booking, booking_id)
    payment = Payment(
        booking_id=booking_id,
        client_id=target.client_id,
        provider_id=target.provider_id,
        kind=PaymentKind.DEPOSIT.value,
        status=PaymentStatus.PROCESSING.value,
        amount_cents=350_000,
        provider_intent_id="pi_test_dup",
    )
    session.add(payment)
    await session.commit()

    body = {
        "id": "evt_test_dup",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_test_dup"}},
    }
    raw, signature = signed_payload(fake_gateway, body)
    headers = {"Content-Type": "application/json", "Stripe-Signature": signature}

    first = await client.post("/api/v1/payments/webhook", content=raw, headers=headers)
    await session.refresh(target)
    paid_after_first = target.paid_cents

    second = await client.post("/api/v1/payments/webhook", content=raw, headers=headers)
    await session.refresh(target)

    assert first.status_code == second.status_code == 200
    assert second.json()["duplicated"] is True
    assert target.paid_cents == paid_after_first


async def test_pago_tardio_sobre_hold_expirado_se_reconcilia(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession, fake_gateway: FakeGateway
) -> None:
    """Edge case A2: hold vencido + pago exitoso ⇒ REFUNDED y acción `reconciled`."""
    hold = await _booking_in_hold(client, venue, auth_headers, days=55)
    booking = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold["id"], "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = uuid.UUID(booking.json()["id"])
    target = await session.get(Booking, booking_id)
    target.state = BookingState.HOLD.value

    stored_hold = await session.get(BookingHold, uuid.UUID(hold["id"]))
    stored_hold.status = "EXPIRED"
    stored_hold.expires_at = datetime.now(UTC) - timedelta(minutes=5)

    payment = Payment(
        booking_id=booking_id,
        client_id=target.client_id,
        provider_id=target.provider_id,
        kind=PaymentKind.DEPOSIT.value,
        status=PaymentStatus.PROCESSING.value,
        amount_cents=350_000,
        provider_intent_id="pi_test_late",
    )
    session.add(payment)
    await session.commit()

    body = {
        "id": "evt_test_late",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_test_late"}},
    }
    raw, signature = signed_payload(fake_gateway, body)
    response = await client.post(
        "/api/v1/payments/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "Stripe-Signature": signature},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "reconciled"

    await session.refresh(target)
    await session.refresh(payment)
    assert target.state == BookingState.REFUNDED.value
    assert payment.reconciliation_action == "refund_hold_expired"


async def test_pago_duplicado_en_la_api_responde_409(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    """Edge case A7: reintento del cliente con un pago ya cobrado."""
    hold = await _booking_in_hold(client, venue, auth_headers, days=65)
    booking = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold["id"], "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = uuid.UUID(booking.json()["id"])
    target = await session.get(Booking, booking_id)
    session.add(
        Payment(
            booking_id=booking_id,
            client_id=target.client_id,
            provider_id=target.provider_id,
            kind=PaymentKind.DEPOSIT.value,
            status=PaymentStatus.SUCCEEDED.value,
            amount_cents=target.deposit_cents,
            provider_intent_id="pi_test_already",
        )
    )
    await session.commit()

    response = await client.post(
        "/api/v1/payments/intent",
        json={"booking_id": str(booking_id), "kind": "DEPOSIT", "method": "card"},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.PAYMENT_DUPLICATED.value


async def test_reembolso_no_puede_exceder_el_pago(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    hold = await _booking_in_hold(client, venue, auth_headers, days=75)
    booking = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold["id"], "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = uuid.UUID(booking.json()["id"])
    target = await session.get(Booking, booking_id)
    payment = Payment(
        booking_id=booking_id,
        client_id=target.client_id,
        provider_id=target.provider_id,
        kind=PaymentKind.DEPOSIT.value,
        status=PaymentStatus.SUCCEEDED.value,
        amount_cents=100_000,
        provider_intent_id="pi_test_refund",
    )
    session.add(payment)
    await session.commit()

    response = await client.post(
        "/api/v1/refunds",
        json={
            "payment_id": str(payment.id),
            "amount_cents": 150_000,
            "reason": "goodwill",
            "apply_policy": False,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    # El cliente no puede forzar reembolsos fuera de política
    assert response.status_code in {403, 422}
