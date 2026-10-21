"""Pruebas unitarias de la máquina de estados de Booking (sin base de datos).

Tabla de transiciones válidas + guardas + actores. Es la prueba que impide que un cambio
de UI habilite un cambio de estado que el dominio no permite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError, ErrorCode
from app.models.enums import BookingState as S
from app.services.state_machine import (
    ADMIN,
    CLIENT,
    PROVIDER,
    SYSTEM,
    GuardContext,
    allowed_transitions,
    assert_transition,
    is_occupying,
    is_terminal,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def ctx(actor: str = CLIENT, **overrides) -> GuardContext:
    base = {
        "now": NOW,
        "actor": actor,
        "start_at": NOW + timedelta(days=30),
        "end_at": NOW + timedelta(days=30, hours=11),
        "total_cents": 1_166_250,
        "paid_cents": 0,
    }
    base.update(overrides)
    return GuardContext(**base)


# ── Transiciones válidas del camino feliz ─────────────────────────────────────


@pytest.mark.parametrize(
    ("from_state", "to_state", "actor"),
    [
        (S.DRAFT, S.HOLD, CLIENT),
        (S.HOLD, S.PENDING_PAYMENT, CLIENT),
        (S.HOLD, S.CONFIRMED, SYSTEM),
        (S.PENDING_PAYMENT, S.CONFIRMED, SYSTEM),
        (S.CONFIRMED, S.COMPLETED, PROVIDER),
        (S.CONFIRMED, S.CANCELLED_BY_CLIENT, CLIENT),
        (S.CONFIRMED, S.CANCELLED_BY_PROVIDER, PROVIDER),
        (S.CONFIRMED, S.NO_SHOW, PROVIDER),
        (S.CONFIRMED, S.DISPUTED, CLIENT),
        (S.COMPLETED, S.DISPUTED, CLIENT),
        (S.DISPUTED, S.COMPLETED, ADMIN),
        (S.DISPUTED, S.REFUNDED, ADMIN),
        (S.HOLD, S.REFUNDED, SYSTEM),
    ],
)
def test_transiciones_validas(from_state: S, to_state: S, actor: str) -> None:
    extra = {"has_evidence": True} if to_state is S.NO_SHOW else {}
    transition = assert_transition(from_state, to_state, ctx(actor, **extra))
    assert transition.to_state is to_state
    assert actor in transition.actors


# ── Transiciones prohibidas ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (S.DRAFT, S.CONFIRMED),          # hay que apartar antes
        (S.DRAFT, S.COMPLETED),
        (S.HOLD, S.COMPLETED),           # no se puede completar un apartado
        (S.CANCELLED_BY_CLIENT, S.CONFIRMED),  # terminal
        (S.REFUNDED, S.CONFIRMED),
        (S.NO_SHOW, S.COMPLETED),
        (S.COMPLETED, S.CANCELLED_BY_CLIENT),  # ya pasó el evento
        (S.PENDING_PAYMENT, S.NO_SHOW),
    ],
)
def test_transiciones_prohibidas(from_state: S, to_state: S) -> None:
    with pytest.raises(AppError) as exc:
        assert_transition(from_state, to_state, ctx(ADMIN))
    assert exc.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert exc.value.details["allowed"] is not None


def test_actor_sin_permiso_no_puede_completar() -> None:
    """El cliente no puede cerrar el evento: eso es del anfitrión."""
    with pytest.raises(AppError) as exc:
        assert_transition(S.CONFIRMED, S.COMPLETED, ctx(CLIENT))
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_cliente_no_puede_cancelar_evento_ya_iniciado() -> None:
    """Cancelar después de que empezó el evento exige no-show o disputa (guarda `_guard_future`)."""
    started = ctx(CLIENT, start_at=NOW - timedelta(hours=2), end_at=NOW + timedelta(hours=9))
    with pytest.raises(AppError) as exc:
        assert_transition(S.CONFIRMED, S.CANCELLED_BY_CLIENT, started)
    assert "ya comenzó" in exc.value.message


def test_no_show_exige_evidencia() -> None:
    with pytest.raises(AppError) as exc:
        assert_transition(S.CONFIRMED, S.NO_SHOW, ctx(PROVIDER, has_evidence=False))
    assert "evidencia" in exc.value.message

    assert_transition(S.CONFIRMED, S.NO_SHOW, ctx(PROVIDER, has_evidence=True))


def test_disputa_exige_motivo() -> None:
    with pytest.raises(AppError) as exc:
        assert_transition(S.CONFIRMED, S.DISPUTED, ctx(CLIENT, reason=""))
    assert "motivo" in exc.value.message


def test_repetir_estado_es_idempotente_no_error_destructivo() -> None:
    """Repetir la misma transición responde 200 con `idempotent: true`, no rompe la app."""
    with pytest.raises(AppError) as exc:
        assert_transition(S.CONFIRMED, S.CONFIRMED, ctx(SYSTEM))
    assert exc.value.status_code == 200
    assert exc.value.details["idempotent"] is True


# ── Propiedades del dominio ───────────────────────────────────────────────────


def test_estados_que_ocupan_calendario() -> None:
    """Estos cuatro estados son los mismos que la cláusula WHERE de la constraint de exclusión."""
    assert {s for s in S if is_occupying(s)} == {
        S.HOLD,
        S.PENDING_PAYMENT,
        S.CONFIRMED,
        S.COMPLETED,
    }


def test_estados_terminales() -> None:
    assert is_terminal(S.REFUNDED)
    assert is_terminal(S.NO_SHOW)
    assert not is_terminal(S.CONFIRMED)


def test_ui_solo_ofrece_transiciones_permitidas() -> None:
    """`allowed_transitions` alimenta los botones: cliente y proveedor ven acciones distintas."""
    client_actions = set(allowed_transitions(S.CONFIRMED, CLIENT))
    provider_actions = set(allowed_transitions(S.CONFIRMED, PROVIDER))
    assert S.CANCELLED_BY_CLIENT in client_actions
    assert S.CANCELLED_BY_CLIENT not in provider_actions
    assert S.COMPLETED in provider_actions
    assert S.NO_SHOW in provider_actions
    assert S.COMPLETED not in client_actions
