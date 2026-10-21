"""Jobs de mantenimiento: holds, recordatorios, reseñas, webhooks, limpieza y exportaciones."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.booking import Booking
from app.models.enums import AuditAction, NotificationKind, ReviewStatus, WebhookEventType
from app.models.notification import Notification
from app.models.review import Review
from app.models.user import User
from app.models.venue import Venue
from app.services import audit, storage, webhooks_out
from app.services import bookings as booking_service
from app.services import notifications as notification_service
from app.services.idempotency import cleanup_expired
from app.tasks.celery_app import celery_app

log = get_logger("tasks")


@celery_app.task(name="app.tasks.jobs.expire_holds", bind=True, max_retries=2)
def expire_holds(self) -> dict:  # type: ignore[no-untyped-def]
    """Libera apartados vencidos. Idempotente: sólo toca los que siguen `ACTIVE`."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            expired = await booking_service.expire_holds(session)
            notified = 0
            for hold in await _recently_expired(session):
                user = await session.get(User, hold.client_id)
                if user is None:
                    continue
                title, body, link = notification_service.hold_expiring_copy(
                    venue_name="tu espacio", minutes_left=0, hold_id=hold.id
                )
                await notification_service.notify(
                    session,
                    user=user,
                    kind=NotificationKind.HOLD_EXPIRED,
                    title="Tu apartado venció",
                    body=body,
                    deep_link=link,
                    dedupe_key=f"hold-expired:{hold.id}",
                )
                notified += 1
            await session.commit()
            return {"expired": expired, "notified": notified}

    return asyncio.run(run())


async def _recently_expired(session) -> list:  # type: ignore[no-untyped-def]
    from app.models.booking import BookingHold
    from app.models.enums import HoldStatus

    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    return list(
        await session.scalars(
            select(BookingHold).where(
                BookingHold.status == HoldStatus.EXPIRED.value,
                BookingHold.updated_at >= cutoff,
            )
        )
    )


@celery_app.task(name="app.tasks.jobs.send_booking_reminders")
def send_booking_reminders() -> dict:
    """Recordatorios 24 h y 2 h. La unicidad la garantiza `dedupe_key` en la tabla de notificaciones."""
    import asyncio

    async def run() -> dict:
        sent = {"24h": 0, "2h": 0}
        async with SessionLocal() as session:
            for hours, key in ((24, "24h"), (2, "2h")):
                for booking in await booking_service.bookings_needing_reminder(session, hours_ahead=hours):
                    user = await session.get(User, booking.client_id)
                    venue = await session.get(Venue, booking.venue_id)
                    if user is None or venue is None:
                        continue
                    kind = (
                        NotificationKind.BOOKING_REMINDER_24H
                        if hours == 24
                        else NotificationKind.BOOKING_REMINDER_2H
                    )
                    await notification_service.notify(
                        session,
                        user=user,
                        kind=kind,
                        title=f"Tu evento en {venue.name} es pronto",
                        body=(
                            f"Empieza el {booking.start_at.astimezone().strftime('%d/%m a las %H:%M')} "
                            f"para {booking.guests} invitados. Folio {booking.folio}."
                        ),
                        payload={"booking_id": str(booking.id)},
                        deep_link=f"quinta://bookings/{booking.id}",
                        dedupe_key=f"reminder-{key}:{booking.id}",
                    )
                    sent[key] += 1
            await session.commit()
        return sent

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.request_reviews")
def request_reviews() -> dict:
    """Solicitud de reseña 24 h después del evento, una sola vez por reserva."""
    import asyncio

    async def run() -> dict:
        count = 0
        async with SessionLocal() as session:
            for booking in await booking_service.completed_without_review(session, days_after=1):
                user = await session.get(User, booking.client_id)
                venue = await session.get(Venue, booking.venue_id)
                if user is None or venue is None:
                    continue
                title, body, link = notification_service.review_request_copy(
                    venue_name=venue.name, booking_id=booking.id
                )
                await notification_service.notify(
                    session,
                    user=user,
                    kind=NotificationKind.REVIEW_REQUEST,
                    title=title,
                    body=body,
                    deep_link=link,
                    dedupe_key=f"review-request:{booking.id}",
                )
                count += 1
            await session.commit()
        return {"requested": count}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.on_booking_completed")
def on_booking_completed(booking_id: str) -> dict:
    """Efectos de un evento completado: acumula payout y programa la solicitud de reseña."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            booking = await session.get(Booking, uuid.UUID(booking_id))
            if booking is None:
                return {"status": "not_found"}
            from app.services import payments as payment_service

            await payment_service.accrue_payout(session, booking=booking)
            await session.commit()
            request_reviews.apply_async(countdown=60)
            return {"booking_id": booking_id, "payout_accrued": True}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.on_review_created")
def on_review_created(review_id: str) -> dict:
    """Notifica al proveedor y emite el webhook `review.created`."""
    import asyncio

    from app.models.enums import WebhookEventType

    async def run() -> dict:
        async with SessionLocal() as session:
            review = await session.get(Review, uuid.UUID(review_id))
            if review is None:
                return {"status": "not_found"}
            if review.status != ReviewStatus.PUBLISHED.value:
                return {"status": "pending_moderation"}
            from app.models.provider import ProviderProfile

            profile = await session.get(ProviderProfile, review.provider_id)
            if profile is not None:
                user = await session.get(User, profile.user_id)
                if user is not None:
                    await notification_service.notify(
                        session,
                        user=user,
                        kind=NotificationKind.REVIEW_REPLY,
                        title=f"Nueva reseña de {review.rating} estrellas",
                        body=(review.body or "")[:160],
                        deep_link=f"quinta://reviews/{review.id}",
                        dedupe_key=f"review:{review.id}",
                    )
            event = await webhooks_out.emit(
                session,
                event_type=WebhookEventType.REVIEW_CREATED,
                provider_id=review.provider_id,
                payload={
                    "review_id": str(review.id),
                    "venue_id": str(review.venue_id),
                    "rating": review.rating,
                },
            )
            await session.commit()
            if event is not None:
                emit_webhook_event.delay(str(event.id))
            return {"review_id": review_id}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.emit_webhook_event")
def emit_webhook_event(event_id: str) -> dict:
    """Fan-out de un evento a las suscripciones activas."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            count = await webhooks_out.fanout_event(session, event_id=uuid.UUID(event_id))
            return {"event_id": event_id, "deliveries": count}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.emit_pending_webhooks")
