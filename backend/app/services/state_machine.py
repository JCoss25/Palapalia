"""Máquina de estados de Booking: tabla de transiciones, guardas y actores permitidos.

Regla de oro: **toda** transición pasa por `assert_transition()` y deja rastro en
`BookingStatusHistory` (con `idempotent_replay=True` si es un reintento).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from app.core.errors import AppError, ErrorCode
from app.models.enums import BookingState as S
from app.models.enums import Role

# ── Actores ────────────────────────────────────────────────────────────────────

CLIENT: Final = "CLIENT"
PROVIDER: Final = "PROVIDER"
ADMIN: Final = "ADMIN"
SYSTEM: Final = "SYSTEM"

ALL_ACTORS: Final = frozenset({CLIENT, PROVIDER, ADMIN, SYSTEM})


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Todo lo que una guarda puede necesitar. Sin acceso a BD: sólo datos ya cargados."""

    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    actor: str = SYSTEM
    is_instant_book: bool = False
    paid_cents: int = 0
    total_cents: int = 0
    start_at: datetime | None = None
    end_at: datetime | None = None
    check_in_at: datetime | None = None
    refund_tier_percent: int | None = None
    has_evidence: bool = False
    reason: str | None = None
    #: Ventana de gracia para check-in (minutos antes del inicio)
    checkin_window_minutes: int = 120

    @property
    def starts_in_hours(self) -> float | None:
        if not self.start_at:
            return None
        return (self.start_at - self.now).total_seconds() / 3600

    @property
    def has_started(self) -> bool:
        return bool(self.start_at and self.now >= self.start_at)

    @property
    def is_fully_paid(self) -> bool:
        return self.total_cents > 0 and self.paid_cents >= self.total_cents


def _guard_actor(*actors: str) -> Callable[[GuardContext], tuple[bool, str]]:
    def check(ctx: GuardContext) -> tuple[bool, str]:
        if ctx.actor not in actors:
            return False, f"El rol {ctx.actor} no puede ejecutar esta transición"
        return True, ""

    return check


def _guard_future(ctx: GuardContext) -> tuple[bool, str]:
    if ctx.has_started:
        return False, "El evento ya comenzó: usa check-in, no-show o disputa"
    return True, ""


def _guard_checkin_window(ctx: GuardContext) -> tuple[bool, str]:
    if not ctx.start_at:
        return False, "Reserva sin fecha"
    hours = ctx.starts_in_hours or 0
    if hours > ctx.checkin_window_minutes / 60:
        return False, "Todavía es muy temprano para hacer check-in"
    return True, ""


def _guard_evidence(ctx: GuardContext) -> tuple[bool, str]:
    if not ctx.has_evidence:
        return False, "Se requiere al menos una evidencia (foto, nota con testigos o registro)"
    return True, ""


def _guard_reason(ctx: GuardContext) -> tuple[bool, str]:
    if not ctx.reason or len(ctx.reason.strip()) < 5:
        return False, "Se requiere un motivo de al menos 5 caracteres"
    return True, ""


@dataclass(frozen=True, slots=True)
class Transition:
    to_state: S
    actors: frozenset[str]
    guards: tuple[Callable[[GuardContext], tuple[bool, str]], ...] = ()
    #: Efectos laterales declarativos (los ejecuta `booking_service`, no la máquina)
    effects: tuple[str, ...] = ()
    requires_idempotency_key: bool = False
    description: str = ""


def _t(
    to_state: S,
    actors: set[str],
    guards: tuple[Callable[[GuardContext], tuple[bool, str]], ...] = (),
    effects: tuple[str, ...] = (),
    description: str = "",
) -> Transition:
    return Transition(
        to_state=to_state,
        actors=frozenset(actors),
        guards=guards,
        effects=effects,
        description=description,
    )


# ── Tabla de transiciones válidas ──────────────────────────────────────────────

