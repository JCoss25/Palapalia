"""Webhooks salientes: HMAC, reintentos con backoff, desactivación por fallos y replay."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sign_payload
from app.models.enums import WebhookEventType
from app.models.notification import WebhookDelivery, WebhookEvent, WebhookSubscription

logger = get_logger("webhooks")

#: Backoff: 1 min, 5 min, 30 min, 2 h, 12 h
RETRY_SCHEDULE_MINUTES = (1, 5, 30, 120, 720)
MAX_FAILURES_BEFORE_DISABLE = 25


def _dedupe_key(event_type: WebhookEventType, payload: dict[str, Any]) -> str:
    for key in ("booking_id", "payment_id", "review_id", "payout_id"):
        if payload.get(key):
            return f"{event_type.value}:{payload[key]}"
    return f"{event_type.value}:{hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]}"


async def emit(
    session: AsyncSession,
    *,
    event_type: WebhookEventType,
    provider_id: uuid.UUID | None,
    payload: dict[str, Any],
    booking_id: uuid.UUID | None = None,
) -> WebhookEvent | None:
    """Persiste el evento y encola la entrega. Sin suscripciones activas, no hace nada."""
    if provider_id is None:
        return None
    subscriptions = (
        await session.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.provider_id == provider_id,
                WebhookSubscription.is_active.is_(True),
                WebhookSubscription.deleted_at.is_(None),
            )
        )
    ).all()
    if not subscriptions:
        return None

    event = WebhookEvent(
        event_type=event_type.value,
        dedupe_key=_dedupe_key(event_type, payload),
        provider_id=provider_id,
        booking_id=booking_id,
        payload={
            "id": None,
            "type": event_type.value,
            "created_at": datetime.now(UTC).isoformat(),
            "api_version": "2026-01-15",
            "data": payload,
        },
    )
    session.add(event)
    await session.flush()
    event.payload["id"] = str(event.id)
    return event


async def deliver(session: AsyncSession, *, delivery_id: uuid.UUID) -> bool:
    """Un intento de entrega. Guarda la respuesta y programa el siguiente reintento."""
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return False
    event = await session.get(WebhookEvent, delivery.event_id)
    subscription = await session.get(WebhookSubscription, delivery.subscription_id)
    if event is None or subscription is None or not subscription.is_active:
        return False

    body = json.dumps(event.payload, separators=(",", ":"), default=str).encode()
    signature = sign_payload(subscription.secret, body)
    started = datetime.now(UTC)

    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            response = await client.post(
                subscription.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Quinta-Signature": signature,
                    "X-Quinta-Event": event.event_type,
                    "X-Quinta-Event-Id": str(event.id),
                    "X-Quinta-Delivery-Attempt": str(delivery.attempt),
                    "User-Agent": "Quinta-Webhooks/1.0",
                },
            )
        delivery.status_code = response.status_code
        delivery.response_body = response.text[:2000]
        delivery.duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        success = 200 <= response.status_code < 300
    except Exception as exc:  # noqa: BLE001
        delivery.error = str(exc)[:500]
        success = False

    delivery.signature = signature
    event.delivery_count += 1

    if success:
        delivery.delivered_at = datetime.now(UTC)
        delivery.next_retry_at = None
        subscription.last_success_at = datetime.now(UTC)
        subscription.failure_count = 0
    else:
        subscription.failure_count += 1
        subscription.last_failure_at = datetime.now(UTC)
        if delivery.attempt < settings.webhook_max_attempts:
            delay = RETRY_SCHEDULE_MINUTES[min(delivery.attempt - 1, len(RETRY_SCHEDULE_MINUTES) - 1)]
            delivery.next_retry_at = datetime.now(UTC) + timedelta(minutes=delay)
            session.add(
                WebhookDelivery(
                    event_id=event.id,
                    subscription_id=subscription.id,
                    attempt=delivery.attempt + 1,
                    next_retry_at=delivery.next_retry_at,
                )
            )
        else:
            event.last_error = f"agotados los {settings.webhook_max_attempts} intentos"
        if subscription.failure_count >= MAX_FAILURES_BEFORE_DISABLE:
            subscription.is_active = False
            subscription.disabled_at = datetime.now(UTC)
            subscription.disabled_reason = "demasiados fallos consecutivos"
            logger.warning("webhook_subscription_disabled", subscription_id=str(subscription.id))

    await session.commit()
    return success


async def fanout_event(session: AsyncSession, *, event_id: uuid.UUID, is_replay: bool = False) -> int:
    """Crea una entrega por suscripción activa y devuelve cuántas se programaron."""
    event = await session.get(WebhookEvent, event_id)
    if event is None:
        return 0
    subscriptions = (
        await session.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.provider_id == event.provider_id,
                WebhookSubscription.is_active.is_(True),
            )
        )
    ).all()
    count = 0
    for subscription in subscriptions:
        if event.event_type not in (subscription.events or []):
            continue
        session.add(
            WebhookDelivery(
                event_id=event.id,
                subscription_id=subscription.id,
                attempt=1,
                is_replay=is_replay,
            )
        )
        count += 1
    if count:
        event.dispatched_at = datetime.now(UTC)
        if is_replay:
            event.replay_count += 1
    await session.commit()
    return count


async def pending_deliveries(session: AsyncSession, limit: int = 200) -> list[WebhookDelivery]:
    return list(
        await session.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.delivered_at.is_(None),
                WebhookDelivery.next_retry_at <= datetime.now(UTC),
            )
            .order_by(WebhookDelivery.next_retry_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def verify_incoming(secret: str, body: bytes, header: str) -> bool:
    """Verificación de firma para el consumidor (documentado en la API pública)."""
    from app.core.security import verify_signature

    return verify_signature(secret, body, header)
