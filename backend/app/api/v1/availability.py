"""Disponibilidad y precios del venue + cotización pública.

- GET/PUT /venues/{id}/availability-rules      · POST/DELETE /venues/{id}/blackout-dates
- GET/PUT /venues/{id}/pricing-rules           · GET /venues/{id}/quote
- GET /venues/{id}/availability
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, MyProvider
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.availability import AvailabilityRule, BlackoutDate, PricingRule
from app.models.enums import AuditAction, SlotKind
from app.models.venue import Venue
from app.schemas.common import ErrorResponse
from app.schemas.venue import (
    AvailabilityRuleIn,
    AvailabilityRuleOut,
    AvailabilityRulesPutRequest,
    AvailabilityDayOut,
    BlackoutIn,
    BlackoutOut,
    PricingRuleIn,
    PricingRuleOut,
    PricingRulesPutRequest,
    QuoteOut,
    QuoteRequest,
    SlotOut,
    VenueAvailabilityOut,
)
from app.services import audit, availability
from app.services import bookings as booking_service

router = APIRouter()


async def _venue_owned(session: DbSession, venue_id: uuid.UUID, provider_id: uuid.UUID) -> Venue:
    venue = await session.scalar(select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None)))
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})
    if venue.provider_id != provider_id:
        raise AppError(ErrorCode.FORBIDDEN, details={"venue_id": str(venue_id)})
    return venue


# ── Reglas de apertura ────────────────────────────────────────────────────────


@router.get(
    "/{venue_id}/availability-rules",
    response_model=list[AvailabilityRuleOut],
    summary="Ver reglas de apertura",
)
async def get_availability_rules(
    venue_id: uuid.UUID, session: DbSession, profile: MyProvider
) -> list[AvailabilityRuleOut]:
    venue = await _venue_owned(session, venue_id, profile.id)
    rows = (
        await session.scalars(
            select(AvailabilityRule)
            .where(AvailabilityRule.venue_id == venue.id, AvailabilityRule.deleted_at.is_(None))
            .order_by(AvailabilityRule.weekday, AvailabilityRule.open_time)
        )
    ).all()
    return [AvailabilityRuleOut.model_validate(row) for row in rows]


@router.put(
    "/{venue_id}/availability-rules",
    response_model=list[AvailabilityRuleOut],
    summary="Reemplazar reglas de apertura",
    responses={422: {"model": ErrorResponse, "description": "AVAILABILITY_RULE_CONFLICT"}},
)
async def put_availability_rules(
    venue_id: uuid.UUID,
    payload: AvailabilityRulesPutRequest,
    session: DbSession,
    profile: MyProvider,
    user: CurrentUser,
    request: Request,
) -> list[AvailabilityRuleOut]:
    """Valida contradicciones antes de guardar (edge case E25): mismo día con ventanas solapadas."""
    venue = await _venue_owned(session, venue_id, profile.id)

    normalized: list[tuple[int, str, datetime, datetime]] = []
    tz = availability.venue_tz(venue)
    for rule in payload.rules:
        anchor = date(2026, 1, 5)  # lunes fijo para comparar ventanas por día
        day = anchor + timedelta(days=rule.weekday)
        start_utc, end_utc = availability.local_window_to_utc(
            day, rule.open_time, rule.close_time, tz, rule.crosses_midnight
        )
        normalized.append((rule.weekday, rule.slot_kind.value, start_utc, end_utc))

    for i, (weekday_a, kind_a, start_a, end_a) in enumerate(normalized):
        for weekday_b, kind_b, start_b, end_b in normalized[i + 1 :]:
            if weekday_a != weekday_b or kind_a != kind_b:
                continue
            if availability.overlaps(start_a, end_a, start_b, end_b):
                raise AppError(
                    ErrorCode.AVAILABILITY_RULE_CONFLICT,
                    message="Hay dos bloques del mismo tipo que se solapan el mismo día.",
                    details={"weekday": weekday_a, "slot_kind": kind_a},
                )

    if payload.replace_all:
        await session.execute(
            sa_delete(AvailabilityRule).where(AvailabilityRule.venue_id == venue.id)
        )
    for rule in payload.rules:
        session.add(
            AvailabilityRule(
                venue_id=venue.id,
                weekday=rule.weekday,
                slot_kind=rule.slot_kind.value,
                open_time=rule.open_time,
                close_time=rule.close_time,
                crosses_midnight=rule.crosses_midnight,
                slot_minutes=rule.slot_minutes,
                min_booking_minutes=rule.min_booking_minutes,
                max_booking_minutes=rule.max_booking_minutes,
                valid_from=rule.valid_from,
                valid_to=rule.valid_to,
                is_active=rule.is_active,
                note=rule.note,
                created_by_id=user.id,
            )
        )
    await audit.record(
        session,
        action=AuditAction.AVAILABILITY_CHANGED,
        entity="venue",
        entity_id=venue.id,
        actor_id=user.id,
        after={"rules": len(payload.rules), "replace_all": payload.replace_all},
        request=request,
    )
    await session.commit()
    return await get_availability_rules(venue_id, session, profile)


# ── Blackouts ─────────────────────────────────────────────────────────────────


@router.post(
    "/{venue_id}/blackout-dates",
    response_model=BlackoutOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cerrar una fecha",
    responses={
        409: {
            "model": ErrorResponse,
            "description": "BLACKOUT_OVERLAPS_CONFIRMED_BOOKING (requiere force=true o cancelar)",
        }
    },
)
async def add_blackout(
    venue_id: uuid.UUID,
    payload: BlackoutIn,
    session: DbSession,
    profile: MyProvider,
    user: CurrentUser,
    request: Request,
) -> BlackoutOut:
    """Un cierre que pisa reservas confirmadas **no** se aplica en silencio (edge case E27)."""
    venue = await _venue_owned(session, venue_id, profile.id)
    busy = await availability.load_busy_windows(session, venue.id, payload.start_at, payload.end_at)
    conflicts = availability.validate_blackout_against_bookings(
        payload.start_at, payload.end_at, busy
    )
    if conflicts and not payload.force:
        raise AppError(
            ErrorCode.BLACKOUT_OVERLAPS_CONFIRMED_BOOKING,
            message=(
                f"Ese rango tiene {len(conflicts)} reserva(s) confirmada(s). "
                "Cancélalas o confirma el cierre forzado (afecta a tus clientes)."
            ),
            details={"conflicts": [item.reference_id for item in conflicts], "requires_force": True},
        )
    if payload.end_at <= payload.start_at:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="La fecha de fin debe ser posterior al inicio.",
            details={"field": "end_at"},
        )

    blackout = BlackoutDate(
        venue_id=venue.id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        is_partial=payload.is_partial,
        reason=payload.reason,
        internal_note=payload.internal_note,
        overlaps_confirmed=bool(conflicts),
        created_by_id=user.id,
    )
    session.add(blackout)
    await audit.record(
        session,
        action=AuditAction.AVAILABILITY_CHANGED,
        entity="blackout_date",
        entity_id=blackout.id,
        actor_id=user.id,
        after={
            "start_at": payload.start_at,
            "end_at": payload.end_at,
            "forced": payload.force,
            "conflicts": len(conflicts),
        },
        request=request,
    )
    await session.commit()
    return BlackoutOut.model_validate(blackout)


@router.get(
    "/{venue_id}/blackout-dates",
    response_model=list[BlackoutOut],
    summary="Listar cierres",
)
async def list_blackouts(
    venue_id: uuid.UUID,
    session: DbSession,
    profile: MyProvider,
    from_date: date | None = None,
) -> list[BlackoutOut]:
    venue = await _venue_owned(session, venue_id, profile.id)
    stmt = select(BlackoutDate).where(
        BlackoutDate.venue_id == venue.id, BlackoutDate.deleted_at.is_(None)
    )
    if from_date:
        stmt = stmt.where(
            BlackoutDate.end_at >= datetime.combine(from_date, time.min, tzinfo=UTC)
        )
    rows = (await session.scalars(stmt.order_by(BlackoutDate.start_at))).all()
    return [BlackoutOut.model_validate(row) for row in rows]


@router.delete(
    "/{venue_id}/blackout-dates/{blackout_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reabrir una fecha",
)
async def delete_blackout(
    venue_id: uuid.UUID,
    blackout_id: uuid.UUID,
    session: DbSession,
    profile: MyProvider,
    user: CurrentUser,
) -> Response:
    venue = await _venue_owned(session, venue_id, profile.id)
    blackout = await session.scalar(
        select(BlackoutDate).where(
            BlackoutDate.id == blackout_id, BlackoutDate.venue_id == venue.id
        )
    )
    if blackout is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"blackout_id": str(blackout_id)})
    blackout.deleted_at = datetime.now(UTC)
    blackout.deleted_by_id = user.id
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Precios ───────────────────────────────────────────────────────────────────


@router.get(
    "/{venue_id}/pricing-rules",
    response_model=list[PricingRuleOut],
    summary="Ver reglas de precio",
)
async def get_pricing_rules(
    venue_id: uuid.UUID, session: DbSession, profile: MyProvider
) -> list[PricingRuleOut]:
    venue = await _venue_owned(session, venue_id, profile.id)
    rows = (
        await session.scalars(
            select(PricingRule)
            .where(PricingRule.venue_id == venue.id, PricingRule.deleted_at.is_(None))
            .order_by(PricingRule.priority.desc())
        )
    ).all()
    return [PricingRuleOut.model_validate(row) for row in rows]


@router.put(
    "/{venue_id}/pricing-rules",
    response_model=list[PricingRuleOut],
    summary="Reemplazar reglas de precio",
    responses={422: {"model": ErrorResponse, "description": "NO_PRICING_RULE (no queda regla BASE)"}},
)
async def put_pricing_rules(
    venue_id: uuid.UUID,
    payload: PricingRulesPutRequest,
    session: DbSession,
    profile: MyProvider,
    user: CurrentUser,
    request: Request,
) -> list[PricingRuleOut]:
    """Debe quedar al menos una regla BASE activa, o el venue no se puede cotizar."""
    venue = await _venue_owned(session, venue_id, profile.id)
    if payload.replace_all and not any(
        rule.kind.value == "BASE" and rule.is_active for rule in payload.rules
    ):
        raise AppError(
            ErrorCode.NO_PRICING_RULE,
            message="Necesitas al menos una tarifa base activa para publicar precios.",
            details={"field": "rules"},
        )

    if payload.replace_all:
        await session.execute(sa_delete(PricingRule).where(PricingRule.venue_id == venue.id))
    for rule in payload.rules:
        session.add(
            PricingRule(
                venue_id=venue.id,
                kind=rule.kind.value,
                name=rule.name,
                amount_cents=rule.amount_cents,
                per_hour=rule.per_hour,
                priority=rule.priority,
                min_guests=rule.min_guests,
                max_guests=rule.max_guests,
                extra_guest_cents=rule.extra_guest_cents,
                valid_from=rule.valid_from,
                valid_to=rule.valid_to,
                weekdays=rule.weekdays,
                specific_dates=[d.isoformat() for d in rule.specific_dates],
                slot_kind=rule.slot_kind.value if rule.slot_kind else None,
                min_days_before=rule.min_days_before,
                max_days_before=rule.max_days_before,
                discount_percent=rule.discount_percent,
                is_active=rule.is_active,
                note=rule.note,
                created_by_id=user.id,
            )
        )
    if payload.rules and payload.rules[0].kind.value == "BASE":
        venue.base_price_cents = payload.rules[0].amount_cents
    await audit.record(
        session,
        action=AuditAction.PRICING_CHANGED,
        entity="venue",
        entity_id=venue.id,
        actor_id=user.id,
        after={"rules": len(payload.rules), "replace_all": payload.replace_all},
        request=request,
    )
    await session.commit()
    return await get_pricing_rules(venue_id, session, profile)


# ── Disponibilidad pública y cotización ───────────────────────────────────────


@router.get(
    "/{venue_id}/availability",
    response_model=VenueAvailabilityOut,
    summary="Disponibilidad del espacio en un rango",
)
async def get_availability(
    venue_id: uuid.UUID,
    session: DbSession,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    slot: SlotKind | None = None,
    guests: int | None = Query(None, ge=1),
) -> VenueAvailabilityOut:
    """Devuelve por día: libre, con motivo cuando no lo está, y bloques con precio.

    Nunca expone datos del cliente que reservó (sólo `booked`/`blackout`).
    """
    venue = await session.scalar(
        select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
    )
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(venue_id)})
    if (to_date - from_date).days > 120:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="El rango máximo de consulta es de 120 días.",
            details={"field": "to"},
        )

    tz = availability.venue_tz(venue)
    start_window, _ = availability.local_window_to_utc(from_date, time(0, 0), time(23, 59), tz, False)
    _, end_window = availability.local_window_to_utc(to_date, time(0, 0), time(23, 59), tz, False)
    busy = await availability.load_busy_windows(session, venue.id, start_window, end_window)

    days: list[AvailabilityDayOut] = []
    today = datetime.now(tz).date()
    for offset in range((to_date - from_date).days + 1):
        day = from_date + timedelta(days=offset)
        slots_out: list[SlotOut] = []
        reason: str | None = None

        if day < today:
            reason = "past"
        elif day < today + timedelta(hours=venue.min_notice_hours) and day == today:
            reason = "min_notice"

        day_slots = availability.slots_for_day(venue, day, venue.base_price_cents)
        for slot_def in day_slots:
            if slot and slot_def.slot_kind != slot.value:
                continue
            conflicts = availability.conflicts_for(
                slot_def.start_at, slot_def.end_at, busy, buffer_minutes=venue.buffer_minutes
            )
            free = not conflicts
            if not free and reason is None:
                reason = "blackout" if any(c.source == "blackout" for c in conflicts) else "booked"
            slots_out.append(
                SlotOut(
                    start_at=slot_def.start_at,
                    end_at=slot_def.end_at,
                    slot_kind=SlotKind(slot_def.slot_kind),
                    price_cents=slot_def.price_cents,
                    currency=venue.currency,
                    is_available=free and reason != "past",
                )
            )
        days.append(
            AvailabilityDayOut(
                date=day,
                is_available=any(item.is_available for item in slots_out),
                slots=slots_out,
                reason_unavailable=reason,
            )
        )

    return VenueAvailabilityOut(
        venue_id=venue.id,
        timezone=venue.timezone,
        from_date=from_date,
        to_date=to_date,
        days=days,
    )


@router.get(
    "/{venue_id}/quote",
    response_model=QuoteOut,
    summary="Cotizar una fecha",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "GUESTS_OUT_OF_RANGE · MIN_HOURS_NOT_MET · NO_PRICING_RULE · DATE_IN_PAST",
        }
    },
)
async def quote(
    venue_id: uuid.UUID,
    session: DbSession,
    start_at: datetime = Query(..., alias="start"),
    end_at: datetime = Query(..., alias="end"),
    guests: int = Query(..., ge=1),
    extras: list[str] = Query(default_factory=list),
    coupon_code: str | None = None,
    user: CurrentUser | None = None,
) -> QuoteOut:
    """Cotización con desglose y snapshot. El cliente **no** fija el precio: se recalcula aquí."""
    venue = await booking_service.load_venue_for_quote(session, venue_id)
    return await booking_service.build_venue_quote(
        session,
        venue=venue,
        start_at=start_at,
        end_at=end_at,
        guests=guests,
        extras=extras,
        user_id=user.id if user else None,
        coupon_code=coupon_code,
    )
