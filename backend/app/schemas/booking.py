"""Esquemas de reservas: hold, booking, transiciones, check-in y contrato."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import BookingState, HoldStatus, SlotKind
from app.schemas.common import ORMModel
from app.schemas.venue import QuoteOut


class HoldCreateRequest(BaseModel):
    venue_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    guests: int = Field(ge=1, le=10000)
    slot_kind: SlotKind = SlotKind.FULL_DAY
    event_type_slug: str | None = None
    extras: list[str] = Field(default_factory=list, max_length=20)
    coupon_code: str | None = Field(default=None, max_length=40)
    special_requests: str | None = Field(default=None, max_length=2000)
    #: Se exige que el cliente haya visto la misma cotización que el servidor
    quote_total_cents: int | None = Field(
        default=None,
        description="Si se envía y no coincide con el recálculo, responde 409 QUOTE_EXPIRED/AMOUNT_MISMATCH",
    )

    @model_validator(mode="after")
    def _window(self) -> HoldCreateRequest:
        if self.end_at <= self.start_at:
            raise ValueError("end_at debe ser posterior a start_at")
        return self


class HoldOut(ORMModel):
    id: uuid.UUID
    folio: str = Field(examples=["HD-7K3M21"])
    venue_id: uuid.UUID
    status: HoldStatus
    start_at: datetime
    end_at: datetime
    venue_timezone: str
    guests: int
    slot_kind: SlotKind
    total_cents: int
    deposit_cents: int
    currency: str = "MXN"
    expires_at: datetime
    seconds_remaining: int
    can_extend: bool
    quote_snapshot: dict
    venue_name: str | None = None
    venue_cover_url: str | None = None


class HoldCreateResponse(HoldOut):
    quote: QuoteOut
    next_step: Literal["payment"] = "payment"
    idempotent_replay: bool = False


class BookingCreateRequest(BaseModel):
    hold_id: uuid.UUID
    payment_method: Literal["card", "spei", "oxxo", "cash_at_venue"] = "card"
    accept_contract: bool = Field(description="Aceptación del contrato de apartado")
    save_payment_method: bool = True
    invoice_requested: bool = False
    rfc: str | None = Field(default=None, max_length=13)
    legal_name: str | None = Field(default=None, max_length=200)
    tax_regime: str | None = Field(default=None, max_length=10)
    cfdi_use: str | None = Field(default=None, max_length=10)
    postal_code: str | None = Field(default=None, pattern=r"^\d{5}$")


class BookingSummaryOut(ORMModel):
    id: uuid.UUID
    folio: str
    state: BookingState
    venue_id: uuid.UUID
    venue_name: str | None = None
    venue_cover_url: str | None = None
    city: str | None = None
    start_at: datetime
    end_at: datetime
    venue_timezone: str
    guests: int
    total_cents: int
    paid_cents: int
    balance_cents: int
    currency: str = "MXN"
    created_at: datetime
    can_cancel: bool
    can_reschedule: bool
    can_review: bool
    can_pay_balance: bool
    next_action: Literal["pay_deposit", "pay_balance", "check_in", "review", "none"] = "none"
    client_name: str | None = None
    provider_name: str | None = None


class BookingDetailOut(BookingSummaryOut):
    previous_state: BookingState | None = None
    slot_kind: SlotKind
    event_type_slug: str | None = None
    special_requests: str | None = None
    blocked_from: datetime
    blocked_to: datetime
    subtotal_cents: int
    extras_cents: int
    cleaning_cents: int
    discount_cents: int
    service_fee_cents: int
    tax_cents: int
    security_deposit_cents: int
    deposit_cents: int
    refunded_cents: int
    coupon_code: str | None = None
    quote_snapshot: dict
    rules_snapshot: dict
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    qr_token: str | None = Field(default=None, description="Sólo para el cliente titular")
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    refund_tier_percent: int | None = None
    contract_url: str | None = None
    payments: list[PaymentSummaryOut] = Field(default_factory=list)
    status_history: list[StatusHistoryOut] = Field(default_factory=list)
    dispute_id: uuid.UUID | None = None


class PaymentSummaryOut(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    amount_cents: int
    currency: str = "MXN"
    refunded_cents: int = 0
    is_security_deposit: bool = False
    created_at: datetime
    paid_at: datetime | None = None
    failure_code: str | None = None


class StatusHistoryOut(ORMModel):
    from_state: BookingState | None = None
    to_state: BookingState
    actor_role: str | None = None
    reason: str | None = None
    created_at: datetime
    idempotent_replay: bool = False


class CancelBookingRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    reason_code: Literal[
        "change_of_plans", "found_other_venue", "weather", "budget", "provider_unresponsive", "other"
    ] = "other"
    request_refund: bool = True
    acknowledge_policy: bool = Field(
        description="El cliente debe confirmar que leyó la política de cancelación"
    )


class CancelBookingResponse(BaseModel):
    booking_id: uuid.UUID
    state: BookingState
    refund_cents: int
    refund_tier_percent: int
    refund_id: uuid.UUID | None = None
    refund_status: str | None = None
    message: str
    policy_explanation: str


class RescheduleRequest(BaseModel):
    new_start_at: datetime
    new_end_at: datetime
    reason: str | None = Field(default=None, max_length=300)
    acknowledge_fee: bool = False

    @model_validator(mode="after")
    def _window(self) -> RescheduleRequest:
        if self.new_end_at <= self.new_start_at:
            raise ValueError("new_end_at debe ser posterior a new_start_at")
        return self


class CheckInRequest(BaseModel):
    qr_token: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    note: str | None = Field(default=None, max_length=300)


class CheckOutRequest(BaseModel):
    damages_cents: int | None = Field(default=None, ge=0)
    damage_notes: str | None = Field(default=None, max_length=1000)
    evidence_keys: list[str] = Field(default_factory=list, max_length=10)
    extra_charges_cents: int | None = Field(default=None, ge=0)
    release_security_deposit: bool = True


class NoShowRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    evidence_keys: list[str] = Field(min_length=1, max_length=10)
    wait_minutes: int = Field(default=30, ge=0, le=480)
    witnesses: list[str] = Field(default_factory=list, max_length=5)


class CompleteRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    release_security_deposit: bool = True
    actual_guests: int | None = Field(default=None, ge=0)


class DisputeCreateRequest(BaseModel):
    kind: Literal["CHARGEBACK", "CLIENT_CLAIM", "PROVIDER_CLAIM", "DAMAGE", "NO_SHOW"]
    description: str = Field(min_length=20, max_length=4000)
    amount_claimed_cents: int | None = Field(default=None, ge=0)
    evidence_keys: list[str] = Field(default_factory=list, max_length=10)
    contacted_provider: bool = False


class DisputeResolveRequest(BaseModel):
    resolution: Literal["RESOLVED_CLIENT", "RESOLVED_PROVIDER", "RESOLVED_SPLIT"]
    amount_to_client_cents: int = Field(default=0, ge=0)
    amount_to_provider_cents: int = Field(default=0, ge=0)
    notes: str = Field(min_length=10, max_length=2000)
    unfreeze_payout: bool = True


class AvailabilityCheckQuery(BaseModel):
    from_date: date
    to_date: date
    slot: SlotKind | None = None
    guests: int | None = Field(default=None, ge=1)


class AvailabilityCheckOut(BaseModel):
    venue_id: uuid.UUID
    window: str
    is_available: bool
    conflicts: list[dict] = Field(
        default_factory=list, description="Reservas/holds que estorban, sin datos personales"
    )
    suggested_slots: list[dict] = Field(default_factory=list)


for _forward_ref_model in (BookingDetailOut,):
    _forward_ref_model.model_rebuild()
