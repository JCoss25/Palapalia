"""Mensajería entre cliente y proveedor, con detección de bypass de plataforma."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import ContactFlagAction, MessageKind, ModerationStatus

if TYPE_CHECKING:
    from app.models.user import User


class MessageThread(Base, TimestampMixin, SoftDeleteMixin):
    """Un hilo por reserva (o por consulta previa venue+cliente)."""

    __tablename__ = "message_threads"
    __table_args__ = (
        UniqueConstraint("venue_id", "client_id", "booking_id", name="uq_thread_context"),
        Index("ix_threads_client", "client_id", "last_message_at"),
        Index("ix_threads_provider", "provider_id", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )

    subject: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(160), nullable=True)
    client_unread: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_unread: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Riesgo acumulado de la conversación (bypass repetido)
    risk_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)

    messages: Mapped[list[Message]] = relationship(
        back_populates="thread", lazy="raise", order_by="Message.created_at"
    )


class Message(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_thread_created", "thread_id", "created_at"),
        Index("ix_messages_sender", "sender_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("message_threads.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[MessageKind] = mapped_column(String(16), default=MessageKind.TEXT, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    moderation_status: Mapped[ModerationStatus] = mapped_column(
        String(16), default=ModerationStatus.APPROVED, nullable=False
    )
    #: Puntaje 0..100 de "probable intento de sacar la conversación de la plataforma" (C19)
    bypass_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    detected_entities: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")

    thread: Mapped[MessageThread] = relationship(back_populates="messages")
    sender: Mapped[User] = relationship(back_populates="messages", lazy="selectin")


class ContactFlag(Base, TimestampMixin):
    """Bandera por mensaje. Se advierte y se revisa antes de bloquear (edge case C19)."""

    __tablename__ = "contact_flags"
    __table_args__ = (
        Index("ix_contact_flags_status", "action", "reviewed_at"),
        Index("ix_contact_flags_thread", "thread_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("message_threads.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)  # PHONE|EMAIL|URL|SOCIAL|IBAN
    entity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    action: Mapped[ContactFlagAction] = mapped_column(
        String(12), default=ContactFlagAction.WARN, nullable=False
    )
    warning_shown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(400), nullable=True)
