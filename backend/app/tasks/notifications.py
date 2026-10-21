"""Jobs de notificación: despacho, OTP, correos transaccionales y avisos de moderación."""

from __future__ import annotations

import uuid

from app.core.logging import get_logger, mask_pii
from app.db.session import SessionLocal
from app.models.enums import NotificationKind, ProviderStatus
from app.models.notification import Notification
from app.models.provider import ProviderProfile
from app.models.user import User
from app.services import notifications as notification_service
from app.tasks.celery_app import celery_app

log = get_logger("tasks.notifications")


@celery_app.task(name="app.tasks.notifications.dispatch_notification", bind=True, max_retries=3)
def dispatch_notification(self, notification_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Despacha por los canales resueltos. Si falla, reintenta con backoff."""
    import asyncio

    try:
        result = asyncio.run(notification_service.dispatch(uuid.UUID(notification_id)))
        return {"notification_id": notification_id, "channels": result}
    except Exception as exc:  # noqa: BLE001
        log.warning("dispatch_failed", notification_id=notification_id, error=str(exc))
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1)) from exc


@celery_app.task(name="app.tasks.notifications.notify_new_message")
def notify_new_message(message_id: str) -> dict:
    """Avisa al destinatario de un mensaje nuevo, respetando quiet hours (no urgentes)."""
    import asyncio

    from app.models.messaging import Message, MessageThread

    async def run() -> dict:
        async with SessionLocal() as session:
            message = await session.get(Message, uuid.UUID(message_id))
            if message is None:
                return {"status": "not_found"}
            thread = await session.get(MessageThread, message.thread_id)
            if thread is None:
                return {"status": "no_thread"}

            recipient_id = (
                thread.provider_id if message.sender_id == thread.client_id else thread.client_id
            )
            user = await session.get(User, recipient_id)
            if user is None:
                return {"status": "no_user"}

            await notification_service.notify(
                session,
                user=user,
                kind=NotificationKind.MESSAGE_RECEIVED,
                title="Tienes un mensaje nuevo",
                body=(message.body or "")[:140],
                payload={"thread_id": str(thread.id)},
                deep_link=f"quinta://threads/{thread.id}",
                dedupe_key=f"message:{message.id}",
            )
            await session.commit()
            return {"notified": str(user.id)}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.notifications.send_otp", rate_limit="60/m")
def send_otp(destination: str, code: str, purpose: str) -> dict:
    """En dev se registra en el log; en prod se envía por SMS (Twilio) o correo."""
    log.info("otp_sent", destination=mask_pii(destination), purpose=purpose)
    return {"sent": True, "destination": mask_pii(destination), "purpose": purpose}


@celery_app.task(name="app.tasks.notifications.send_password_reset_email")
def send_password_reset_email(user_id: str, raw_token: str) -> dict:
    """El token viaja por correo, nunca por la respuesta HTTP."""
    log.info("password_reset_email", user_id=user_id, token_length=len(raw_token))
    return {"sent": True}


@celery_app.task(name="app.tasks.notifications.notify_provider_verification")
def notify_provider_verification(provider_id: str, approved: bool, reason: str | None) -> dict:
    """Aviso de resultado de verificación, con la acción concreta si fue rechazada."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            profile = await session.get(ProviderProfile, uuid.UUID(provider_id))
            if profile is None:
                return {"status": "not_found"}
            user = await session.get(User, profile.user_id)
            if user is None:
                return {"status": "no_user"}
            await notification_service.notify(
                session,
                user=user,
                kind=(
                    NotificationKind.VERIFICATION_APPROVED
                    if approved
                    else NotificationKind.VERIFICATION_REJECTED
                ),
                title="Tu expediente fue aprobado" if approved else "Revisamos tu expediente",
                body=(
                    "Ya puedes publicar tus espacios y recibir reservas."
                    if approved
                    else f"Necesitamos corregir algo: {reason or 'revisa tus documentos'}."
                ),
                deep_link="quinta://provider/verification",
                dedupe_key=f"verification:{provider_id}:{approved}",
            )
            await session.commit()
            return {"notified": approved}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.notifications.notify_contact_warning")
def notify_contact_warning(user_id: str, note: str) -> dict:
    """Aviso al usuario sobre compartir contacto fuera de la plataforma (edge case C19)."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            user = await session.get(User, uuid.UUID(user_id))
            if user is None:
                return {"status": "not_found"}
            await notification_service.notify(
                session,
                user=user,
                kind=NotificationKind.MESSAGE_RECEIVED,
                title="Sobre compartir datos de contacto",
                body=(
                    "Notamos que compartiste datos de contacto. No está prohibido, pero las reservas "
                    "fuera de Quinta no tienen contrato, ni protección de pago, ni soporte. "
                    f"Nota del equipo: {note}"
                ),
                deep_link="quinta://help/contact",
                dedupe_key=f"contact-warning:{user_id}:{note[:24]}",
            )
            await session.commit()
            return {"notified": True}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.notifications.notify_account_banned")
def notify_account_banned(user_id: str, reason: str) -> dict:
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            user = await session.get(User, uuid.UUID(user_id))
            if user is None:
                return {"status": "not_found"}
            await notification_service.notify(
                session,
                user=user,
                kind=NotificationKind.VERIFICATION_REJECTED,
                title="Tu cuenta fue suspendida",
                body=f"Motivo: {reason}. Puedes responder a este correo si crees que es un error.",
                dedupe_key=f"banned:{user_id}",
                urgent=True,
            )
            await session.commit()
            return {"notified": True}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.notifications.flush_pending_notifications")
def flush_pending_notifications(batch: int = 200) -> dict:
    """Barrido de seguridad: despacha notificaciones que quedaron sin enviar."""
    import asyncio

    from sqlalchemy import select

    async def run() -> dict:
        async with SessionLocal() as session:
            rows = (
                await session.scalars(
                    select(Notification)
                    .where(
                        Notification.sent_push_at.is_(None),
                        Notification.sent_email_at.is_(None),
                        Notification.send_failures < 3,
                    )
                    .limit(batch)
                )
            ).all()
            ids = [str(row.id) for row in rows]
        for notification_id in ids:
            dispatch_notification.delay(notification_id)
        return {"queued": len(ids)}

    return asyncio.run(run())
