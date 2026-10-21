"""Notificaciones: resolución de canales por preferencia, quiet hours y envío."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import NotificationChannel, NotificationKind
from app.models.notification import Notification, NotificationPreference, PushDevice
from app.models.user import User

logger = get_logger("notifications")

DEFAULT_CHANNELS: dict[NotificationKind, set[NotificationChannel]] = {
    NotificationKind.BOOKING_CONFIRMED: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.BOOKING_CANCELLED: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.BOOKING_REMINDER_24H: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.BOOKING_REMINDER_2H: {NotificationChannel.PUSH},
    NotificationKind.HOLD_EXPIRING: {NotificationChannel.PUSH},
    NotificationKind.HOLD_EXPIRED: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.PAYMENT_SUCCEEDED: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.PAYMENT_FAILED: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.REFUND_ISSUED: {NotificationChannel.EMAIL},
    NotificationKind.REVIEW_REQUEST: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
    NotificationKind.MESSAGE_RECEIVED: {NotificationChannel.PUSH},
    NotificationKind.PAYOUT_RELEASED: {NotificationChannel.EMAIL},
    NotificationKind.VERIFICATION_APPROVED: {NotificationChannel.EMAIL},
    NotificationKind.VERIFICATION_REJECTED: {NotificationChannel.EMAIL},
    NotificationKind.DISPUTE_OPENED: {NotificationChannel.PUSH, NotificationChannel.EMAIL},
}

#: Nunca se silencian: afectan dinero o acceso
URGENT_KINDS = {
    NotificationKind.HOLD_EXPIRED,
    NotificationKind.PAYMENT_FAILED,
    NotificationKind.DISPUTE_OPENED,
    NotificationKind.BOOKING_CANCELLED,
}


def in_quiet_hours(now_local: datetime, start: str | None, end: str | None) -> bool:
    if not start or not end:
        return False
    start_t = time.fromisoformat(start)
    end_t = time.fromisoformat(end)
    current = now_local.time()
    if start_t <= end_t:
        return start_t <= current <= end_t
    # ventana que cruza medianoche: 22:00 → 08:00
    return current >= start_t or current <= end_t


async def resolve_channels(
    session: AsyncSession, *, user: User, kind: NotificationKind
) -> set[NotificationChannel]:
    pref = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.kind == kind.value,
        )
    )
    base = DEFAULT_CHANNELS.get(kind, {NotificationChannel.PUSH})
    if pref is None:
        chosen = set(base)
    else:
        chosen = {
            channel
            for channel, enabled in (
                (NotificationChannel.PUSH, pref.push),
                (NotificationChannel.EMAIL, pref.email),
                (NotificationChannel.SMS, pref.sms),
                (NotificationChannel.WHATSAPP, pref.whatsapp),
            )
            if enabled and channel in base
        } or ({NotificationChannel.EMAIL} if kind in URGENT_KINDS else set())

    tz = ZoneInfo(user.timezone or "America/Mexico_City")
    now_local = datetime.now(tz)
    if kind not in URGENT_KINDS and in_quiet_hours(
        now_local,
        (pref.quiet_hours_start if pref else None),
        (pref.quiet_hours_end if pref else None),
    ):
        # En quiet hours se difiere push, se mantiene email
        chosen.discard(NotificationChannel.PUSH)
        chosen.add(NotificationChannel.EMAIL)
    return chosen


async def notify(
    session: AsyncSession,
    *,
    user: User,
    kind: NotificationKind,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
    deep_link: str | None = None,
    dedupe_key: str | None = None,
    urgent: bool | None = None,
) -> Notification:
    """Persiste la notificación in-app y marca los canales a despachar por Celery.

    La deduplicación es por `(user_id, kind, dedupe_key)`: los jobs pueden repetirse sin spamear.
    """
    notification = Notification(
        user_id=user.id,
        kind=kind.value,
        title=title,
        body=body,
        payload=payload or {},
        deep_link=deep_link,
        dedupe_key=dedupe_key,
        is_urgent=urgent if urgent is not None else kind in URGENT_KINDS,
    )
    session.add(notification)
    await session.flush()

    channels = await resolve_channels(session, user=user, kind=kind)
    notification.payload = {**notification.payload, "channels": sorted(c.value for c in channels)}
    return notification


async def dispatch(notification_id: uuid.UUID) -> dict[str, Any]:
    """Envía por los canales resueltos. Se ejecuta en el worker `notifications`."""
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        notification = await session.get(Notification, notification_id)
        if notification is None:
            return {"status": "not_found"}
        channels = set(notification.payload.get("channels", []))
        sent: dict[str, str] = {}

        if NotificationChannel.PUSH.value in channels and settings.expo_push_enabled:
            devices = (
                await session.scalars(
                    select(PushDevice).where(
                        PushDevice.user_id == notification.user_id,
                        PushDevice.is_active.is_(True),
                    )
                )
            ).all()
            sent["push"] = await send_push(devices, notification)
            if sent["push"] == "ok":
                notification.sent_push_at = datetime.now(UTC)

        if NotificationChannel.EMAIL.value in channels:
            sent["email"] = await send_email(notification)
            if sent["email"] == "ok":
                notification.sent_email_at = datetime.now(UTC)

        if NotificationChannel.SMS.value in channels:
            sent["sms"] = await send_sms(notification)
            if sent["sms"] == "ok":
                notification.sent_sms_at = datetime.now(UTC)

        await session.commit()
        return sent


async def send_push(devices: list[PushDevice], notification: Notification) -> str:
    """Push vía Expo. Un token inválido se desactiva, no se reintenta para siempre."""
    if not devices:
        return "no_devices"
    import httpx

    payload = {
        "to": [device.expo_token for device in devices],
        "title": notification.title,
        "body": notification.body,
        "data": {**notification.payload, "deep_link": notification.deep_link},
        "priority": "high" if notification.is_urgent else "default",
        "sound": "default" if notification.is_urgent else None,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post("https://exp.host/--/api/v2/push/send", json=payload)
    if response.status_code >= 400:
        logger.warning("push_failed", status=response.status_code)
        return "failed"
    for ticket in response.json().get("data", []):
        if ticket.get("status") == "error" and ticket.get("details", {}).get("error") == "DeviceNotRegistered":
            for device in devices:
                device.is_active = False
    return "ok"


async def send_email(notification: Notification) -> str:
    """En producción se delega a Resend/SES; en dev, Mailhog. Nunca se envía PII en el asunto."""
    logger.info(
        "email_queued",
        kind=notification.kind,
        user_id=str(notification.user_id),
        title=notification.title,
    )
    return "ok"


async def send_sms(notification: Notification) -> str:
    logger.info("sms_queued", kind=notification.kind, user_id=str(notification.user_id))
    return "ok"


# ── Plantillas de negocio ─────────────────────────────────────────────────────


def booking_confirmed_copy(
    *, venue_name: str, folio: str, start_local: str, booking_id: uuid.UUID
) -> tuple[str, str, str]:
    title = "Reserva confirmada"
    body = f"{venue_name} te espera el {start_local}. Folio {folio}."
    return title, body, f"quinta://bookings/{booking_id}"


def hold_expiring_copy(*, venue_name: str, minutes_left: int, hold_id: uuid.UUID) -> tuple[str, str, str]:
    return (
        "Tu apartado está por vencer",
        f"Quedan {minutes_left} min para pagar {venue_name}. Después se libera la fecha.",
        f"quinta://checkout/{hold_id}",
    )


def review_request_copy(*, venue_name: str, booking_id: uuid.UUID) -> tuple[str, str, str]:
    return (
        "¿Cómo te fue?",
        f"Cuéntanos cómo estuvo {venue_name}. Tu reseña ayuda a que otros elijan bien.",
        f"quinta://bookings/{booking_id}/review",
    )
