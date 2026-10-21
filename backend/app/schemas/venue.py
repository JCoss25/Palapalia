"""Esquemas de proveedor, venues, catálogo y cotización."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, model_validator

from app.models.enums import (
    MediaKind,
    ModerationStatus,
    PricingKind,
    ProviderStatus,
    SlotKind,
    VenueStatus,
    VerificationDocType,
    VerificationStatus,
)
from app.schemas.common import ORMModel, GeoPoint, Money, PhoneE164, Slug, VenueSort


# ── Proveedor ──────────────────────────────────────────────────────────────────


class ProviderApplyRequest(BaseModel):
    business_name: str = Field(min_length=3, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    tax_id: str | None = Field(default=None, max_length=20, examples=["XAXX010101000"])
    description: str | None = Field(default=None, max_length=2000)
    phone: PhoneE164 | None = None
    whatsapp: PhoneE164 | None = None
    website_url: HttpUrl | None = None
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    accepts_tos_provider: bool = Field(description="Aceptación del contrato de proveedor")
    cancellation_policy: Literal["TIERED_STANDARD", "FLEXIBLE", "STRICT"] = "TIERED_STANDARD"


class ProviderOut(ORMModel):
    id: uuid.UUID
    business_name: str
    legal_name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    city: str | None = None
    state: str | None = None
    status: ProviderStatus
    status_reason: str | None = None
    payouts_enabled: bool = False
    offline_booking_only: bool = False
    instant_book_enabled: bool = False
    cancellation_policy: str = "TIERED_STANDARD"
    rating_avg: float | None = None
    rating_count: int = 0
    completed_bookings: int = 0
    response_rate: float | None = None
    created_at: datetime


class ProviderPublicOut(ProviderOut):
    """Vista pública: sin RFC, sin estado de pagos, sin notas de revisión."""

    venues_count: int = 0


class ProviderPatchRequest(BaseModel):
    business_name: str | None = Field(default=None, min_length=3, max_length=160)
    legal_name: str | None = None
    tax_id: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    phone: PhoneE164 | None = None
    whatsapp: PhoneE164 | None = None
    website_url: HttpUrl | None = None
    city: str | None = None
    state: str | None = None
    instant_book_enabled: bool | None = None
    cancellation_policy: Literal["TIERED_STANDARD", "FLEXIBLE", "STRICT"] | None = None


class VerificationDocumentOut(ORMModel):
    id: uuid.UUID
    doc_type: VerificationDocType
    status: VerificationStatus
    mime_type: str
    expires_on: datetime | None = None
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class VerificationDocumentCreate(BaseModel):
    doc_type: VerificationDocType
    file_key: str = Field(description="Clave devuelta por el endpoint de URL pre-firmada")
    file_sha256: str = Field(min_length=64, max_length=64)
    mime_type: str = Field(examples=["image/jpeg", "application/pdf"])
    expires_on: datetime | None = None


class VerificationStatusOut(BaseModel):
    provider_status: ProviderStatus
    payouts_enabled: bool
    offline_booking_only: bool
    required_documents: list[VerificationDocType]
    submitted_documents: list[VerificationDocumentOut]
    missing_documents: list[VerificationDocType]
    blocking_reasons: list[str] = Field(
        description="Por qué no puede publicar o cobrar todavía", default_factory=list
    )
    can_publish: bool
    can_receive_payments: bool


class ProviderDashboardOut(BaseModel):
    provider_id: uuid.UUID
    period: Literal["30d", "90d", "mtd", "ytd"]
    occupancy_rate: float = Field(ge=0, le=1)
    revenue_cents: int
    revenue_currency: str = "MXN"
    confirmed_bookings: int
    pending_requests: int
    upcoming_events: int
    cancelled_by_client: int
    cancelled_by_provider: int
    avg_ticket_cents: int
    rating_avg: float | None
    response_time_minutes: int | None
    next_payout_cents: int | None = None
    next_payout_date: date | None = None
    pending_tasks: list[dict] = Field(default_factory=list)


class ProviderApproveRequest(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=500)
    payouts_enabled: bool | None = None


# ── Dirección ──────────────────────────────────────────────────────────────────


class AddressIn(BaseModel):
    street: str | None = Field(default=None, max_length=200)
    exterior_number: str | None = None
    neighborhood: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=2, max_length=120)
    municipality: str | None = None
    state: str = Field(min_length=2, max_length=120)
    postal_code: str | None = Field(default=None, pattern=r"^\d{5}$")
    country_code: str = Field(default="MX", pattern=r"^[A-Z]{2}$")
    point: GeoPoint | None = Field(
        default=None, description="Si falta, se geocodifica; puede quedar `is_approximate`"
    )
    access_notes: str | None = Field(default=None, max_length=500)


class AddressOut(ORMModel):
    id: uuid.UUID
    street: str | None = None
    exterior_number: str | None = None
    neighborhood: str | None = None
    city: str
    municipality: str | None = None
    state: str
    postal_code: str | None = None
    country_code: str = "MX"
    point: GeoPoint | None = None
    is_approximate: bool = False
    pin_adjusted_by_provider: bool = False
    access_notes: str | None = None


# ── Media ──────────────────────────────────────────────────────────────────────


class VenueMediaOut(ORMModel):
    id: uuid.UUID
    kind: MediaKind
    url: str
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    blurhash: str | None = None
    sort_order: int
    alt_text: str | None = None
    is_cover: bool = False
    moderation_status: ModerationStatus


class MediaUploadRequest(BaseModel):
    kind: MediaKind = MediaKind.PHOTO
    filename: str = Field(max_length=200)
    mime_type: str = Field(examples=["image/jpeg", "video/mp4"])
    size_bytes: int = Field(gt=0, le=200 * 1024 * 1024)
    alt_text: str | None = Field(default=None, max_length=200)
    checksum_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class MediaUploadTicket(BaseModel):
    media_id: uuid.UUID
    upload_url: str
    storage_key: str
    fields: dict[str, str] = Field(default_factory=dict)
    expires_in_seconds: int
    max_size_bytes: int


class MediaReorderRequest(BaseModel):
    order: list[uuid.UUID] = Field(min_length=1, description="IDs en el orden final")
    cover_media_id: uuid.UUID | None = None


# ── Venue ──────────────────────────────────────────────────────────────────────


class VenueCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    headline: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    house_rules: str | None = Field(default=None, max_length=4000)
    included_items: list[str] = Field(default_factory=list, max_length=40)

    address: AddressIn
    timezone: str = Field(default="America/Mexico_City", examples=["America/Merida"])
    category_slugs: list[Slug] = Field(min_length=1, max_length=3)
    amenity_slugs: list[Slug] = Field(default_factory=list, max_length=40)
    event_type_slugs: list[Slug] = Field(default_factory=list, max_length=12)

    capacity_min: int = Field(ge=1, le=10000)
    capacity_max: int = Field(ge=1, le=10000)
    min_hours: int = Field(default=4, ge=1, le=72)
    max_hours: int = Field(default=24, ge=1, le=72)
    buffer_minutes: int = Field(default=60, ge=0, le=480)
    slot_kind_default: SlotKind = SlotKind.FULL_DAY
    allows_multi_day: bool = False
    crosses_midnight: bool = True
    curfew_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    base_price_cents: int | None = Field(default=None, ge=0)
    currency: str = Field(default="MXN", min_length=3, max_length=3)
    deposit_percent: int = Field(default=30, ge=0, le=100)
    security_deposit_cents: int | None = Field(default=None, ge=0)
    cleaning_fee_cents: int = Field(default=0, ge=0)
    instant_book: bool = False
    min_notice_hours: int = Field(default=24, ge=0, le=8760)

    @model_validator(mode="after")
    def _coherence(self) -> VenueCreateRequest:
        if self.capacity_max < self.capacity_min:
            raise ValueError("capacity_max debe ser mayor o igual a capacity_min")
        if self.max_hours < self.min_hours:
            raise ValueError("max_hours debe ser mayor o igual a min_hours")
        return self


class VenuePatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=160)
    headline: str | None = None
    description: str | None = None
    house_rules: str | None = None
    included_items: list[str] | None = None
    address: AddressIn | None = None
    category_slugs: list[Slug] | None = None
    amenity_slugs: list[Slug] | None = None
    event_type_slugs: list[Slug] | None = None
    capacity_min: int | None = Field(default=None, ge=1)
    capacity_max: int | None = Field(default=None, ge=1)
    min_hours: int | None = Field(default=None, ge=1, le=72)
    max_hours: int | None = Field(default=None, ge=1, le=72)
    buffer_minutes: int | None = Field(default=None, ge=0, le=480)
    base_price_cents: int | None = Field(default=None, ge=0)
    deposit_percent: int | None = Field(default=None, ge=0, le=100)
    security_deposit_cents: int | None = Field(default=None, ge=0)
    cleaning_fee_cents: int | None = Field(default=None, ge=0)
    instant_book: bool | None = None
    status: Literal["DRAFT", "PUBLISHED", "PAUSED"] | None = Field(
        default=None, description="El proveedor no puede auto-suspenderse ni auto-aprobarse"
    )


class VenueSummaryOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    headline: str | None = None
    city: str
    state: str
    neighborhood: str | None = None
    capacity_min: int
    capacity_max: int
    base_price_cents: int | None = None
    currency: str = "MXN"
    rating_avg: float | None = None
    rating_count: int = 0
    instant_book: bool = False
    status: VenueStatus
    cover_url: str | None = None
    distance_km: float | None = Field(
        default=None, description="Presente sólo si la búsqueda incluyó lat/lng"
    )
    #: Disponibilidad del rango consultado
    is_available: bool | None = None
    next_available_date: date | None = None
    price_from_cents: int | None = None
    provider_name: str | None = None
    provider_verified: bool = False
    sponsored: bool = Field(default=False, description="Etiqueta 'Patrocinado' obligatoria en UI")
    badges: list[str] = Field(default_factory=list)


class VenueDetailOut(VenueSummaryOut):
    description: str | None = None
    house_rules: str | None = None
    included_items: list[str] = Field(default_factory=list)
    address: AddressOut | None = None
    timezone: str
    min_hours: int
    max_hours: int
    buffer_minutes: int
    slot_kind_default: SlotKind
    allows_multi_day: bool
    crosses_midnight: bool
    curfew_time: str | None = None
    deposit_percent: int
    security_deposit_cents: int | None = None
    cleaning_fee_cents: int = 0
    min_notice_hours: int
    media: list[VenueMediaOut] = Field(default_factory=list)
    categories: list[CategoryOut] = Field(default_factory=list)
    amenities: list[AmenityOut] = Field(default_factory=list)
    event_types: list[EventTypeOut] = Field(default_factory=list)
    provider: ProviderPublicOut | None = None
    is_favorite: bool = False
    views_count: int = 0
    quality_flags: dict = Field(default_factory=dict)


class VenueSearchParams(BaseModel):
    """Todos los filtros de `GET /venues`. Cada uno tiene índice o se resuelve por EXISTS."""

    q: str | None = Field(default=None, max_length=120)
    category: list[str] = Field(default_factory=list)
    city: str | None = None
    state: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float = Field(default=25, gt=0, le=120)
    capacity_min: int | None = Field(default=None, ge=1)
    capacity_max: int | None = Field(default=None, ge=1)
    price_min: int | None = Field(default=None, ge=0, description="Centavos")
    price_max: int | None = Field(default=None, ge=0, description="Centavos")
    date_from: date | None = None
    date_to: date | None = None
    time_slot: SlotKind | None = None
    amenities: list[str] = Field(default_factory=list, max_length=20)
    event_types: list[str] = Field(default_factory=list, max_length=10)
    rating_min: float | None = Field(default=None, ge=0, le=5)
    instant_book: bool | None = None
    verified_only: bool = False
    sort: VenueSort = VenueSort.RECOMMENDED
    page: int = Field(default=1, ge=1, le=200)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = None

    @model_validator(mode="after")
    def _geo_pair(self) -> VenueSearchParams:
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat y lng deben enviarse juntos")
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to debe ser posterior a date_from")
        return self


class AvailabilityDayOut(BaseModel):
    date: date
    is_available: bool
    slots: list[SlotOut] = Field(default_factory=list)
    reason_unavailable: str | None = Field(
        default=None, description="booked | blackout | closed | past | min_notice | buffer"
    )


class SlotOut(BaseModel):
    start_at: datetime
    end_at: datetime
    slot_kind: SlotKind
    price_cents: int
    currency: str = "MXN"
    is_available: bool
    is_held_by_me: bool = False


class VenueAvailabilityOut(BaseModel):
    venue_id: uuid.UUID
    timezone: str
    from_date: date
    to_date: date
    days: list[AvailabilityDayOut]


# ── Disponibilidad y precios ───────────────────────────────────────────────────


class AvailabilityRuleIn(BaseModel):
    weekday: int = Field(ge=0, le=6, description="0=lunes … 6=domingo")
    slot_kind: SlotKind = SlotKind.FULL_DAY
    open_time: time
    close_time: time
    crosses_midnight: bool = False
    slot_minutes: int = Field(default=1440, ge=30, le=1440)
    min_booking_minutes: int = Field(default=240, ge=30, le=4320)
    max_booking_minutes: int | None = Field(default=None, ge=30, le=4320)
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool = True
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _midnight_consistency(self) -> AvailabilityRuleIn:
        if self.close_time <= self.open_time and not self.crosses_midnight:
            # 22:00 → 02:00 debe declararse como cruce de medianoche (edge case A4)
            raise ValueError(
                "Si close_time es menor que open_time, marca crosses_midnight=true"
            )
        return self


class AvailabilityRuleOut(ORMModel):
    id: uuid.UUID
    weekday: int
    slot_kind: SlotKind
    open_time: time
    close_time: time
    crosses_midnight: bool
    slot_minutes: int
    min_booking_minutes: int
    max_booking_minutes: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool


class AvailabilityRulesPutRequest(BaseModel):
    rules: list[AvailabilityRuleIn] = Field(max_length=7 * 6)
    replace_all: bool = Field(
        default=True, description="False = upsert por (weekday, slot_kind)"
    )


class BlackoutIn(BaseModel):
    start_at: datetime
    end_at: datetime
    is_partial: bool = False
    reason: str | None = Field(default=None, max_length=280)
    internal_note: str | None = Field(default=None, max_length=280)
    force: bool = Field(
        default=False,
        description="Si choca con reservas confirmadas, se rechaza salvo force=true (edge case E27)",
    )


class BlackoutOut(ORMModel):
    id: uuid.UUID
    start_at: datetime
    end_at: datetime
    is_partial: bool
    reason: str | None = None
    overlaps_confirmed: bool


class PricingRuleIn(BaseModel):
    kind: PricingKind = PricingKind.BASE
    name: str | None = Field(default=None, max_length=120)
    amount_cents: int = Field(ge=0)
    per_hour: bool = False
    priority: int = Field(default=10, ge=0, le=100)
    min_guests: int | None = Field(default=None, ge=1)
    max_guests: int | None = Field(default=None, ge=1)
    extra_guest_cents: int | None = Field(default=None, ge=0)
    valid_from: date | None = None
    valid_to: date | None = None
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    specific_dates: list[date] = Field(default_factory=list, max_length=400)
    slot_kind: SlotKind | None = None
    min_days_before: int | None = Field(default=None, ge=0)
    max_days_before: int | None = Field(default=None, ge=0)
    discount_percent: float | None = Field(default=None, ge=0, le=90)
    is_active: bool = True
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _guests_range(self) -> PricingRuleIn:
        if self.min_guests and self.max_guests and self.max_guests < self.min_guests:
            raise ValueError("max_guests debe ser mayor o igual a min_guests")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays debe usar 0..6")
        return self


class PricingRuleOut(ORMModel):
    id: uuid.UUID
    kind: PricingKind
    name: str | None = None
    amount_cents: int
    per_hour: bool
    priority: int
    min_guests: int | None = None
    max_guests: int | None = None
    extra_guest_cents: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    weekdays: list[int] = Field(default_factory=list)
    specific_dates: list[date] = Field(default_factory=list)
    slot_kind: SlotKind | None = None
    discount_percent: float | None = None
    is_active: bool


class PricingRulesPutRequest(BaseModel):
    rules: list[PricingRuleIn] = Field(max_length=60)
    replace_all: bool = True


class QuoteRequest(BaseModel):
    start_at: datetime
    end_at: datetime
    guests: int = Field(ge=1, le=10000)
    extras: list[str] = Field(default_factory=list, max_length=20, description="Slugs de amenidades extra")
    coupon_code: str | None = Field(default=None, max_length=40)
    event_type_slug: str | None = None

    @model_validator(mode="after")
    def _window(self) -> QuoteRequest:
        if self.end_at <= self.start_at:
            raise ValueError("end_at debe ser posterior a start_at")
        return self


class QuoteLineOut(BaseModel):
    concept: str = Field(examples=["Renta del espacio"])
    detail: str | None = None
    amount_cents: int
    is_refundable: bool = False
    is_discount: bool = False


class QuoteOut(BaseModel):
    venue_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    venue_timezone: str
    duration_hours: float
    guests: int
    lines: list[QuoteLineOut]
    subtotal_cents: int
    extras_cents: int
    cleaning_cents: int
    discount_cents: int
    service_fee_cents: int
    tax_cents: int
    security_deposit_cents: int
    total_cents: int
    deposit_cents: int
    currency: str = "MXN"
    applied_rules: list[str] = Field(description="IDs/nombres de reglas aplicadas (para soporte)")
    coupon: CouponAppliedOut | None = None
    hold_ttl_seconds: int = 900
    pricing_engine_version: str = "1.0.0"
    warnings: list[str] = Field(default_factory=list)
    snapshot: dict = Field(
        description="Snapshot a persistir en el hold/booking; no se recalcula después"
    )
    expires_at: datetime = Field(description="La cotización se considera válida hasta este momento")


class CouponAppliedOut(BaseModel):
    code: str
    kind: Literal["PERCENT", "FIXED"]
    discount_cents: int


class CouponValidateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    venue_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    guests: int = Field(ge=1)
    subtotal_cents: int | None = Field(default=None, ge=0)


class CouponValidateResponse(BaseModel):
    valid: bool
    code: str
    discount_cents: int = 0
    message: str
    reason: str | None = Field(default=None, examples=["COUPON_EXPIRED", "COUPON_NOT_APPLICABLE"])


# ── Catálogo ───────────────────────────────────────────────────────────────────


class CategoryOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    icon_key: str | None = None
    sort_order: int = 0
    venues_count: int | None = None


class AmenityOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    category: str = "GENERAL"
    icon_key: str | None = None
    is_included_by_default: bool = True
    default_price_cents: int | None = None


class EventTypeOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    requires_curfew: bool = False
    default_curfew_time: str | None = None


class CategoryUpsert(BaseModel):
    slug: Slug
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    icon_key: str | None = None
    sort_order: int = 0
    is_active: bool = True


class AmenityUpsert(BaseModel):
    slug: Slug
    name: str = Field(min_length=2, max_length=80)
    category: str = Field(default="GENERAL", max_length=40)
    icon_key: str | None = None
    is_included_by_default: bool = True
    default_price_cents: int | None = Field(default=None, ge=0)
    is_active: bool = True


class EventTypeUpsert(BaseModel):
    slug: Slug
    name: str = Field(min_length=2, max_length=80)
    requires_curfew: bool = False
    default_curfew_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    is_active: bool = True


class CouponUpsert(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Z0-9_-]+$")
    kind: Literal["PERCENT", "FIXED"]
    value: int = Field(gt=0)
    description: str | None = None
    max_discount_cents: int | None = Field(default=None, ge=0)
    min_subtotal_cents: int | None = Field(default=None, ge=0)
    max_redemptions: int | None = Field(default=None, ge=1)
    per_user_limit: int = Field(default=1, ge=1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    venue_ids: list[uuid.UUID] = Field(default_factory=list)
    excluded_venue_ids: list[uuid.UUID] = Field(default_factory=list)
    category_slugs: list[str] = Field(default_factory=list)
    first_booking_only: bool = False
    stackable: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def _percent_range(self) -> CouponUpsert:
        if self.kind == "PERCENT" and not 1 <= self.value <= 100:
            raise ValueError("Un cupón de porcentaje debe estar entre 1 y 100")
        return self


class CouponOut(ORMModel):
    id: uuid.UUID
    code: str
    kind: str
    value: int
    description: str | None = None
    redemptions_count: int = 0
    max_redemptions: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool
    created_at: datetime


class SearchSuggestionOut(BaseModel):
    kind: Literal["venue", "city", "category", "event_type", "query"]
    text: str
    subtitle: str | None = None
    venue_id: uuid.UUID | None = None
    category_slug: str | None = None
    lat: float | None = None
    lng: float | None = None
    score: float = 0


class HomeFeaturedOut(BaseModel):
    section: Literal["sponsored", "nearby", "trending", "new", "last_minute"]
    title: str
    subtitle: str | None = None
    venues: list[VenueSummaryOut]
    disclosure: str | None = Field(
        default=None,
        description="Texto legal obligatorio en secciones pagadas",
        examples=["Patrocinado · estos espacios pagaron por aparecer aquí"],
    )


# Referencias hacia adelante (se resuelven cuando todo el módulo ya está cargado).
for _forward_ref_model in (AvailabilityDayOut, VenueDetailOut, QuoteOut):
    _forward_ref_model.model_rebuild()

