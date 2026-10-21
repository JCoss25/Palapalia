"""Usuario, sesiones, OTP y documentos de verificación."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, AuditFieldsMixin, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import Role, UserStatus, VerificationDocType, VerificationStatus

if TYPE_CHECKING:
    from app.models.booking import Booking
    from app.models.messaging import Message
    from app.models.provider import ProviderProfile
    from app.models.review import Review


class User(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        UniqueConstraint("phone_e164", name="uq_users_phone_e164"),
        Index("ix_users_role_status", "role", "status"),
        Index("ix_users_search", "full_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: colapsa alias `+` y googlemail→gmail: evita cuentas duplicadas (edge case C16)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    role: Mapped[Role] = mapped_column(String(20), nullable=False, default=Role.CLIENT)
    status: Mapped[UserStatus] = mapped_column(
        String(24), nullable=False, default=UserStatus.PENDING_VERIFICATION
    )

    full_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), default="es-MX", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Mexico_City", nullable=False)

    email_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Términos y condiciones versionados (edge case C18)
    tos_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tos_accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Bloqueos administrativos
    banned_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ban_reason: Mapped[str | None] = mapped_column(String(280), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Cumplimiento: cuándo se anonimizó la PII (se conserva el histórico contable)
    pii_erased_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    data_export_requested_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    provider_profile: Mapped[ProviderProfile | None] = relationship(
        back_populates="user", uselist=False, lazy="selectin"
    )
    bookings: Mapped[list[Booking]] = relationship(
        back_populates="client", foreign_keys="Booking.client_id", lazy="raise"
    )
    reviews: Mapped[list[Review]] = relationship(back_populates="author", lazy="raise")
    messages: Mapped[list[Message]] = relationship(back_populates="sender", lazy="raise")

    @property
    def is_active(self) -> bool:
        return self.status in {UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION}

    @property
    def is_staff(self) -> bool:
        return self.role in {Role.ADMIN, Role.SUPPORT}


class RefreshToken(Base, TimestampMixin):
    """Rotación de refresh tokens por familia: reutilizar uno revoca toda la familia."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family", "family_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    replaced_by_jti: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(280), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OtpCode(Base, TimestampMixin):
    """OTP de teléfono/email. Se guarda el hash, nunca el código."""

    __tablename__ = "otp_codes"
    __table_args__ = (Index("ix_otp_user_purpose", "user_id", "purpose"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    destination: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)  # phone_verify | login | reset
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class VerificationDocument(Base, TimestampMixin):
    """Documento del proveedor. Nunca se sirve sin URL firmada."""

    __tablename__ = "verification_documents"
    __table_args__ = (Index("ix_verif_docs_provider_status", "provider_id", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    doc_type: Mapped[VerificationDocType] = mapped_column(String(30), nullable=False)
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_on: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    status: Mapped[VerificationStatus] = mapped_column(
        String(16), default=VerificationStatus.PENDING, nullable=False
    )
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Detección de documentos repetidos entre cuentas (edge case C15)
    fraud_signals: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