TRANSITIONS: Final[dict[S, dict[S, Transition]]] = {
    S.DRAFT: {
        S.HOLD: _t(
            S.HOLD,
            {CLIENT, SYSTEM},
            guards=(_guard_future,),
            effects=("reserve_slot", "start_hold_ttl"),
            description="Crear apartado desde el borrador",
        ),
        S.CANCELLED_BY_CLIENT: _t(S.CANCELLED_BY_CLIENT, {CLIENT, SYSTEM}),
    },
    S.HOLD: {
        S.PENDING_PAYMENT: _t(
            S.PENDING_PAYMENT,
            {CLIENT, SYSTEM},
            effects=("freeze_quote", "create_payment_intent"),
            description="Se inició el cobro; el slot sigue reservado",
        ),
        S.CONFIRMED: _t(
            S.CONFIRMED,
            {CLIENT, PROVIDER, SYSTEM},
            effects=("confirm_slot", "notify_both", "schedule_reminders", "emit_webhook"),
            description="Reserva inmediata pagada o aprobación del proveedor",
        ),
        S.CANCELLED_BY_CLIENT: _t(
            S.CANCELLED_BY_CLIENT,
            {CLIENT, ADMIN, SYSTEM},
            effects=("release_slot", "apply_refund_policy", "notify_provider"),
            description="El cliente abandona el apartado",
        ),
        S.CANCELLED_BY_PROVIDER: _t(
            S.CANCELLED_BY_PROVIDER,
            {PROVIDER, ADMIN, SYSTEM},
            effects=("release_slot", "full_refund", "penalize_provider"),
            description="El proveedor rechaza la solicitud",
        ),
        S.REFUNDED: _t(
            S.REFUNDED,
            {ADMIN, SYSTEM},
            effects=("full_refund",),
            description="Apartado expirado con pago tardío (edge case A2)",
        ),
    },
    S.PENDING_PAYMENT: {
        S.CONFIRMED: _t(
            S.CONFIRMED,
            {CLIENT, PROVIDER, SYSTEM},
            effects=("confirm_slot", "notify_both", "schedule_reminders", "emit_webhook"),
            description="Pago confirmado por webhook",
        ),
        S.CANCELLED_BY_CLIENT: _t(
            S.CANCELLED_BY_CLIENT,
            {CLIENT, ADMIN, SYSTEM},
            effects=("release_slot", "apply_refund_policy"),
        ),
        S.CANCELLED_BY_PROVIDER: _t(
            S.CANCELLED_BY_PROVIDER,
            {PROVIDER, ADMIN, SYSTEM},
            effects=("release_slot", "full_refund", "penalize_provider"),
        ),
        S.REFUNDED: _t(
            S.REFUNDED,
            {ADMIN, SYSTEM},
            effects=("full_refund",),
            description="Pago duplicado o fallo de cobro (edge cases A7, B9)",
        ),
    },
    S.CONFIRMED: {
        S.COMPLETED: _t(
            S.COMPLETED,
            {PROVIDER, ADMIN, SYSTEM},
            effects=("release_security_deposit", "request_review", "accrue_payout", "emit_webhook"),
            description="El evento terminó y se cerró correctamente",
        ),
        S.CANCELLED_BY_CLIENT: _t(
            S.CANCELLED_BY_CLIENT,
            {CLIENT, ADMIN, SYSTEM},
            guards=(_guard_future,),
            effects=("release_slot", "apply_refund_policy", "notify_provider"),
            description="Cancelación del cliente con política escalonada",
        ),
        S.CANCELLED_BY_PROVIDER: _t(
            S.CANCELLED_BY_PROVIDER,
            {PROVIDER, ADMIN, SYSTEM},
            effects=("release_slot", "full_refund", "penalize_provider", "offer_alternatives"),
            description="El proveedor cancela: reembolso total + penalización",
        ),
        S.NO_SHOW: _t(
            S.NO_SHOW,
            {PROVIDER, ADMIN},
            guards=(_guard_evidence,),
            effects=("retain_deposit", "notify_client", "flag_client_risk"),
            description="El cliente no llegó y el proveedor documentó la espera",
        ),
        S.DISPUTED: _t(
            S.DISPUTED,
            {CLIENT, PROVIDER, ADMIN},
            guards=(_guard_reason,),
            effects=("freeze_payout", "open_dispute", "notify_both"),
            description="Se abrió una disputa",
        ),
        S.REFUNDED: _t(
            S.REFUNDED,
            {ADMIN, SYSTEM},
            effects=("full_refund",),
        ),
    },
    S.COMPLETED: {
        S.DISPUTED: _t(
            S.DISPUTED,
            {CLIENT, PROVIDER, ADMIN},
            guards=(_guard_reason,),
            effects=("freeze_payout", "open_dispute", "notify_both"),
            description="Disputa posterior al evento (daños, incumplimiento)",
        ),
        S.REFUNDED: _t(
            S.REFUNDED,
            {ADMIN},
            effects=("refund", "adjust_payout"),
            description="Reembolso de buena voluntad tras resolución",
        ),
    },
    S.DISPUTED: {
        S.COMPLETED: _t(
            S.COMPLETED,
            {ADMIN},
            effects=("unfreeze_payout", "resolve_dispute", "notify_both"),
            description="Disputa resuelta a favor del proveedor",
        ),
        S.REFUNDED: _t(
            S.REFUNDED,
            {ADMIN},
            effects=("refund", "adjust_payout", "resolve_dispute"),
            description="Disputa resuelta a favor del cliente",
        ),
        S.CANCELLED_BY_CLIENT: _t(S.CANCELLED_BY_CLIENT, {ADMIN}, effects=("release_slot",)),
        S.CANCELLED_BY_PROVIDER: _t(S.CANCELLED_BY_PROVIDER, {ADMIN}, effects=("release_slot",)),
    },
    # Estados terminales: no salen de aquí
    S.CANCELLED_BY_CLIENT: {},
    S.CANCELLED_BY_PROVIDER: {},
    S.NO_SHOW: {},
    S.REFUNDED: {},
}

