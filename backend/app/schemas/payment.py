"""Esquemas de pagos, reembolsos, payouts, facturas y disputas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import PaymentKind, PaymentStatus, PayoutStatus, RefundStatus
from app.schemas.common import ORMModel


class PaymentIntentRequest(BaseModel):
    booking_id: uuid.UUID
    kind: PaymentKind = PaymentKind.DEPOSIT
    #: `card` cobra ya; `spei`/`oxxo` generan referencia (conciliación manual, S1)
    method: Literal["card", "spei", "oxxo"] = "card"
    save_payment_method: bool = True
    amount_cents: int | None = Field(
        default=None,
        ge=1,
        description="Sólo se acepta para pagos parciales; el servidor valida contra el saldo",
    )
    payment_method_id: str | None = Field(default=None, description="Token del SDK del cliente")
    return_url: str | None = Field(default=None, max_length=1024)


class PaymentIntentResponse(BaseModel):
    payment_id: uuid.UUID
    booking_id: uuid.UUID
    status: PaymentStatus
    amount_cents: int
    currency: str = "MXN"
    client_secret: str | None = None
    next_action_url: str | None = Field(
        default=None, description="Presente si el banco pide 3DS/SCA (edge case B9)"
    )
    requires_action: bool = False
    publishable_key: str | None = None
    expires_at: datetime | None = None
    idempotent_replay: bool = False


class PaymentOut(ORMModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    kind: PaymentKind
    status: PaymentStatus
    amount_cents: int
    currency: str = "MXN"
    refunded_cents: int = 0
    application_fee_cents: int = 0
    transfer_cents: int = 0
    is_security_deposit: bool = False
    payment_method_type: str | None = None
    payment_method_brand: str | None = None
    payment_method_last4: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    receipt_url: str | None = None
    created_at: datetime
    paid_at: datetime | None = None
    released_at: datetime | None = None
    reconciled_at: datetime | None = None
    reconciliation_action: str | None = None


class RefundCreateRequest(BaseModel):
    payment_id: uuid.UUID
    amount_cents: int | None = Field(default=None, ge=1, description="Vacío = reembolso total")
    reason: Literal[
        "client_cancel",
        "provider_cancel",
        "hold_expired",
        "duplicate_payment",
        "dispute_resolved",
        "goodwill",
        "venue_not_as_described",
    ]
    notes: str | None = Field(default=None, max_length=500)
    apply_policy: bool = Field(
        default=True,
        description="Si true, el monto se recorta a la política escalonada; si false requiere rol ADMIN",
    )
    release_coupon: bool = True


class RefundOut(ORMModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    booking_id: uuid.UUID
    amount_cents: int
    currency: str = "MXN"
    status: RefundStatus
    reason: str
    tier_percent: int | None = None
    tier_eligible_cents: int | None = None
    fee_retained_cents: int = 0
    is_automatic: bool = False
    failure_reason: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class RefundPolicyPreviewRequest(BaseModel):
    booking_id: uuid.UUID
    cancel_at: datetime | None = Field(
        default=None, description="Por defecto: ahora. Sirve para simular escenarios en la UI"
    )


class RefundPolicyPreviewOut(BaseModel):
    tier_percent: int = Field(description="100 | 50 | 0")
    eligible_refund_cents: int
    fee_retained_cents: int
    security_deposit_refund_cents: int
    days_until_event: float
    policy_explanation: str = Field(examples=["100% hasta 30 días antes; 50% entre 7 y 30 días; 0% a menos de 7 días."])
    next_tier_deadline: date | None = None


class PayoutOut(ORMModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    period_start: date
    period_end: date
    gross_cents: int
    fee_cents: int
    net_cents: int
    currency: str = "MXN"
    status: PayoutStatus
    frozen_reason: str | None = None
    released_at: datetime | None = None
    breakdown: dict = Field(default_factory=dict)
    created_at: datetime


class PayoutReleaseRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=500)
    force: bool = Field(default=False, description="Ignora el congelamiento; requiere ADMIN")


class InvoiceRequest(BaseModel):
    booking_id: uuid.UUID
    rfc: str = Field(min_length=12, max_length=13, examples=["XAXX010101000"])
    legal_name: str = Field(min_length=3, max_length=200)
    tax_regime: str = Field(examples=["612", "601", "626"])
    cfdi_use: str = Field(default="G03", examples=["G03", "D01", "S01"])
    postal_code: str = Field(pattern=r"^\d{5}$")
    email: str | None = None


class InvoiceOut(ORMModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    kind: str
    rfc: str
    legal_name: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    currency: str = "MXN"
    uuid_fiscal: str | None = None
    cfdi_status: str
    pdf_url: str | None = None
    xml_url: str | None = None
    stamped_at: datetime | None = None


class WebhookAckOut(BaseModel):
    received: bool = True
    event_id: str | None = None
    duplicated: bool = False
    action: Literal["processed", "ignored", "reconciled", "queued"] = "processed"


class DisputeOut(ORMModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    kind: str
    status: str
    amount_claimed_cents: int | None = None
    amount_awarded_cents: int | None = None
    description: str
    deadline_at: datetime | None = None
    provider_response: str | None = None
    resolution_notes: str | None = None
    frozen_payout_id: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class LedgerSummaryOut(BaseModel):
    booking_id: uuid.UUID
    entries: list[dict]
    balanced: bool = Field(description="Los asientos del grupo deben sumar cero")
