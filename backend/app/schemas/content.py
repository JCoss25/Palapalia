"""Esquemas de reseñas, mensajería, notificaciones, admin y webhooks."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    AuditAction,
    ContactFlagAction,
    MessageKind,
    NotificationChannel,
    NotificationKind,
    ReportReason,
    ReviewStatus,
    Role,
    WebhookEventType,
)
from app.schemas.common import ORMModel


# ── Reseñas ────────────────────────────────────────────────────────────────────


class ReviewCreateRequest(BaseModel):
    booking_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    title: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=4000)
    subratings: dict[str, int] = Field(
        default_factory=dict,
        description="cleanliness, communication, accuracy, value (1..5)",
        examples=[{"cleanliness": 5, "communication": 5, "accuracy": 4, "value": 5}],
    )
    guest_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _subratings_valid(self) -> ReviewCreateRequest:
        allowed = {"cleanliness", "communication", "accuracy", "value", "amenities", "location"}
        for key, value in self.subratings.items():
            if key not in allowed:
                raise ValueError(f"Subcalificación desconocida: {key}")
            if not 1 <= value <= 5:
                raise ValueError(f"{key} debe estar entre 1 y 5")
        return self


class ReviewOut(ORMModel):
    id: uuid.UUID
    venue_id: uuid.UUID
    booking_id: uuid.UUID
    rating: int
    title: str | None = None
    body: str | None = None
    subratings: dict = Field(default_factory=dict)
    status: ReviewStatus
    is_verified_booking: bool
    helpful_count: int = 0
    created_at: datetime
    author_display_name: str | None = None
    author_avatar_url: str | None = None
    event_type_slug: str | None = None
    guest_count: int | None = None
    reply: ReviewReplyOut | None = None


class ReviewReplyOut(ORMModel):
    id: uuid.UUID
    body: str
    created_at: datetime
    edit_count: int = 0
    can_edit: bool = False


class ReviewReplyCreate(BaseModel):
    body: str = Field(min_length=10, max_length=2000)


class ReviewReportCreate(BaseModel):
    reason: ReportReason
    detail: str | None = Field(default=None, max_length=500)


class RatingSummaryOut(BaseModel):
    provider_id: uuid.UUID | None = None
    venue_id: uuid.UUID | None = None
    rating_avg: float | None
    rating_count: int
    distribution: dict[str, int] = Field(
        description="Conteo por estrella", examples=[{"1": 1, "2": 0, "3": 2, "4": 8, "5": 24}]
    )
    subratings_avg: dict[str, float] = Field(default_factory=dict)
    response_rate: float | None = None
    last_review_at: datetime | None = None


class ReviewModerateRequest(BaseModel):
    status: ReviewStatus
    reason: str = Field(min_length=5, max_length=400)


# ── Mensajería ─────────────────────────────────────────────────────────────────


class ThreadCreateRequest(BaseModel):
    venue_id: uuid.UUID
    booking_id: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=4000)


class ThreadOut(ORMModel):
    id: uuid.UUID
    venue_id: uuid.UUID
    venue_name: str | None = None
    booking_id: uuid.UUID | None = None
    subject: str | None = None
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    unread_count: int = 0
    is_locked: bool = False
    counterpart_name: str | None = None
    counterpart_avatar_url: str | None = None


class MessageCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    kind: MessageKind = MessageKind.TEXT
    attachments: list[dict] = Field(default_factory=list, max_length=5)
    #: El cliente puede enviar el mensaje aunque la moderación lo marque; sólo se advierte (C19)
    accept_warning: bool = False


class MessageOut(ORMModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    sender_id: uuid.UUID | None = None
    kind: MessageKind
    body: str
    attachments: list[dict] = Field(default_factory=list)
    read_at: datetime | None = None
    created_at: datetime
    moderation_status: str = "APPROVED"
    #: Advertencia de contacto fuera de plataforma: la UI la muestra sin bloquear el envío
    contact_warning: str | None = None
    bypass_score: float = 0
    is_mine: bool = False


class TypingEvent(BaseModel):
    type: Literal["typing"] = "typing"
    thread_id: uuid.UUID
    user_id: uuid.UUID
    is_typing: bool = True


class ContactFlagOut(ORMModel):
    id: uuid.UUID
    message_id: uuid.UUID
    thread_id: uuid.UUID
    entity_type: str
    score: float
    action: ContactFlagAction
    reviewed_at: datetime | None = None
    review_note: str | None = None
    created_at: datetime


class ContactFlagReviewRequest(BaseModel):
    action: ContactFlagAction
    note: str = Field(min_length=5, max_length=400)
    warn_user: bool = True


# ── Notificaciones ─────────────────────────────────────────────────────────────


class NotificationOut(ORMModel):
    id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    payload: dict = Field(default_factory=dict)
    deep_link: str | None = None
    image_url: str | None = None
    read_at: datetime | None = None
    is_urgent: bool = False
    created_at: datetime


class NotificationPreferenceOut(ORMModel):
    kind: NotificationKind
    push: bool = True
    email: bool = True
    sms: bool = False
    whatsapp: bool = False
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


class NotificationPreferencesPut(BaseModel):
    preferences: list[NotificationPreferenceOut] = Field(max_length=40)
    global_quiet_hours_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    global_quiet_hours_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class PushDeviceRegister(BaseModel):
    expo_token: str = Field(min_length=10, max_length=255)
    platform: Literal["ios", "android"]
    app_version: str | None = None
    device_model: str | None = None


# ── Admin, auditoría, webhooks ─────────────────────────────────────────────────


class AdminMetricsOut(BaseModel):
    gmv_cents: int
    gmv_currency: str = "MXN"
    net_revenue_cents: int
    take_rate: float
    bookings_confirmed: int
    bookings_completed: int
    hold_conversion_rate: float
    hold_conflicts_total: int
    cancellation_rate: float
    dispute_rate: float
    active_providers: int
    pending_verifications: int
    pending_venue_reviews: int
    search_p95_ms: float | None = None
    payment_webhook_lag_seconds: float | None = None
    period_from: date
    period_to: date


class AuditLogOut(ORMModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None = None
    actor_role: str | None = None
    action: AuditAction | str
    entity: str
    entity_id: str | None = None
    before: dict | None = None
    after: dict | None = None
    reason: str | None = None
    ip: str | None = None
    request_id: str | None = None
    created_at: datetime


class ImpersonateRequest(BaseModel):
    user_id: uuid.UUID
    reason: str = Field(min_length=10, max_length=300)
    ticket_ref: str | None = Field(default=None, max_length=60)
    ttl_minutes: int = Field(default=30, ge=5, le=120)


class ImpersonateResponse(BaseModel):
    access_token: str
    expires_in: int
    target_user_id: uuid.UUID
    session_id: uuid.UUID
    warning: str = "Toda acción realizada en esta sesión queda auditada y es visible para el usuario."


class WebhookSubscriptionCreate(BaseModel):
    url: str = Field(min_length=12, max_length=1024, pattern=r"^https://")
    events: list[WebhookEventType] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=200)


class WebhookSubscriptionOut(ORMModel):
    id: uuid.UUID
    url: str
    events: list[str]
    description: str | None = None
    is_active: bool
    failure_count: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    created_at: datetime


class WebhookSubscriptionCreated(WebhookSubscriptionOut):
    secret: str = Field(description="Se muestra una sola vez. Guárdalo para verificar firmas.")


class WebhookEventOut(ORMModel):
    id: uuid.UUID
    event_type: str
    payload: dict
    dispatched_at: datetime | None = None
    delivery_count: int = 0
    replay_count: int = 0
    last_error: str | None = None
    created_at: datetime


class WebhookReplayRequest(BaseModel):
    subscription_ids: list[uuid.UUID] | None = Field(
        default=None, description="Vacío = reintentar todas las suscripciones activas"
    )
    reason: str = Field(min_length=5, max_length=300)


class WebhookDeliveryOut(ORMModel):
    id: uuid.UUID
    event_id: uuid.UUID
    subscription_id: uuid.UUID
    attempt: int
    status_code: int | None = None
    error: str | None = None
    duration_ms: int | None = None
    delivered_at: datetime | None = None
    next_retry_at: datetime | None = None
    is_replay: bool = False


class AdminVenueSuspendRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=400)
    notify_provider: bool = True
    cancel_future_bookings: bool = False
    refund_cancelled: bool = True


class AdminVenueMergeRequest(BaseModel):
    source_venue_id: uuid.UUID
    target_venue_id: uuid.UUID
    reason: str = Field(min_length=10, max_length=400)
    move_bookings: bool = True


class RoleGrantRequest(BaseModel):
    role: Role
    reason: str = Field(min_length=10, max_length=300)


for _forward_ref_model in (ReviewOut,):
    _forward_ref_model.model_rebuild()
