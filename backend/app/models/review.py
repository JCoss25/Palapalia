"""Reseñas verificadas, réplicas y reportes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from app.db.types import GUID, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import ReportReason, ReviewStatus

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.venue import Venue


class Review(Base, TimestampMixin, SoftDeleteMixin):
    """Sólo se puede reseñar un booking propio en estado `COMPLETED` (edge case C17)."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="rating_range"),
        UniqueConstraint("booking_id", name="uq_reviews_booking_id"),
        Index("ix_reviews_venue_status", "venue_id", "status", "created_at"),
        Index("ix_reviews_provider", "provider_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Subcalificaciones: limpieza, comunicación, exactitud, relación calidad/precio
    subratings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type_slug: Mapped[str | None] = mapped_column(String(60), nullable=True)
    guest_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[ReviewStatus] = mapped_column(
        String(16), default=ReviewStatus.PUBLISHED, nullable=False
    )
    #: Moderación y señales antifraude
    moderation_flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    is_verified_booking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Ventana para reseñar (30 días después del evento)
    editable_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    provider_rating_at_publish: Mapped[float | None] = mapped_column(Integer, nullable=True)

    venue: Mapped[Venue] = relationship(lazy="selectin")
    author: Mapped[User] = relationship(back_populates="reviews", lazy="selectin")
    reply: Mapped[ReviewReply | None] = relationship(
        back_populates="review", uselist=False, lazy="selectin"
    )


class ReviewReply(Base, TimestampMixin, SoftDeleteMixin):
    """Una réplica por reseña. Editable 72 h; después queda como respuesta oficial."""

    __tablename__ = "review_replies"
    __table_args__ = (UniqueConstraint("review_id", name="uq_review_replies_review_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    review_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    editable_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    edit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    review: Mapped[Review] = relationship(back_populates="reply")


class ReviewReport(Base, TimestampMixin):
    """Reporte de reseña (extorsión, spam, datos personales). Requiere revisión humana."""

    __tablename__ = "review_reports"
    __table_args__ = (
        UniqueConstraint("review_id", "reporter_id", name="uq_review_report_reporter"),
        Index("ix_review_reports_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    review_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[ReportReason] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(280), nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class VenueReport(Base, TimestampMixin):
    """Reporte de catálogo: fotos con derechos de autor, amenidad inexistente, dirección falsa."""

    __tablename__ = "venue_reports"
    __table_args__ = (Index("ix_venue_reports_status", "status", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    amenity_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # COPYRIGHT|FALSE_AMENITY|ADDRESS|OTHER
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(280), nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
