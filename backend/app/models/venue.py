"""Catálogo: direcciones (PostGIS), venues, media, amenidades, categorías y tipos de evento."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, AuditFieldsMixin, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import MediaKind, ModerationStatus, VenueStatus

if TYPE_CHECKING:
    from app.models.availability import AvailabilityRule, BlackoutDate, PricingRule
    from app.models.booking import Booking
    from app.models.provider import ProviderProfile


class Address(Base, TimestampMixin):
    """Dirección con punto geográfico. `geography` (no `geometry`) para medir en metros reales."""

    __tablename__ = "addresses"
    __table_args__ = (
        Index("ix_addresses_point", "point", postgresql_using="gist"),
        Index("ix_addresses_city_state", "city", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    exterior_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    interior_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)  # colonia
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    municipality: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    postal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="MX", nullable=False)

    point: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    #: La geocodificación puede fallar o ser imprecisa: se marca para validación manual (C21)
    geocoded_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    geocoding_provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    geocoding_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    pin_adjusted_by_provider: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_approximate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Texto libre para llegar ("entra por la calle de la iglesia")
    access_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Category(Base, TimestampMixin, SoftDeleteMixin):
    """Palapa, Salón de fiestas, Quinta, Jardín, Terraza, Rooftop, Rancho, Hacienda."""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("slug", name="uq_categories_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    icon_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Amenity(Base, TimestampMixin, SoftDeleteMixin):
    """Alberca, asador, sonido, cocina, baños, estacionamiento, rampa, clima, generador…"""

    __tablename__ = "amenities"
    __table_args__ = (UniqueConstraint("slug", name="uq_amenities_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="GENERAL", nullable=False)
    icon_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: `True` = viene incluida; `False` = extra que se cotiza
    is_included_by_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requires_evidence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EventType(Base, TimestampMixin, SoftDeleteMixin):
    """XV años, boda, cumpleaños, corporativo, baby shower, graduación, concierto privado."""

    __tablename__ = "event_types"
    __table_args__ = (UniqueConstraint("slug", name="uq_event_types_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    requires_curfew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_curfew_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "02:00"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Venue(Base, TimestampMixin, SoftDeleteMixin, AuditFieldsMixin):
    __tablename__ = "venues"
    __table_args__ = (
        CheckConstraint("capacity_min >= 1", name="capacity_min_positive"),
        CheckConstraint("capacity_max >= capacity_min", name="capacity_range"),
        CheckConstraint("min_hours >= 1 AND min_hours <= 72", name="min_hours_range"),
        CheckConstraint("buffer_minutes >= 0 AND buffer_minutes <= 480", name="buffer_range"),
        Index("ix_venues_provider_status", "provider_id", "status"),
        Index("ix_venues_city_state", "city", "state"),
        Index("ix_venues_price", "base_price_cents"),
        Index("ix_venues_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_venues_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    address_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("addresses.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    headline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    house_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    included_items: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")

    #: Copias desnormalizadas de la dirección para búsqueda y tarjetas (se sincronizan al guardar)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: Reglas operativas
    timezone: Mapped[str] = mapped_column(String(64), default="America/Mexico_City", nullable=False)
    capacity_min: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    capacity_max: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    min_hours: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    max_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    #: Buffer de limpieza/montaje entre eventos (edge case A4)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    setup_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    #: Si el venue cobra por bloques predefinidos en lugar de horas libres
    slot_kind_default: Mapped[str] = mapped_column(String(16), default="FULL_DAY", nullable=False)
    allows_multi_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    crosses_midnight: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    curfew_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    max_events_per_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    #: Precio de referencia (el real sale de `PricingRule` + cotización)
    base_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    deposit_percent: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    security_deposit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cleaning_fee_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requires_full_payment_days_before: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    #: Publicación
    status: Mapped[VenueStatus] = mapped_column(String(24), default=VenueStatus.DRAFT, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    instant_book: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_notice_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    max_advance_days: Mapped[int] = mapped_column(Integer, default=540, nullable=False)

    #: Reputación y ranking
    rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    rating_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bookings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    favorites_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Penalizaciones por reportes verificados (amenidades falsas, D22)
    trust_score: Mapped[float] = mapped_column(Numeric(5, 2), default=100, nullable=False)
    quality_flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")

    #: Búsqueda
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    #: Duplicados: huella para deduplicar (D23)
    dedupe_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="SET NULL"), nullable=True
    )

    provider: Mapped[ProviderProfile] = relationship(back_populates="venues", lazy="selectin")
    address: Mapped[Address | None] = relationship(lazy="selectin")
    media: Mapped[list[VenueMedia]] = relationship(
        back_populates="venue", lazy="selectin", order_by="VenueMedia.sort_order"
    )
    categories: Mapped[list[VenueCategory]] = relationship(back_populates="venue", lazy="selectin")
    amenities: Mapped[list[VenueAmenity]] = relationship(back_populates="venue", lazy="selectin")
    event_types: Mapped[list[EventTypeVenue]] = relationship(back_populates="venue", lazy="selectin")
    availability_rules: Mapped[list[AvailabilityRule]] = relationship(
        back_populates="venue", lazy="raise", cascade="all, delete-orphan"
    )
    blackout_dates: Mapped[list[BlackoutDate]] = relationship(
        back_populates="venue", lazy="raise", cascade="all, delete-orphan"
    )
    pricing_rules: Mapped[list[PricingRule]] = relationship(
        back_populates="venue", lazy="raise", cascade="all, delete-orphan"
    )
    bookings: Mapped[list[Booking]] = relationship(back_populates="venue", lazy="raise")

    @property
    def is_bookable(self) -> bool:
        return self.status == VenueStatus.PUBLISHED and self.deleted_at is None

    @property
    def cover_url(self) -> str | None:
        for item in self.media:
            if item.kind == MediaKind.PHOTO and item.moderation_status == ModerationStatus.APPROVED:
                return item.url
        return None


class VenueMedia(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "venue_media"
    __table_args__ = (
        Index("ix_venue_media_venue_order", "venue_id", "sort_order"),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MediaKind] = mapped_column(String(16), default=MediaKind.PHOTO, nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blurhash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Moderación: derechos de autor, contenido inapropiado (D20)
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        String(16), default=ModerationStatus.PENDING, nullable=False
    )
    moderation_notes: Mapped[str | None] = mapped_column(String(400), nullable=True)
    reported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: EXIF limpio: se elimina geolocalización del archivo subido
    exif_stripped: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    capture_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="media")


class VenueCategory(Base, TimestampMixin):
    __tablename__ = "venue_categories"
    __table_args__ = (UniqueConstraint("venue_id", "category_id", name="uq_venue_category"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    venue: Mapped[Venue] = relationship(back_populates="categories")
    category: Mapped[Category] = relationship(lazy="joined")


class VenueAmenity(Base, TimestampMixin):
    __tablename__ = "venue_amenities"
    __table_args__ = (UniqueConstraint("venue_id", "amenity_id", name="uq_venue_amenity"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    amenity_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("amenities.id", ondelete="CASCADE"), nullable=False
    )
    is_included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extra_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Reportes de "la amenidad no existe" (D22)
    reported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    venue: Mapped[Venue] = relationship(back_populates="amenities")
    amenity: Mapped[Amenity] = relationship(lazy="joined")


class EventTypeVenue(Base, TimestampMixin):
    __tablename__ = "event_type_venues"
    __table_args__ = (UniqueConstraint("venue_id", "event_type_id", name="uq_event_type_venue"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    event_type_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False
    )
    max_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="event_types")
    event_type: Mapped[EventType] = relationship(lazy="joined")


class Favorite(Base, TimestampMixin):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "venue_id", name="uq_favorite_user_venue"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False, index=True
    )
