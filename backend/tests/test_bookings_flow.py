"""Integración: apartado, idempotencia, concurrencia y conversión a reserva.

Estas pruebas necesitan Postgres con PostGIS: la garantía anti-solapamiento vive en la
constraint de exclusión y no existe en SQLite.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode
from app.models.booking import Booking, BookingHold
from app.models.enums import BookingState, HoldStatus
from app.models.user import User

pytestmark = pytest.mark.integration


def hold_payload(venue_id: uuid.UUID, days: int = 45) -> dict:
    start = (datetime.now(UTC) + timedelta(days=days)).replace(hour=15, minute=0, second=0, microsecond=0)
    return {
        "venue_id": str(venue_id),
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=11)).isoformat(),
        "guests": 80,
        "slot_kind": "FULL_DAY",
        "extras": [],
    }


async def test_hold_devuelve_folio_snapshot_y_ttl(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    response = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["folio"].startswith("HD-")
    assert body["status"] == "ACTIVE"
    assert body["seconds_remaining"] > 800
    assert body["quote_snapshot"]["engine_version"]
    assert body["total_cents"] == body["quote"]["total_cents"]
    assert body["can_extend"] is True
    assert body["next_step"] == "payment"


async def test_hold_sin_idempotency_key_es_rechazado(client: AsyncClient, venue, auth_headers: dict) -> None:
    response = await client.post("/api/v1/bookings/hold", json=hold_payload(venue.id), headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.IDEMPOTENCY_KEY_REQUIRED.value


async def test_reintento_con_misma_llave_devuelve_la_misma_respuesta(
    client: AsyncClient, venue, auth_headers: dict
) -> None:
    """El cliente reintenta por red inestable: no se crean dos apartados."""
    key = str(uuid.uuid4())
    headers = {**auth_headers, "Idempotency-Key": key}
    payload = hold_payload(venue.id, days=60)

    first = await client.post("/api/v1/bookings/hold", json=payload, headers=headers)
    second = await client.post("/api/v1/bookings/hold", json=payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["idempotent_replay"] is True


async def test_misma_llave_con_payload_distinto_es_conflicto(
    client: AsyncClient, venue, auth_headers: dict
) -> None:
    key = str(uuid.uuid4())
    headers = {**auth_headers, "Idempotency-Key": key}
    await client.post("/api/v1/bookings/hold", json=hold_payload(venue.id, days=61), headers=headers)
    other = hold_payload(venue.id, days=62)
    other["guests"] = 40
    response = await client.post("/api/v1/bookings/hold", json=other, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.IDEMPOTENCY_KEY_REUSED.value


async def test_dos_clientes_compiten_por_el_mismo_slot(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    """Edge case A1: uno gana con 201, el otro recibe 409 VENUE_NOT_AVAILABLE con alternativas."""
    payload = hold_payload(venue.id, days=70)

    first = await client.post(
        "/api/v1/bookings/hold",
        json=payload,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    second = await client.post(
        "/api/v1/bookings/hold",
        json=payload,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == ErrorCode.VENUE_NOT_AVAILABLE.value
    assert "conflicts" in error["details"]
    assert "suggested" in error["details"]


async def test_holds_solapados_son_imposibles_a_nivel_db(
    session: AsyncSession, venue, client_user: User
) -> None:
    """La constraint de exclusión rechaza el solape incluso saltándose la API."""
    from sqlalchemy.exc import IntegrityError

    start = datetime.now(UTC) + timedelta(days=90)
    first = BookingHold(
        folio="HD-AAA111",
        venue_id=venue.id,
        client_id=client_user.id,
        start_at=start,
        end_at=start + timedelta(hours=10),
        venue_timezone=venue.timezone,
        guests=50,
        quote_snapshot={"totals": {"total_cents": 1}},
        total_cents=1000,
        deposit_cents=300,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(first)
    await session.flush()

    overlap = BookingHold(
        folio="HD-BBB222",
        venue_id=venue.id,
        client_id=client_user.id,
        start_at=start + timedelta(hours=5),
        end_at=start + timedelta(hours=12),
        venue_timezone=venue.timezone,
        guests=30,
        quote_snapshot={"totals": {"total_cents": 1}},
        total_cents=1000,
        deposit_cents=300,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(overlap)
    with pytest.raises(IntegrityError) as exc:
        await session.flush()
    assert "holds_no_overlap" in str(exc.value)


async def test_buffer_impide_eventos_pegados(
    client: AsyncClient, venue, auth_headers: dict
) -> None:
    """El buffer de limpieza (60 min) bloquea un evento que empieza justo al terminar el anterior."""
    start = (datetime.now(UTC) + timedelta(days=100)).replace(hour=10, minute=0, second=0, microsecond=0)
    first_payload = {
        "venue_id": str(venue.id),
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=8)).isoformat(),
        "guests": 40,
        "slot_kind": "FULL_DAY",
        "extras": [],
    }
    first = await client.post(
        "/api/v1/bookings/hold",
        json=first_payload,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert first.status_code == 201

    back_to_back = {
        **first_payload,
        "start_at": (start + timedelta(hours=8)).isoformat(),
        "end_at": (start + timedelta(hours=16)).isoformat(),
    }
    second = await client.post(
        "/api/v1/bookings/hold",
        json=back_to_back,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == ErrorCode.VENUE_NOT_AVAILABLE.value


async def test_hold_expirado_no_se_puede_confirmar(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    """Edge case A2: si el TTL venció, la confirmación falla con 410 HOLD_EXPIRED."""
    created = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=110),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    hold_id = created.json()["id"]

    hold = await session.get(BookingHold, uuid.UUID(hold_id))
    hold.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold_id, "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == ErrorCode.HOLD_EXPIRED.value


async def test_confirmar_apartado_crea_booking_y_libera_el_hold(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    """ADR-003: el hold pasa a CONVERTED y el slot queda cubierto por la reserva."""
    created = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=120),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    hold_id = created.json()["id"]

    response = await client.post(
        "/api/v1/bookings",
        json={"hold_id": hold_id, "payment_method": "cash_at_venue", "accept_contract": True},
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    booking = response.json()
    assert booking["folio"].startswith("QX-")
    assert booking["state"] == BookingState.CONFIRMED.value
    assert booking["qr_token"] is not None
    assert booking["blocked_from"] < booking["start_at"]  # buffer aplicado

    hold = await session.get(BookingHold, uuid.UUID(hold_id))
    assert hold.status == HoldStatus.CONVERTED.value
    assert str(hold.converted_booking_id) == booking["id"]


async def test_cancelacion_aplica_politica_y_reembolso(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    """Cancelar 45 días antes ⇒ 100%; cerca del evento ⇒ 0% con explicación clara."""
    created = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=45),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking = await client.post(
        "/api/v1/bookings",
        json={
            "hold_id": created.json()["id"],
            "payment_method": "cash_at_venue",
            "accept_contract": True,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking_id = booking.json()["id"]

    preview = await client.get(f"/api/v1/bookings/{booking_id}/refund-preview", headers=auth_headers)
    assert preview.status_code == 200
    assert preview.json()["tier_percent"] == 100

    cancelled = await client.post(
        f"/api/v1/bookings/{booking_id}/cancel",
        json={
            "reason": "cambiamos de fecha por el clima",
            "reason_code": "weather",
            "request_refund": True,
            "acknowledge_policy": True,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == BookingState.CANCELLED_BY_CLIENT.value
    assert cancelled.json()["refund_tier_percent"] == 100

    # El slot se libera: otra persona puede apartar la misma fecha
    again = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=45),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert again.status_code == 201


async def test_reprogramacion_no_permite_solape(
    client: AsyncClient, venue, auth_headers: dict
) -> None:
    first_hold = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=130),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    first_booking = await client.post(
        "/api/v1/bookings",
        json={
            "hold_id": first_hold.json()["id"],
            "payment_method": "cash_at_venue",
            "accept_contract": True,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    second_hold = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=140),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    second_booking = await client.post(
        "/api/v1/bookings",
        json={
            "hold_id": second_hold.json()["id"],
            "payment_method": "cash_at_venue",
            "accept_contract": True,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    target = datetime.fromisoformat(second_booking.json()["start_at"])
    response = await client.post(
        f"/api/v1/bookings/{first_booking.json()['id']}/reschedule",
        json={
            "new_start_at": target.isoformat(),
            "new_end_at": (target + timedelta(hours=11)).isoformat(),
            "reason": "cambio de fecha",
        },
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.RESCHEDULE_TARGET_UNAVAILABLE.value


async def test_limite_de_apartados_activos(client: AsyncClient, venue, auth_headers: dict) -> None:
    """Edge case F30: un usuario no puede bloquear el calendario con holds infinitos."""
    from app.core.config import settings

    for offset in range(settings.hold_max_active_per_user):
        response = await client.post(
            "/api/v1/bookings/hold",
            json=hold_payload(venue.id, days=200 + offset * 3),
            headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 201

    blocked = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=260),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == ErrorCode.HOLD_LIMIT_REACHED.value


async def test_el_cliente_no_ve_reservas_de_otros(
    client: AsyncClient, venue, auth_headers: dict, session: AsyncSession
) -> None:
    """RBAC: el ID de la ruta no autoriza. Otro usuario recibe 403."""
    from app.core.security import create_access_token, hash_password
    from app.models.enums import Role, UserStatus

    created = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=300),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    booking = await client.post(
        "/api/v1/bookings",
        json={
            "hold_id": created.json()["id"],
            "payment_method": "cash_at_venue",
            "accept_contract": True,
        },
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    intruder = User(
        email="intruso@example.com",
        email_normalized="intruso@example.com",
        hashed_password=hash_password("Palapa2026!"),
        role=Role.CLIENT.value,
        status=UserStatus.ACTIVE.value,
        full_name="Intruso Curioso",
    )
    session.add(intruder)
    await session.commit()

    response = await client.get(
        f"/api/v1/bookings/{booking.json()['id']}",
        headers={"Authorization": f"Bearer {create_access_token(intruder.id, intruder.role.value)}"},
    )
    assert response.status_code == 403


async def test_hold_extension_una_sola_vez(client: AsyncClient, venue, auth_headers: dict) -> None:
    created = await client.post(
        "/api/v1/bookings/hold",
        json=hold_payload(venue.id, days=320),
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    hold_id = created.json()["id"]

    first = await client.post(f"/api/v1/bookings/holds/{hold_id}/extend", headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["can_extend"] is False

    second = await client.post(f"/api/v1/bookings/holds/{hold_id}/extend", headers=auth_headers)
    assert second.status_code == 410