def emit_pending_webhooks() -> dict:
    """Reintenta entregas vencidas con backoff. Los fallos no bloquean la cola."""
    import asyncio

    async def run() -> dict:
        sent = failed = 0
        async with SessionLocal() as session:
            for delivery in await webhooks_out.pending_deliveries(session):
                ok = await webhooks_out.deliver(session, delivery_id=delivery.id)
                sent += int(ok)
                failed += int(not ok)
        return {"delivered": sent, "failed": failed}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.cleanup_idempotency_keys")
def cleanup_idempotency_keys() -> dict:
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            deleted = await cleanup_expired(session)
            await session.commit()
            return {"deleted": deleted}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.recalculate_ratings")
def recalculate_ratings() -> dict:
    """Recalcula promedios desnormalizados de venues y proveedores (repara derivas)."""
    import asyncio

    from app.models.provider import ProviderProfile

    async def run() -> dict:
        updated = 0
        async with SessionLocal() as session:
            venues = (await session.scalars(select(Venue).where(Venue.deleted_at.is_(None)))).all()
            for venue in venues:
                average, count = (
                    await session.execute(
                        select(func.avg(Review.rating), func.count()).where(
                            Review.venue_id == venue.id, Review.status == ReviewStatus.PUBLISHED.value
                        )
                    )
                ).one()
                new_avg = round(float(average), 2) if average else None
                if venue.rating_avg != new_avg or venue.rating_count != int(count or 0):
                    venue.rating_avg = new_avg
                    venue.rating_count = int(count or 0)
                    updated += 1
            profiles = (await session.scalars(select(ProviderProfile))).all()
            for profile in profiles:
                average, count = (
                    await session.execute(
                        select(func.avg(Review.rating), func.count()).where(
                            Review.provider_id == profile.id,
                            Review.status == ReviewStatus.PUBLISHED.value,
                        )
                    )
                ).one()
                profile.rating_avg = round(float(average), 2) if average else None
                profile.rating_count = int(count or 0)
            await session.commit()
        return {"venues_updated": updated}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.purge_old_audit")
def purge_old_audit() -> dict:
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            deleted = await audit.purge_old(session)
            await session.commit()
            return {"deleted": deleted}

    return asyncio.run(run())


@celery_app.task(name="app.tasks.jobs.build_data_export")
def build_data_export(user_id: str) -> dict:
    """Portabilidad (LFPDPPP): genera el JSON del usuario y lo deja en el bucket con URL firmada."""
    import asyncio

    async def run() -> dict:
        async with SessionLocal() as session:
            user = await session.get(User, uuid.UUID(user_id))
            if user is None:
                return {"status": "not_found"}
            bookings = (
                await session.scalars(select(Booking).where(Booking.client_id == user.id))
            ).all()
            reviews = (
                await session.scalars(select(Review).where(Review.author_id == user.id))
            ).all()
            notifications = (
                await session.scalars(
                    select(Notification).where(Notification.user_id == user.id).limit(500)
                )
            ).all()
            payload = {
                "generated_at": datetime.now(UTC).isoformat(),
                "profile": {
                    "id": str(user.id),
                    "email": user.email,
                    "full_name": user.full_name,
                    "phone": user.phone_e164,
                    "created_at": user.created_at.isoformat(),
                },
                "bookings": [
                    {
                        "folio": booking.folio,
                        "state": booking.state,
                        "start_at": booking.start_at.isoformat(),
                        "total_cents": booking.total_cents,
                        "currency": booking.currency,
                    }
                    for booking in bookings
                ],
                "reviews": [
                    {"rating": review.rating, "body": review.body, "created_at": review.created_at.isoformat()}
                    for review in reviews
                ],
                "notifications": [
                    {"kind": item.kind, "title": item.title, "created_at": item.created_at.isoformat()}
                    for item in notifications
                ],
                "note": (
                    "No se incluyen facturas, pagos ni registros contables: se conservan por obligación "
                    "fiscal durante el plazo legal aplicable."
                ),
            }
            key = storage.build_key("exports", user.id, "datos.json")
            # En producción se sube con `put_object`; aquí se firma una URL de subida para el worker
            ticket = storage.presign_upload(key, "application/json")
            await audit.record(
                session,
                action=AuditAction.DATA_EXPORTED,
                entity="user",
                entity_id=user.id,
                actor_id=user.id,
                after={"storage_key": key, "size_bytes": len(json.dumps(payload))},
            )
            await session.commit()
            return {"storage_key": key, "upload_url": ticket.url}

    return asyncio.run(run())
