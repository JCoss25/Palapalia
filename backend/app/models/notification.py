"""Notificaciones, preferencias y webhooks salientes."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import GUID, SoftDeleteMixin, TimestampMixin, TZDateTime, uuid_pk
from app.models.enums import NotificationChannel, NotificationKind, WebhookEventType


class Notification(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", "read_at"),
        #: Dedupe: un recordatorio 24h por booking, aunque el job corra dos veces
        UniqueConstraint("user_id", "kind", "dedupe_key", name="uq_notification_dedupe"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[NotificationKind] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(600), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    #: Deep link de la app: "quinta://bookings/{id}"
    deep_link: Mapped[str | None] = mapped_column(String(300), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    sent_push_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    sent_email_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    sent_sms_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    send_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Agrupador para "silenciar en la noche" (no despertar a nadie a las 3 a.m.)
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class NotificationPreference(Base, TimestampMixin):
    """Matriz evento × canal. Por defecto: push + email; SMS sólo para lo crítico."""

    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_notification_pref_user_kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[NotificationKind] = mapped_column(String(32), nullable=False)
    push: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "22:00"
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "08:00"


class PushDevice(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "push_devices"
    __table_args__ = (UniqueConstraint("user_id", "expo_token", name="uq_push_device_token"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expo_token: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(12), nullable=False)  # ios | android
    app_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WebhookSubscription(Base, TimestampMixin, SoftDeleteMixin):
    """Suscripción de un proveedor/partner a eventos de la plataforma."""

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider_id", "url", name="uq_webhook_sub_url"),
        Index("ix_webhook_sub_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    #: Se muestra una sola vez al crear; en BD se guarda cifrado en reposo
    secret: Mapped[str] = mapped_column(String(120), nullable=False)
    events: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class WebhookEvent(Base, TimestampMixin):
    """Evento interno persistido **antes** de entregarse: la entrega es best-effort y reintentable."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("event_type", "dedupe_key", name="uq_webhook_event_dedupe"),
        Index("ix_webhook_events_type_created", "event_type", "created_at"),
        Index("ix_webhook_events_pending", "dispatched_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_type: Mapped[WebhookEventType] = mapped_column(String(40), nullable=False)
    #: Clave de deduplicación del productor (p. ej. booking_id + transición)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("provider_profiles.id", ondelete="SET NULL"), nullable=True
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    delivery_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replay_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WebhookDelivery(Base, TimestampMixin):
    """Un intento de entrega. Guarda la respuesta para diagnóstico y replay."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_event", "event_id", "attempt"),
        Index("ix_webhook_deliveries_retry", "next_retry_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("webhook_events.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signature: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class IdempotencyKey(Base, TimestampMixin):
    """Resultado persistido de un POST crítico. Durable: sobrevive caídas del worker (ADR-005)."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "endpoint", "key", name="uq_idempotency_scope"),
        Index("ix_idempotency_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="IN_PROGRESS", nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class AuditLog(Base, TimestampMixin):
    """Bitácora de acciones sensibles. Append-only; no se actualiza ni se borra."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_actor_created", "actor_id", "created_at"),
        Index("ix_audit_entity", "entity", "entity_id", "created_at"),
        Index("ix_audit_action_created", "action", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    impersonated_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(280), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Cadena de integridad: hash del registro anterior + el propio
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ImpersonationSession(Base, TimestampMixin):
    """`POST /admin/impersonate`: sesión acotada, con motivo y auditoría."""

    __tablename__ = "impersonation_sessions"
    __table_args__ = (Index("ix_impersonation_admin", "admin_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    admin_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    ticket_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    token_jti: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Coupon(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coupons_code"),
        Index("ix_coupons_validity", "valid_from", "valid_to"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="PERCENT", nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # porcentaje (1-100) o centavos
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    max_discount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_subtotal_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    redemptions_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    valid_from: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Restricciones: venues incluidos/excluidos, categorías, primer evento del usuario
    venue_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    excluded_venue_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    category_slugs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")
    first_booking_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stackable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CouponRedemption(Base, TimestampMixin):
    """Unicidad (coupon, user, booking): imposible aplicar dos veces el mismo cupón (B13)."""

    __tablename__ = "coupon_redemptions"
    __table_args__ = (
        UniqueConstraint("coupon_id", "user_id", "booking_id", name="uq_coupon_redemption"),
        Index("ix_coupon_redemptions_user", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    coupon_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    hold_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