TERMINAL_STATES: Final = frozenset(
    {S.COMPLETED, S.CANCELLED_BY_CLIENT, S.CANCELLED_BY_PROVIDER, S.NO_SHOW, S.REFUNDED}
    | {S.DISPUTED}
)

#: Estados en los que el venue queda ocupado (debe coincidir con la constraint SQL)
OCCUPYING_STATES: Final = frozenset({S.HOLD, S.PENDING_PAYMENT, S.CONFIRMED, S.COMPLETED})


def is_terminal(state: S) -> bool:
    return state in TERMINAL_STATES


def is_occupying(state: S) -> bool:
    return state in OCCUPYING_STATES


def allowed_transitions(state: S, actor: str) -> list[S]:
    """Lo que la UI puede ofrecer al usuario. La misma tabla que valida el servidor."""
    return [
        target
        for target, transition in TRANSITIONS.get(state, {}).items()
        if actor in transition.actors
    ]


def assert_transition(from_state: S, to_state: S, ctx: GuardContext) -> Transition:
    """Valida la transición o lanza `INVALID_STATE_TRANSITION` con el detalle de la guarda."""
    if from_state == to_state:
        # Idempotencia: repetir la misma transición no es error, se audita como replay
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message="La reserva ya está en ese estado.",
            details={"from": from_state.value, "to": to_state.value, "idempotent": True},
            status_code=200,
        )

    transition = TRANSITIONS.get(from_state, {}).get(to_state)
    if transition is None:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            message=f"No se puede pasar de {from_state.value} a {to_state.value}.",
            details={
                "from": from_state.value,
                "to": to_state.value,
                "allowed": [s.value for s in allowed_transitions(from_state, ctx.actor)],
            },
        )

    if ctx.actor not in transition.actors:
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="No tienes permiso para hacer este cambio en la reserva.",
            details={"from": from_state.value, "to": to_state.value, "actor": ctx.actor},
        )

    for guard in transition.guards:
        ok, reason = guard(ctx)
        if not ok:
            raise AppError(
                ErrorCode.INVALID_STATE_TRANSITION,
                message=reason,
                details={"from": from_state.value, "to": to_state.value},
            )
    return transition


def actor_from_role(role: Role | str, is_owner: bool, is_provider: bool) -> str:
    """Traduce el rol autenticado al actor de la máquina, sin confiar en el cliente."""
    value = role.value if isinstance(role, Role) else role
    if value == Role.ADMIN.value:
        return ADMIN
    if value == Role.SUPPORT.value:
        return ADMIN
    if is_provider:
        return PROVIDER
    if is_owner:
        return CLIENT
    return SYSTEM
