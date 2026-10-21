"""Disponibilidad efectiva: reglas semanales − blackouts − reservas/holds (con buffer).

Toda comparación se hace en UTC sobre `timestamptz`; las horas de las reglas se interpretan
en la zona horaria del venue, de modo que el cambio de horario no mueve la apertura (A3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.availability import AvailabilityRule, BlackoutDate
from app.models.booking import Booking, BookingHold
from app.models.enums import BookingState, HoldStatus
from app.models.venue import Venue

OCCUPYING_BOOKING_STATES = (
    BookingState.HOLD.value,
    BookingState.PENDING_PAYMENT.value,
    BookingState.CONFIRMED.value,
    BookingState.COMPLETED.value,
)


@dataclass(frozen=True, slots=True)
class BusyWindow:
    start_at: datetime
    end_at: datetime
    source: str  # booking | hold | blackout
    reference_id: str


@dataclass(frozen=True, slots=True)
class Slot:
    start_at: datetime
    end_at: datetime
    slot_kind: str
    price_cents: int


def venue_tz(venue: Venue) -> ZoneInfo:
    try:
        return ZoneInfo(venue.timezone)
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            ErrorCode.TIMEZONE_INVALID,
            message=f"Zona horaria inválida en el espacio: {venue.timezone}",
        ) from exc


def local_window_to_utc(day: date, start: time, end: time, tz: ZoneInfo, crosses_midnight: bool) -> tuple[datetime, datetime]:
    """Convierte horas locales del venue a UTC. `crosses_midnight` suma un día al cierre."""
    start_local = datetime.combine(day, start, tzinfo=tz)
    end_local = datetime.combine(day, end, tzinfo=tz)
    if crosses_midnight or end <= start:
        end_local += timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def rule_applies_on(rule: AvailabilityRule, day: date, tz: ZoneInfo) -> bool:
    if not rule.is_active:
        return False
    local_day = day
    if rule.weekday != local_day.weekday():
        return False
    if rule.valid_from and local_day < rule.valid_from:
        return False
    return not (rule.valid_to and local_day > rule.valid_to)


async def load_busy_windows(
    session: AsyncSession,
    venue_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
    *,
    exclude_hold_id: uuid.UUID | None = None,
    exclude_booking_id: uuid.UUID | None = None,
) -> list[BusyWindow]:
    """Reservas, apartados activos y blackouts que tocan la ventana, ya con buffer aplicado."""
    busy: list[BusyWindow] = []

    bookings = await session.scalars(
        select(Booking).where(
            Booking.venue_id == venue_id,
            Booking.deleted_at.is_(None),
            Booking.state.in_(OCCUPYING_BOOKING_STATES),
            Booking.id != (exclude_booking_id or uuid.UUID(int=0)),
            # Solape con buffer ya incluido en blocked_from/blocked_to
            Booking.blocked_from < window_end,
            Booking.blocked_to > window_start,
        )
    )
    for booking in bookings:
        busy.append(
            BusyWindow(
                start_at=booking.blocked_from,
                end_at=booking.blocked_to,
                source="booking",
                reference_id=str(booking.id),
            )
        )

    holds = await session.scalars(
        select(BookingHold).where(
            BookingHold.venue_id == venue_id,
            BookingHold.deleted_at.is_(None),
            BookingHold.status == HoldStatus.ACTIVE.value,
            BookingHold.expires_at > datetime.now(UTC),
            BookingHold.id != (exclude_hold_id or uuid.UUID(int=0)),
            BookingHold.start_at < window_end,
            BookingHold.end_at > window_start,
        )
    )
    for hold in holds:
        busy.append(
            BusyWindow(
                start_at=hold.start_at,
                end_at=hold.end_at,
                source="hold",
                reference_id=str(hold.id),
            )
        )

    blackouts = await session.scalars(
        select(BlackoutDate).where(
            BlackoutDate.venue_id == venue_id,
            BlackoutDate.deleted_at.is_(None),
            BlackoutDate.start_at < window_end,
            BlackoutDate.end_at > window_start,
        )
    )
    for blackout in blackouts:
        busy.append(
            BusyWindow(
                start_at=blackout.start_at,
                end_at=blackout.end_at,
                source="blackout",
                reference_id=str(blackout.id),
            )
        )

    return sorted(busy, key=lambda item: item.start_at)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Solape con rangos semiabiertos `[inicio, fin)`: reservas contiguas no chocan."""
    return a_start < b_end and b_start < a_end


def conflicts_for(
    start_at: datetime,
    end_at: datetime,
    busy: list[BusyWindow],
    *,
    buffer_minutes: int,
) -> list[BusyWindow]:
    """Aplica el buffer de limpieza a ambos lados del evento solicitado (edge case A4)."""
    buffer = timedelta(minutes=buffer_minutes)
    guarded_start = start_at - buffer
    guarded_end = end_at + buffer
    return [
        item
        for item in busy
        if overlaps(guarded_start, guarded_end, item.start_at, item.end_at)
    ]


