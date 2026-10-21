"""Disponibilidad, blackouts y precios por venue."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, AuditFieldsMixin, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import PricingKind, SlotKind

if TYPE_CHECKING:
    from app.models.venue import Venue


class AvailabilityRule(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    """Ventana semanal de apertura. La disponibilidad efectiva = reglas − blackouts − reservas.

    `open_time`/`close_time` son horas **locales del venue** (no UTC) para que el DST no
    mueva el horario de apertura (edge case A3).
    """

    __tablename__ = "availability_rules"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_range"),
        CheckConstraint("slot_minutes >= 30 AND slot_minutes <= 1440", name="slot_minutes_range"),
        Index("ix_availability_venue_weekday", "venue_id", "weekday"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=lunes … 6=domingo
    slot_kind: Mapped[SlotKind] = mapped_column(String(16), default=SlotKind.FULL_DAY, nullable=False)
    open_time: Mapped[time] = mapped_column(Time, nullable=False)
    close_time: Mapped[time] = mapped_column(Time, nullable=False)
    #: Si `close_time` < `open_time`, el bloque cruza medianoche (edge case A4)
    crosses_midnight: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=1440, nullable=False)
    min_booking_minutes: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    max_booking_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="availability_rules")


class BlackoutDate(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    """Cierre puntual (mantenimiento, uso propio, feriado). Puede ser parcial."""

    __tablename__ = "blackout_dates"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="blackout_range"),
        Index("ix_blackout_venue_range", "venue_id", "start_at", "end_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(280), nullable=True)
    #: Se calcula al guardar: `True` si choca con reservas ya confirmadas (edge case E27)
    overlaps_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    internal_note: Mapped[str | None] = mapped_column(String(280), nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="blackout_dates")


class PricingRule(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    """Regla de precio. Se evalúan por prioridad; la de mayor prioridad que aplique gana.

    Nunca se edita un precio ya usado: el booking guarda `quote_snapshot` (ADR-004).
    """

    __tablename__ = "pricing_rules"
    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="amount_non_negative"),
        CheckConstraint("priority >= 0 AND priority <= 100", name="priority_range"),
        Index("ix_pricing_venue_kind", "venue_id", "kind", "priority"),
        Index("ix_pricing_venue_validity", "venue_id", "valid_from", "valid_to"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[PricingKind] = mapped_column(String(16), default=PricingKind.BASE, nullable=False)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: `amount_cents` es por hora si `per_hour=True`, si no es por bloque/día
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    per_hour: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    min_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_guest_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Vigencia: fechas concretas, días de la semana, o temporada
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    weekdays: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    specific_dates: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    slot_kind: Mapped[SlotKind | None] = mapped_column(String(16), nullable=True)
    #: Descuento por reservar con poca anticipación
    min_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="pricing_rules")


class SeasonalRate(Base, TimestampMixin, SoftDeleteMixin):
    """Temporadas altas y feriados locales/regionales (edge case E28).

    Se separa de `PricingRule` porque la UI de proveedor las edita como calendario.
    """

    __tablename__ = "seasonal_rates"
    __table_args__ = (
        UniqueConstraint("venue_id", "name", "start_date", name="uq_seasonal_rate"),
        Index("ix_seasonal_venue_range", "venue_id", "start_date", "end_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    multiplier: Mapped[float] = mapped_column(Numeric(5, 2), default=1.0, nullable=False)
    surcharge_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applies_weekdays_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
