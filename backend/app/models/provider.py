"""Perfil de proveedor: estado de verificación, capacidad de cobro y métricas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, AuditFieldsMixin, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import ProviderStatus

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.venue import Venue


class ProviderProfile(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "provider_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_provider_profiles_user_id"),
        Index("ix_providers_status", "status"),
        Index("ix_providers_city", "city"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    #: Nombre comercial y de contacto
    business_name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(20), nullable=True)  # RFC
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    whatsapp_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)

    status: Mapped[ProviderStatus] = mapped_column(
        String(24), default=ProviderStatus.DRAFT, nullable=False
    )
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    suspended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Pagos: sin esto no se puede publicar con cobro en línea (edge case B12)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # acct_*
    payment_provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payouts_blocked_reason: Mapped[str | None] = mapped_column(String(280), nullable=True)
    #: Modo degradado: publicar sólo con apartado sin cobro en línea
    offline_booking_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Operación
    instant_book_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    response_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancellation_policy: Mapped[str] = mapped_column(
        String(24), default="TIERED_STANDARD", nullable=False
    )

    #: Reputación desnormalizada (se recalcula por job; nunca se edita a mano)
    rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    rating_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_bookings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancelled_by_provider: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    #: Señales de riesgo calculadas (documentos duplicados, disputas, reseñas anómalas)
    risk_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    risk_flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False, server_default="{}"
    )

    user: Mapped[User] = relationship(back_populates="provider_profile", lazy="selectin")
    venues: Mapped[list[Venue]] = relationship(
        back_populates="provider", lazy="raise", cascade="all, delete-orphan"
    )