async def is_slot_free(
    session: AsyncSession,
    venue: Venue,
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_hold_id: uuid.UUID | None = None,
    exclude_booking_id: uuid.UUID | None = None,
) -> tuple[bool, list[BusyWindow]]:
    busy = await load_busy_windows(
        session,
        venue.id,
        start_at - timedelta(minutes=venue.buffer_minutes),
        end_at + timedelta(minutes=venue.buffer_minutes),
        exclude_hold_id=exclude_hold_id,
        exclude_booking_id=exclude_booking_id,
    )
    conflicts = conflicts_for(start_at, end_at, busy, buffer_minutes=venue.buffer_minutes)
    return (not conflicts), conflicts


def assert_within_rules(venue: Venue, start_at: datetime, end_at: datetime) -> None:
    """Valida que el evento caiga dentro de una ventana publicada (regla de negocio, no DB)."""
    tz = venue_tz(venue)
    local_start = start_at.astimezone(tz)
    ok = False
    for rule in venue.availability_rules:
        if not rule.is_active:
            continue
        if rule.valid_from and local_start.date() < rule.valid_from:
            continue
        if rule.valid_to and local_start.date() > rule.valid_to:
            continue
        if rule.weekday != local_start.weekday():
            continue
        open_utc, close_utc = local_window_to_utc(
            local_start.date(), rule.open_time, rule.close_time, tz, rule.crosses_midnight
        )
        if open_utc <= start_at and end_at <= close_utc:
            ok = True
            break
    if not ok:
        raise AppError(
            ErrorCode.VENUE_NOT_AVAILABLE,
            message="El espacio no abre en ese horario. Revisa los horarios publicados.",
            details={"timezone": venue.timezone, "start_at": start_at.isoformat()},
        )


def slots_for_day(venue: Venue, day: date, price_cents: int | None = None) -> list[Slot]:
    """Bloques del día según las reglas activas. Sirve para pintar el calendario."""
    tz = venue_tz(venue)
    slots: list[Slot] = []
    for rule in venue.availability_rules:
        if not rule_applies_on(rule, day, tz):
            continue
        start_utc, end_utc = local_window_to_utc(
            day, rule.open_time, rule.close_time, tz, rule.crosses_midnight
        )
        slot_minutes = rule.slot_minutes or int((end_utc - start_utc).total_seconds() // 60)
        cursor = start_utc
        while cursor < end_utc:
            slot_end = min(cursor + timedelta(minutes=slot_minutes), end_utc)
            if (slot_end - cursor).total_seconds() / 60 >= rule.min_booking_minutes:
                slots.append(
                    Slot(
                        start_at=cursor,
                        end_at=slot_end,
                        slot_kind=rule.slot_kind.value if hasattr(rule.slot_kind, "value") else str(rule.slot_kind),
                        price_cents=price_cents or 0,
                    )
                )
            cursor = slot_end
    return sorted(slots, key=lambda item: item.start_at)


async def next_available_date(
    session: AsyncSession, venue: Venue, from_date: date, days_ahead: int = 90
) -> date | None:
    """Primera fecha con al menos un bloque libre. Se usa en las tarjetas de búsqueda."""
    for offset in range(days_ahead):
        day = from_date + timedelta(days=offset)
        for slot in slots_for_day(venue, day):
            free, _ = await is_slot_free(session, venue, slot.start_at, slot.end_at)
            if free:
                return day
    return None


def summarize_availability(venue: Venue, busy: list[BusyWindow], window_start: datetime, window_end: datetime) -> dict:
    """Resumen barato para listados: no expone quién reservó, sólo si hay hueco."""
    tz = venue_tz(venue)
    days: list[dict] = []
    day = window_start.astimezone(tz).date()
    last_day = window_end.astimezone(tz).date()
    while day <= last_day:
        day_start, day_end = local_window_to_utc(day, time(0, 0), time(23, 59), tz, False)
        day_busy = [
            item for item in busy if overlaps(day_start, day_end, item.start_at, item.end_at)
        ]
        has_slots = bool(slots_for_day(venue, day))
        days.append(
            {
                "date": day.isoformat(),
                "has_rules": has_slots,
                "busy_count": len(day_busy),
                "is_available": has_slots and not any(item.source == "blackout" for item in day_busy),
            }
        )
        day += timedelta(days=1)
    return {"days": days}


def validate_blackout_against_bookings(
    start_at: datetime, end_at: datetime, busy: list[BusyWindow]
) -> list[BusyWindow]:
    """Cerrar una fecha con reservas confirmadas exige decisión explícita (edge case E27)."""
    return [
        item
        for item in busy
        if item.source == "booking" and overlaps(start_at, end_at, item.start_at, item.end_at)
    ]


def quote_window_days(venue: Venue, start_at: datetime, end_at: datetime) -> Decimal:
    """Días facturables en la zona del venue (para reportes de ocupación)."""
    tz = venue_tz(venue)
    start_local = start_at.astimezone(tz)
    end_local = end_at.astimezone(tz)
    return Decimal(str((end_local - start_local).total_seconds() / 86400)).quantize(Decimal("0.01"))


__all__ = [
    "OCCUPYING_BOOKING_STATES",
    "BusyWindow",
    "Slot",
    "assert_within_rules",
    "conflicts_for",
    "is_slot_free",
    "load_busy_windows",
    "local_window_to_utc",
    "next_available_date",
    "overlaps",
    "quote_window_days",
    "rule_applies_on",
    "slots_for_day",
    "summarize_availability",
    "validate_blackout_against_bookings",
    "venue_tz",
]
