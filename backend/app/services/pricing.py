"""Motor de cotización: reglas de precio, extras, cupones, comisión, impuestos y snapshot.

Versionado: `PRICING_ENGINE_VERSION` viaja dentro del snapshot. Si cambia la forma del
snapshot, se migra; nunca se recalcula un hold/booking existente (ADR-004).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.availability import PricingRule, SeasonalRate
from app.models.venue import Venue, VenueAmenity
from app.schemas.venue import QuoteLineOut, QuoteOut

PRICING_ENGINE_VERSION: Final = "1.0.0"
TAX_RATE_MX: Final = Decimal("0.16")  # IVA; se aplica a la comisión de servicio
HOURS_QUANTUM: Final = Decimal("0.5")  # se cobra por medias horas


@dataclass(slots=True)
class CouponEvaluation:
    coupon_id: uuid.UUID | None = None
    code: str | None = None
    kind: str | None = None
    discount_cents: int = 0
    reason: str | None = None


@dataclass(slots=True)
class QuoteInput:
    venue: Venue
    start_at: datetime
    end_at: datetime
    guests: int
    extras: list[str] = field(default_factory=list)
    pricing_rules: list[PricingRule] = field(default_factory=list)
    seasonal_rates: list[SeasonalRate] = field(default_factory=list)
    amenities: list[VenueAmenity] = field(default_factory=list)
    coupon: CouponEvaluation | None = None
    platform_fee_bps: int | None = None


def _money(cents: Decimal | int) -> int:
    return int(Decimal(cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _venue_tz(venue: Venue) -> ZoneInfo:
    try:
        return ZoneInfo(venue.timezone)
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            ErrorCode.TIMEZONE_INVALID,
            message=f"La zona horaria del espacio ({venue.timezone}) no es válida.",
            details={"timezone": venue.timezone},
        ) from exc


def local_dates(start_at: datetime, end_at: datetime, tz: ZoneInfo) -> tuple[date, date]:
    """Fechas locales del venue. Un evento 22:00→02:00 abarca dos fechas locales (edge case A4)."""
    return start_at.astimezone(tz).date(), end_at.astimezone(tz).date()


def real_duration_hours(start_at: datetime, end_at: datetime) -> Decimal:
    """Duración real en horas, en UTC. Inmune a DST: 02:00→03:00 del cambio de horario son 1 h reales.

    El proveedor cobra por duración real, no por "horas de reloj de pared"; se avisa en la UI
    cuando la duración real difiere de la resta de horas locales (edge case A3).
    """
    seconds = (end_at - start_at).total_seconds()
    if seconds <= 0:
        raise AppError(
            ErrorCode.INVALID_DURATION,
            message="La fecha de fin debe ser posterior al inicio.",
            details={"start_at": start_at.isoformat(), "end_at": end_at.isoformat()},
        )
    return (Decimal(seconds) / Decimal(3600)).quantize(HOURS_QUANTUM)


def validate_window(venue: Venue, start_at: datetime, end_at: datetime) -> tuple[Decimal, list[str]]:
    """Validaciones de tiempo que no dependen del precio. Devuelve (horas, advertencias)."""
    warnings: list[str] = []
    now = datetime.now(UTC)

    if start_at < now:
        raise AppError(
            ErrorCode.DATE_IN_PAST,
            message="Esa fecha ya pasó. Elige una fecha futura.",
            details={"start_at": start_at.isoformat(), "now": now.isoformat()},
        )

    if venue.min_notice_hours and start_at < now + timedelta(hours=venue.min_notice_hours):
        raise AppError(
            ErrorCode.MIN_HOURS_NOT_MET,
            message=f"Este espacio necesita al menos {venue.min_notice_hours} h de anticipación.",
            details={"min_notice_hours": venue.min_notice_hours},
        )

    hours = real_duration_hours(start_at, end_at)
    tz = _venue_tz(venue)

    if hours < Decimal(venue.min_hours):
        raise AppError(
            ErrorCode.MIN_HOURS_NOT_MET,
            message=f"La renta mínima es de {venue.min_hours} horas.",
            details={"min_hours": venue.min_hours, "requested_hours": float(hours)},
        )
    if hours > Decimal(venue.max_hours):
        raise AppError(
            ErrorCode.INVALID_DURATION,
            message=f"La renta máxima es de {venue.max_hours} horas.",
            details={"max_hours": venue.max_hours, "requested_hours": float(hours)},
        )

    start_local, end_local = local_dates(start_at, end_at, tz)
    if start_local != end_local and not venue.allows_multi_day:
        # Cruce de medianoche dentro del mismo bloque nocturno: permitido si no cruza a un día
        # natural completo siguiente; multi-día requiere habilitación explícita.
        span_days = (end_local - start_local).days
        if span_days >= 1 and hours > Decimal(18):
            raise AppError(
                ErrorCode.INVALID_DURATION,
                message="Este espacio no permite rentas de varios días. Divide el evento en bloques.",
                details={"allows_multi_day": False, "span_days": span_days},
            )

    if venue.curfew_time:
        curfew = time.fromisoformat(venue.curfew_time)
        end_local_time = end_at.astimezone(tz).time()
        # Después de medianoche, la salida debe ser <= hora límite
        if end_local > start_local and end_local_time > curfew and end_local_time < time(12, 0):
            raise AppError(
                ErrorCode.INVALID_DURATION,
                message=f"Este espacio cierra a las {venue.curfew_time}. Ajusta la hora de salida.",
                details={"curfew_time": venue.curfew_time},
            )

    if venue.max_events_per_day == 1 and start_local != end_local:
        warnings.append("Tu evento cruza la medianoche: cuenta como un solo día, sin cargos extra.")

    if venue.buffer_minutes:
        warnings.append(
            f"Se reservan {venue.buffer_minutes} min de limpieza después de tu evento "
            "(ya incluidos en el bloqueo del calendario)."
        )
    return hours, warnings


def _rule_applies(rule: PricingRule, start_at: datetime, end_at: datetime, guests: int, tz: ZoneInfo) -> bool:
    if not rule.is_active:
        return False
    start_local = start_at.astimezone(tz)
    day = start_local.date()
    weekday = start_local.weekday()

    if rule.valid_from and day < rule.valid_from:
        return False
    if rule.valid_to and day > rule.valid_to:
        return False
    if rule.weekdays and weekday not in rule.weekdays:
        return False
    if rule.specific_dates and day.isoformat() not in {d.isoformat() for d in rule.specific_dates}:
        return False
    if rule.min_guests and guests < rule.min_guests:
        return False
    if rule.max_guests and guests > rule.max_guests:
        return False
    if rule.min_days_before is not None or rule.max_days_before is not None:
        days_before = (start_local - datetime.now(tz)).days
        if rule.min_days_before is not None and days_before < rule.min_days_before:
            return False
        if rule.max_days_before is not None and days_before > rule.max_days_before:
            return False
    return True


def _seasonal_multiplier(
    seasonal: list[SeasonalRate], start_at: datetime, tz: ZoneInfo
) -> tuple[Decimal, SeasonalRate | None]:
    day = start_at.astimezone(tz).date()
    weekday = start_at.astimezone(tz).weekday()
    best = Decimal("1.0")
    applied: SeasonalRate | None = None
    for rate in seasonal:
        if not (rate.start_date <= day <= rate.end_date):
            continue
        if rate.applies_weekdays_only and weekday >= 5:
            continue
        multiplier = Decimal(str(rate.multiplier or 1))
        if multiplier > best:
            best, applied = multiplier, rate
    return best, applied


def build_quote(data: QuoteInput) -> QuoteOut:
    """Cotización determinista: mismo input ⇒ mismo output, siempre con snapshot.

    Orden: base → extras → limpieza → temporada → cupón → comisión → impuesto → garantía.
    """
    venue = data.venue
    tz = _venue_tz(venue)
    hours, warnings = validate_window(venue, data.start_at, data.end_at)

    if not venue.capacity_min <= data.guests <= venue.capacity_max:
        raise AppError(
            ErrorCode.GUESTS_OUT_OF_RANGE,
            message=(
                f"Este espacio recibe entre {venue.capacity_min} y {venue.capacity_max} invitados."
            ),
            details={
                "capacity_min": venue.capacity_min,
                "capacity_max": venue.capacity_max,
                "guests": data.guests,
            },
        )

    applicable = [
        rule
        for rule in data.pricing_rules
        if _rule_applies(rule, data.start_at, data.end_at, data.guests, tz)
    ]
    if not applicable:
        raise AppError(
            ErrorCode.NO_PRICING_RULE,
            message="Este espacio no tiene precio publicado para esa fecha. Contacta al anfitrión.",
            details={"venue_id": str(venue.id)},
        )

    # La regla de mayor prioridad define la base; `specific_dates` gana sobre temporada.
    applicable.sort(key=lambda r: (len(r.specific_dates) > 0, r.priority, r.kind), reverse=True)
    base_rule = applicable[0]

    if base_rule.per_hour:
        base_cents = Decimal(base_rule.amount_cents) * hours
    else:
        # Bloque: si el evento dura más que el bloque, se cobra proporcionalmente
        base_cents = Decimal(base_rule.amount_cents)

    lines: list[QuoteLineOut] = []
    applied_rule_labels: list[str] = [f"{base_rule.kind}:{base_rule.id}"]

    # Extras de invitados
    if base_rule.extra_guest_cents and data.guests > (base_rule.min_guests or 0):
        extra_guests = data.guests - (base_rule.min_guests or 0)
        extra_cents = Decimal(base_rule.extra_guest_cents) * extra_guests
        base_cents += extra_cents
        lines.append(
            QuoteLineOut(
                concept="Invitados adicionales",
                detail=f"{extra_guests} × {base_rule.extra_guest_cents / 100:.2f}",
                amount_cents=_money(extra_cents),
            )
        )

    # Temporada alta / feriados
    multiplier, seasonal = _seasonal_multiplier(data.seasonal_rates, data.start_at, tz)
    seasonal_cents = Decimal(0)
    if multiplier > 1:
        seasonal_cents = base_cents * (multiplier - 1)
        base_cents += seasonal_cents
        if seasonal:
            applied_rule_labels.append(f"season:{seasonal.name}")
            lines.append(
                QuoteLineOut(
                    concept=f"Temporada: {seasonal.name}",
                    detail=f"×{multiplier}",
                    amount_cents=_money(seasonal_cents),
                )
            )
    elif seasonal and seasonal.surcharge_cents:
        base_cents += Decimal(seasonal.surcharge_cents)
        lines.append(
            QuoteLineOut(
                concept=f"Recargo: {seasonal.name}",
                amount_cents=int(seasonal.surcharge_cents),
            )
        )

    lines.insert(
        0,
        QuoteLineOut(
            concept="Renta del espacio",
            detail=f"{float(hours):g} h · {base_rule.name or base_rule.kind}",
            amount_cents=_money(base_cents - seasonal_cents if seasonal_cents else base_cents),
        ),
    )

    # Extras solicitados
    extras_cents = Decimal(0)
    for amenity in data.amenities:
        slug = amenity.amenity.slug if amenity.amenity else None
        if slug and slug in data.extras and not amenity.is_included:
            price = amenity.extra_price_cents or (amenity.amenity.default_price_cents if amenity.amenity else 0) or 0
            extras_cents += Decimal(price)
            lines.append(
                QuoteLineOut(
                    concept=amenity.amenity.name if amenity.amenity else slug,
                    detail="Extra",
                    amount_cents=int(price),
                )
            )

    cleaning_cents = Decimal(venue.cleaning_fee_cents or 0)
    if cleaning_cents:
        lines.append(
            QuoteLineOut(concept="Limpieza y montaje", amount_cents=int(cleaning_cents))
        )

    subtotal = base_cents + extras_cents + cleaning_cents

    # Cupón: nunca por debajo de 0 y nunca sobre la garantía
    discount_cents = Decimal(0)
    coupon = data.coupon
    if coupon and coupon.discount_cents:
        discount_cents = min(Decimal(coupon.discount_cents), subtotal)
        lines.append(
            QuoteLineOut(
                concept=f"Cupón {coupon.code}",
                detail=coupon.kind,
                amount_cents=_money(discount_cents),
                is_discount=True,
            )
        )

    taxable_base = subtotal - discount_cents
    fee_bps = data.platform_fee_bps if data.platform_fee_bps is not None else settings.platform_fee_bps
    service_fee = (taxable_base * Decimal(fee_bps) / Decimal(10_000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if service_fee:
        lines.append(
            QuoteLineOut(
                concept="Comisión de servicio",
                detail=f"{fee_bps / 100:.2f}%",
                amount_cents=int(service_fee),
            )
        )

    tax = (service_fee * TAX_RATE_MX).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if tax:
        lines.append(QuoteLineOut(concept="IVA sobre comisión (16%)", amount_cents=int(tax)))

    security_deposit = Decimal(venue.security_deposit_cents or 0)
    if security_deposit:
        lines.append(
            QuoteLineOut(
                concept="Depósito de garantía (reembolsable)",
                detail="Se libera 48 h después del evento si no hay daños",
                amount_cents=int(security_deposit),
                is_refundable=True,
            )
        )

    total = taxable_base + service_fee + tax + security_deposit
    deposit = (total * Decimal(venue.deposit_percent) / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if venue.deposit_percent == 0:
        deposit = total

    now = datetime.now(UTC)
    snapshot: dict[str, Any] = {
        "engine_version": PRICING_ENGINE_VERSION,
        "computed_at": now.isoformat(),
        "venue_id": str(venue.id),
        "venue_timezone": venue.timezone,
        "start_at": data.start_at.isoformat(),
        "end_at": data.end_at.isoformat(),
        "duration_hours": float(hours),
        "guests": data.guests,
        "currency": venue.currency,
        "applied_rules": applied_rule_labels,
        "base_rule_id": str(base_rule.id),
        "seasonal_multiplier": float(multiplier),
        "lines": [line.model_dump() for line in lines],
        "totals": {
            "subtotal_cents": _money(subtotal),
            "extras_cents": _money(extras_cents),
            "cleaning_cents": _money(cleaning_cents),
            "discount_cents": _money(discount_cents),
            "service_fee_cents": int(service_fee),
            "tax_cents": int(tax),
            "security_deposit_cents": int(security_deposit),
            "total_cents": _money(total),
            "deposit_cents": _money(deposit),
        },
        "coupon": (
            {"code": coupon.code, "kind": coupon.kind, "discount_cents": coupon.discount_cents}
            if coupon and coupon.discount_cents
            else None
        ),
        "policy": {
            "deposit_percent": venue.deposit_percent,
            "cancellation": "TIERED_STANDARD",
            "full_payment_days_before": venue.requires_full_payment_days_before,
        },
    }

    return QuoteOut(
        venue_id=venue.id,
        start_at=data.start_at,
        end_at=data.end_at,
        venue_timezone=venue.timezone,
        duration_hours=float(hours),
        guests=data.guests,
        lines=lines,
        subtotal_cents=_money(subtotal),
        extras_cents=_money(extras_cents),
        cleaning_cents=_money(cleaning_cents),
        discount_cents=_money(discount_cents),
        service_fee_cents=int(service_fee),
        tax_cents=int(tax),
        security_deposit_cents=int(security_deposit),
        total_cents=_money(total),
        deposit_cents=_money(deposit),
        currency=venue.currency,
        applied_rules=applied_rule_labels,
        coupon=(
            {"code": coupon.code, "kind": coupon.kind, "discount_cents": coupon.discount_cents}
            if coupon and coupon.discount_cents
            else None
        ),
        hold_ttl_seconds=settings.hold_ttl_seconds,
        pricing_engine_version=PRICING_ENGINE_VERSION,
        warnings=warnings,
        snapshot=snapshot,
        expires_at=now + timedelta(seconds=settings.hold_ttl_seconds),
    )


def verify_snapshot_total(snapshot: dict[str, Any], expected_cents: int) -> None:
    """El cliente no puede fijar el precio: se compara su expectativa con el snapshot del servidor."""
    server_total = int(snapshot.get("totals", {}).get("total_cents", -1))
    if server_total != expected_cents:
        raise AppError(
            ErrorCode.AMOUNT_MISMATCH,
            message="El precio cambió desde que viste la cotización. Revisa el nuevo desglose.",
            details={"expected_cents": expected_cents, "server_cents": server_total},
        )


def refund_tier(days_until_event: float) -> tuple[int, str]:
    """Política escalonada estándar: 100% >30d, 50% 7–30d, 0% <7d (edge case B10)."""
    if days_until_event >= settings.cancel_full_refund_days:
        return 100, f"Reembolso total: cancelaste con {days_until_event:.1f} días de anticipación."
    if days_until_event >= settings.cancel_half_refund_days:
        return 50, (
            f"Reembolso del 50%: cancelaste entre {settings.cancel_half_refund_days} y "
            f"{settings.cancel_full_refund_days} días antes del evento."
        )
    return 0, (
        f"Sin reembolso: cancelaste a menos de {settings.cancel_half_refund_days} días del evento. "
        "El depósito de garantía sí se libera."
    )
