"""Pruebas unitarias del motor de cotización: dinero, DST, medianoche, cupones y política."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import AppError, ErrorCode
from app.services import pricing
from app.services.pricing import (
    QuoteInput,
    build_quote,
    real_duration_hours,
    refund_tier,
    validate_window,
)

MERIDA = ZoneInfo("America/Merida")


def make_venue(**overrides) -> SimpleNamespace:
    """Venue mínimo para el motor. Se usa SimpleNamespace para no arrastrar la base de datos."""
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "timezone": "America/Merida",
        "capacity_min": 20,
        "capacity_max": 120,
        "min_hours": 4,
        "max_hours": 24,
        "buffer_minutes": 60,
        "base_price_cents": 840_000,
        "currency": "MXN",
        "deposit_percent": 30,
        "security_deposit_cents": 200_000,
        "cleaning_fee_cents": 65_000,
        "min_notice_hours": 24,
        "requires_full_payment_days_before": 7,
        "curfew_time": None,
        "allows_multi_day": False,
        "max_events_per_day": 1,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def make_rule(**overrides) -> SimpleNamespace:
    base = {
        "id": "22222222-2222-2222-2222-222222222222",
        "kind": "BASE",
        "name": "Tarifa base",
        "amount_cents": 840_000,
        "per_hour": False,
        "priority": 10,
        "min_guests": 20,
        "max_guests": None,
        "extra_guest_cents": 3_000,
        "valid_from": None,
        "valid_to": None,
        "weekdays": [],
        "specific_dates": [],
        "slot_kind": None,
        "min_days_before": None,
        "max_days_before": None,
        "discount_percent": None,
        "is_active": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def window(days_ahead: int = 45, hours: int = 11) -> tuple[datetime, datetime]:
    start = (datetime.now(UTC) + timedelta(days=days_ahead)).replace(hour=15, minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=hours)


# ── Duración real y DST ───────────────────────────────────────────────────────


def test_duracion_real_es_inmune_al_cambio_de_horario() -> None:
    """02:00 → 03:00 en el día del cambio de horario son 1 h reales, no 0 ni 2 (edge case A3)."""
    start = datetime(2026, 4, 5, 2, 0, tzinfo=MERIDA)
    end = datetime(2026, 4, 5, 3, 0, tzinfo=MERIDA)
    assert float(real_duration_hours(start.astimezone(UTC), end.astimezone(UTC))) == 1.0


def test_duracion_invalida_falla_con_codigo_estable() -> None:
    start, end = window()
    with pytest.raises(AppError) as exc:
        real_duration_hours(end, start)
    assert exc.value.code is ErrorCode.INVALID_DURATION


def test_reserva_cruza_medianoche_cuenta_como_un_dia() -> None:
    """22:00 → 02:00: el venue permite el cruce, el aviso se muestra en la UI (edge case A4)."""
    venue = make_venue(crosses_midnight=True)
    start = datetime(2026, 11, 21, 22, 0, tzinfo=MERIDA).astimezone(UTC)
    end = datetime(2026, 11, 22, 2, 0, tzinfo=MERIDA).astimezone(UTC)
    hours, warnings = validate_window(venue, start, end)
    assert float(hours) == 4.0
    assert any("medianoche" in warning for warning in warnings)
    assert any("limpieza" in warning for warning in warnings)


def test_curfew_rechaza_salida_tardia() -> None:
    venue = make_venue(curfew_time="02:00")
    start = datetime(2026, 11, 21, 20, 0, tzinfo=MERIDA).astimezone(UTC)
    end = datetime(2026, 11, 22, 4, 0, tzinfo=MERIDA).astimezone(UTC)
    with pytest.raises(AppError) as exc:
        validate_window(venue, start, end)
    assert "cierra" in exc.value.message


def test_reserva_en_el_pasado() -> None:
    venue = make_venue()
    start = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(AppError) as exc:
        validate_window(venue, start, start + timedelta(hours=6))
    assert exc.value.code is ErrorCode.DATE_IN_PAST


def test_anticipacion_minima() -> None:
    venue = make_venue(min_notice_hours=72)
    start = datetime.now(UTC) + timedelta(hours=30)
    with pytest.raises(AppError) as exc:
        validate_window(venue, start, start + timedelta(hours=8))
    assert exc.value.code is ErrorCode.MIN_HOURS_NOT_MET


# ── Cotización ────────────────────────────────────────────────────────────────


def test_cotizacion_desglose_completo_y_snapshot() -> None:
    venue = make_venue()
    start, end = window()
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=100,
            pricing_rules=[make_rule()],  # type: ignore[list-item]
        )
    )
    # 840,000 base + 80 invitados extra × 3,000 = 1,080,000
    assert quote.subtotal_cents == 1_080_000 + 65_000
    assert quote.cleaning_cents == 65_000
    assert quote.security_deposit_cents == 200_000
    assert quote.deposit_cents == (quote.total_cents * 30) // 100
    assert quote.total_cents == (
        quote.subtotal_cents + quote.service_fee_cents + quote.tax_cents + quote.security_deposit_cents
    )
    # El snapshot debe ser reconstruible sin las reglas vivas (ADR-004)
    assert quote.snapshot["totals"]["total_cents"] == quote.total_cents
    assert quote.snapshot["engine_version"] == pricing.PRICING_ENGINE_VERSION
    assert quote.snapshot["policy"]["deposit_percent"] == 30
    assert quote.pricing_engine_version == pricing.PRICING_ENGINE_VERSION


def test_cotizacion_es_determinista() -> None:
    """Mismo input ⇒ mismo output. Si no, la idempotencia del hold no sirve."""
    venue = make_venue()
    start, end = window()
    args = dict(venue=venue, start_at=start, end_at=end, guests=60, pricing_rules=[make_rule()])
    first = build_quote(QuoteInput(**args))  # type: ignore[arg-type]
    second = build_quote(QuoteInput(**args))  # type: ignore[arg-type]
    assert first.total_cents == second.total_cents
    assert first.snapshot["totals"] == second.snapshot["totals"]


def test_temporada_alta_aplica_multiplicador() -> None:
    venue = make_venue()
    start, end = window(days_ahead=60)
    seasonal = SimpleNamespace(
        name="Fin de año",
        start_date=(start.astimezone(MERIDA).date() - timedelta(days=2)),
        end_date=(start.astimezone(MERIDA).date() + timedelta(days=2)),
        multiplier=1.5,
        surcharge_cents=None,
        applies_weekdays_only=False,
    )
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=20,
            pricing_rules=[make_rule(extra_guest_cents=None)],  # type: ignore[list-item]
            seasonal_rates=[seasonal],  # type: ignore[list-item]
        )
    )
    assert quote.snapshot["seasonal_multiplier"] == 1.5
    assert "season:Fin de año" in quote.applied_rules


def test_prioridad_de_reglas_especificas_gana() -> None:
    """Una regla con `specific_dates` (feriado) gana a la tarifa base (edge case E28)."""
    venue = make_venue()
    start, end = window(days_ahead=50)
    local_day = start.astimezone(MERIDA).date()
    base = make_rule(amount_cents=500_000, priority=10)
    holiday = make_rule(
        id="33333333-3333-3333-3333-333333333333",
        kind="HOLIDAY",
        name="Día de Muertos",
        amount_cents=1_200_000,
        priority=10,
        specific_dates=[local_day],
        extra_guest_cents=None,
    )
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=20,
            pricing_rules=[base, holiday],  # type: ignore[list-item]
        )
    )
    assert quote.subtotal_cents == 1_200_000 + 65_000
    assert any("HOLIDAY" in label for label in quote.applied_rules)


def test_sin_regla_de_precio_no_se_puede_cotizar() -> None:
    venue = make_venue()
    start, end = window()
    with pytest.raises(AppError) as exc:
        build_quote(
            QuoteInput(venue=venue, start_at=start, end_at=end, guests=50, pricing_rules=[])  # type: ignore[arg-type]
        )
    assert exc.value.code is ErrorCode.NO_PRICING_RULE


def test_capacidad_excedida() -> None:
    venue = make_venue(capacity_max=120)
    start, end = window()
    with pytest.raises(AppError) as exc:
        build_quote(
            QuoteInput(
                venue=venue,  # type: ignore[arg-type]
                start_at=start,
                end_at=end,
                guests=250,
                pricing_rules=[make_rule()],  # type: ignore[list-item]
            )
        )
    assert exc.value.code is ErrorCode.GUESTS_OUT_OF_RANGE
    assert exc.value.details["capacity_max"] == 120


def test_cupon_no_baja_de_cero_ni_toca_la_garantia() -> None:
    venue = make_venue()
    start, end = window()
    coupon = pricing.CouponEvaluation(
        coupon_id="44444444-4444-4444-4444-444444444444", code="FIESTA", kind="FIXED", discount_cents=99_999_999
    )
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=20,
            pricing_rules=[make_rule(extra_guest_cents=None)],  # type: ignore[list-item]
            coupon=coupon,
        )
    )
    assert quote.discount_cents == 840_000 + 65_000  # tope: el subtotal
    assert quote.security_deposit_cents == 200_000  # intacta
    assert quote.total_cents >= 200_000


def test_deposito_cero_por_ciento_cobra_todo() -> None:
    venue = make_venue(deposit_percent=0)
    start, end = window()
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=20,
            pricing_rules=[make_rule()],  # type: ignore[list-item]
        )
    )
    assert quote.deposit_cents == quote.total_cents


def test_comision_se_calcula_sobre_el_subtotal_con_descuento() -> None:
    venue = make_venue()
    start, end = window()
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=20,
            pricing_rules=[make_rule(extra_guest_cents=None)],  # type: ignore[list-item]
            coupon=pricing.CouponEvaluation(code="MENOS10", kind="PERCENT", discount_cents=84_000),
        )
    )
    taxable = quote.subtotal_cents - quote.discount_cents
    assert quote.service_fee_cents == round(taxable * 0.075)


def test_verificar_total_detecta_cambio_de_precio() -> None:
    """El cliente no fija el precio: si su expectativa no cuadra, 422 AMOUNT_MISMATCH."""
    venue = make_venue()
    start, end = window()
    quote = build_quote(
        QuoteInput(
            venue=venue,  # type: ignore[arg-type]
            start_at=start,
            end_at=end,
            guests=20,
            pricing_rules=[make_rule()],  # type: ignore[list-item]
        )
    )
    pricing.verify_snapshot_total(quote.snapshot, quote.total_cents)
    with pytest.raises(AppError) as exc:
        pricing.verify_snapshot_total(quote.snapshot, quote.total_cents - 1)
    assert exc.value.code is ErrorCode.AMOUNT_MISMATCH


# ── Política de cancelación ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("days", "expected_tier"),
    [(45, 100), (30, 100), (29.9, 50), (7, 50), (6.9, 0), (1, 0), (0, 0)],
)
def test_politica_escalonada(days: float, expected_tier: int) -> None:
    tier, explanation = refund_tier(days)
    assert tier == expected_tier
    assert explanation
