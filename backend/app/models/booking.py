"""Reservas: hold (apartado), booking (contrato) e historial de estados.

La garantía anti-doble-reserva vive aquí: `ExcludeConstraint` sobre
`tstzrange(start_at, end_at, '[)')` por venue (ADR-002).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, AuditFieldsMixin, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import BookingState, HoldStatus

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.venue import Venue


# Estados que ocupan el calendario. El hold expirado libera el slot.
OCCUPYING_STATES = ("HOLD", "PENDING_PAYMENT", "CONFIRMED", "COMPLETED")


class BookingHold(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    """Apartado con TTL. Congela la cotización y el folio antes de pagar (edge case A5)."""

    __tablename__ = "booking_holds"
    __table_args__ = (
        ExcludeConstraint(
            (Column("venue_id", GUID), "="),
            (func.tstzrange(Column("start_at"), Column("end_at"), "[)"), "&&"),
            using="gist",
            name="holds_no_overlap",
            where=text("status = 'ACTIVE' AND deleted_at IS NULL"),
        ),
        CheckConstraint("end_at > start_at", name="hold_range"),
        CheckConstraint("expires_at > created_at", name="hold_expiry_after_creation"),
        Index("ix_holds_venue_window", "venue_id", "start_at", "end_at"),
        Index("ix_holds_expires", "expires_at", postgresql_where=text("status = 'ACTIVE'")),
        Index("ix_holds_client", "client_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    folio: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    start_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    #: Zona horaria del venue al momento de apartar (la UI muestra esta hora)
    venue_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    slot_kind: Mapped[str] = mapped_column(String(16), default="FULL_DAY", nullable=False)
    guests: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type_slug: Mapped[str | None] = mapped_column(String(60), nullable=True)

    #: Precio congelado: desglose, reglas aplicadas y versión del motor (ADR-004)
    quote_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    deposit_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    coupon_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    status: Mapped[HoldStatus] = mapped_column(String(16), default=HoldStatus.ACTIVE, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    converted_booking_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    released_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Extensión única de TTL (máx. 1 por hold): evita holds infinitos
    extended_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    venue: Mapped[Venue] = relationship(lazy="selectin")


class Booking(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "bookings"
    __table_args__ = (
        ExcludeConstraint(
            (Column("venue_id", GUID), "="),
            (func.tstzrange(Column("start_at"), Column("end_at"), "[)"), "&&"),
            using="gist",
            name="bookings_no_overlap",
            where=text(
                "state IN ('HOLD','PENDING_PAYMENT','CONFIRMED','COMPLETED') AND deleted_at IS NULL"
            ),
        ),
        CheckConstraint("end_at > start_at", name="booking_range"),
        CheckConstraint(
            "total_cents >= 0 AND deposit_cents >= 0 AND deposit_cents <= total_cents",
            name="amounts_coherent",
        ),
        CheckConstraint("guests >= 1", name="guests_positive"),
        UniqueConstraint("folio", name="uq_bookings_folio"),
        Index("ix_bookings_venue_window", "venue_id", "start_at", "end_at"),
        Index("ix_bookings_client_state", "client_id", "state", "start_at"),
        Index("ix_bookings_provider_state", "provider_id", "state", "start_at"),
        Index("ix_bookings_start", "start_at"),
        Index("ix_bookings_state_start", "state", "start_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    folio: Mapped[str] = mapped_column(String(20), nullable=False)
    hold_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("booking_holds.id", ondelete="SET NULL"), nullable=True
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="RESTRICT"), nullable=False
    )
    #: Desnormalizado para RBAC y para el panel del proveedor sin joins
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    state: Mapped[BookingState] = mapped_column(
        String(24), default=BookingState.DRAFT, nullable=False
    )
    previous_state: Mapped[BookingState | None] = mapped_column(String(24), nullable=True)

    start_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    #: Rango efectivo con buffer de limpieza, para UI y operación
    blocked_from: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    blocked_to: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    venue_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    slot_kind: Mapped[str] = mapped_column(String(16), default="FULL_DAY", nullable=False)
    guests: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type_slug: Mapped[str | None] = mapped_column(String(60), nullable=True)
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Snapshots: precio y reglas vigentes al confirmar (grandfathering, edge case E27)
    quote_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rules_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    extras_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cleaning_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    service_fee_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    security_deposit_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    deposit_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paid_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    coupon_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    #: Operación del día
    check_in_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    check_out_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    qr_token_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    no_show_evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")

    #: Cancelación y reembolso
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    refund_tier_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reschedule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    contract_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contract_generated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="bookings", lazy="selectin")
    payments: Mapped[list[Payment]] = relationship(back_populates="booking", lazy="selectin")
    status_history: Mapped[list[BookingStatusHistory]] = relationship(
        back_populates="booking", lazy="raise", order_by="BookingStatusHistory.created_at"
    )

    # ── Guardas de la máquina de estados ──────────────────────────────────────
    @property
    def is_active(self) -> bool:
        return self.state in {
            BookingState.HOLD,
            BookingState.PENDING_PAYMENT,
            BookingState.CONFIRMED,
        }

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            BookingState.COMPLETED,
            BookingState.CANCELLED_BY_CLIENT,
            BookingState.CANCELLED_BY_PROVIDER,
            BookingState.NO_SHOW,
            BookingState.REFUNDED,
            BookingState.DISPUTED,
        }

    @property
    def balance_cents(self) -> int:
        return max(self.total_cents - self.paid_cents, 0)


class BookingStatusHistory(Base, TimestampMixin):
    """Bitácora inmutable de transiciones. Se escribe siempre, incluso en no-ops idempotentes."""

    __tablename__ = "booking_status_history"
    __table_args__ = (
        Index("ix_booking_history_booking", "booking_id", "created_at"),
        Index("ix_booking_history_actor", "actor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[BookingState | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[BookingState] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Si la transición vino de un reintento idempotente (no se reescribe estado, sí se audita)
    idempotent_replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")

    booking: Mapped[Booking] = relationship(back_populates="status_history")


class Contract(Base, TimestampMixin, SoftDeleteMixin):
    """Contrato de apartado en PDF, generado desde el snapshot (no desde reglas vivas)."""

    __tablename__ = "contracts"
    __table_args__ = (UniqueConstraint("booking_id", "version", name="uq_contract_booking_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_by_client_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    signed_by_provider_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    accepted_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tos_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    template_version: Mapped[str] = mapped_column(String(20), default="2026.1", nullable=False)
