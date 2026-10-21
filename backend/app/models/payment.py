"""Pagos, reembolsos, payouts, ledger, facturas y disputas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, AuditFieldsMixin, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import (
    DisputeKind,
    DisputeStatus,
    LedgerAccount,
    LedgerDirection,
    PaymentKind,
    PaymentStatus,
    PayoutStatus,
    RefundStatus,
)

if TYPE_CHECKING:
    from app.models.booking import Booking


class Payment(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    """Un intento de cobro. `provider_intent_id` es único ⇒ imposible cobrar dos veces lo mismo."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_intent_id", name="uq_payment_provider_intent"),
        CheckConstraint("amount_cents > 0", name="payment_amount_positive"),
        CheckConstraint("refunded_cents >= 0 AND refunded_cents <= amount_cents", name="refund_bounds"),
        Index("ix_payments_booking_status", "booking_id", "status"),
        Index("ix_payments_client", "client_id", "created_at"),
        Index("ix_payments_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[PaymentKind] = mapped_column(String(12), default=PaymentKind.DEPOSIT, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        String(24), default=PaymentStatus.REQUIRES_PAYMENT_METHOD, nullable=False
    )

    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Comisión de plataforma retenida (basis points aplicados al momento del cobro)
    application_fee_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Monto que va al proveedor vía destination charge
    transfer_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    provider: Mapped[str] = mapped_column("gateway", String(24), default="stripe", nullable=False)
    provider_intent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_charge_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_method_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payment_method_brand: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payment_method_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)

    #: Idempotencia y conciliación
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    client_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 3DS/SCA: se conserva la acción pendiente para reintentar (edge case B9)
    requires_action_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    next_action_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    #: Depósito de garantía: se retiene y se libera/cobra por job (edge case B14)
    is_security_deposit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    held_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    captured_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    paid_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Marca de reconciliación: pagos cuyo hold ya había expirado (edge case A2)
    reconciled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    reconciliation_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    statement_descriptor: Mapped[str | None] = mapped_column(String(22), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False, server_default="{}"
    )

    booking: Mapped[Booking] = relationship(back_populates="payments")
    refunds: Mapped[list[Refund]] = relationship(back_populates="payment", lazy="selectin")


class Refund(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("payment_id", "provider_refund_id", name="uq_refund_provider"),
        CheckConstraint("amount_cents > 0", name="refund_amount_positive"),
        Index("ix_refunds_booking", "booking_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    payment_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    status: Mapped[RefundStatus] = mapped_column(String(16), default=RefundStatus.PENDING, nullable=False)

    reason: Mapped[str] = mapped_column(String(40), nullable=False)  # client_cancel | hold_expired | ...
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Política escalonada aplicada (100 / 50 / 0) y el monto que correspondía
    tier_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier_eligible_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Retenciones: comisión de plataforma no reembolsada en ciertos casos
    fee_retained_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    provider_refund_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(280), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    initiated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Reembolso automático por sistema (hold expirado, pago duplicado)
    is_automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="refunds")


class Payout(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "payouts"
    __table_args__ = (
        UniqueConstraint("provider_id", "period_start", "period_end", name="uq_payout_period"),
        CheckConstraint("net_cents = gross_cents - fee_cents", name="payout_net_consistent"),
        Index("ix_payouts_provider_status", "provider_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    gross_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    net_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    status: Mapped[PayoutStatus] = mapped_column(String(16), default=PayoutStatus.PENDING, nullable=False)

    provider_transfer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    released_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Congelado por disputa/chargeback (edge case B11)
    frozen_reason: Mapped[str | None] = mapped_column(String(280), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    frozen_by_dispute_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class LedgerEntry(Base, TimestampMixin):
    """Partida doble mínima: cada movimiento de dinero deja dos asientos."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_booking", "booking_id", "created_at"),
        Index("ix_ledger_account", "account", "direction", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    payout_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    account: Mapped[LedgerAccount] = mapped_column(String(24), nullable=False)
    direction: Mapped[LedgerDirection] = mapped_column(String(8), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Grupo de asientos que deben sumar cero
    entry_group: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class Invoice(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    """CFDI 4.0 (México). En MVP se marca para timbrado manual/asistido; F2 lo automatiza."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("uuid_fiscal", name="uq_invoice_uuid_fiscal"),
        Index("ix_invoices_booking", "booking_id"),
        Index("ix_invoices_rfc", "rfc"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="CLIENT", nullable=False)  # CLIENT|PROVIDER
    rfc: Mapped[str] = mapped_column(String(13), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_regime: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cfdi_use: Mapped[str | None] = mapped_column(String(10), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)

    uuid_fiscal: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cfdi_status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    xml_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stamped_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: La factura se conserva 5 años aunque el usuario pida borrado (LFPDPPP vs. fiscal)
    retention_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class Dispute(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "disputes"
    __table_args__ = (
        UniqueConstraint("booking_id", "kind", name="uq_dispute_booking_kind"),
        Index("ix_disputes_status_deadline", "status", "deadline_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False
    )
    opened_by_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    against_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[DisputeKind] = mapped_column(String(20), nullable=False)
    status: Mapped[DisputeStatus] = mapped_column(String(24), default=DisputeStatus.OPEN, nullable=False)

    amount_claimed_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_awarded_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: Plazo: chargebacks tienen ventana del banco; reclamos internos 7 días
    deadline_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    provider_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_responded_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    frozen_payout_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("payouts.id", ondelete="SET NULL"), nullable=True
    )
    provider_dispute_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # dp_*


class DisputeEvidence(Base, TimestampMixin):
    __tablename__ = "dispute_evidence"
    __table_args__ = (Index("ix_dispute_evidence_dispute", "dispute_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), default="DOCUMENT", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_to_provider: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
