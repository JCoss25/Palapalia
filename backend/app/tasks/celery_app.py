"""Aplicación Celery: colas por criticidad, beat y configuración de reintentos."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "quinta",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.jobs",
        "app.tasks.payments",
        "app.tasks.notifications",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,               # se confirma al terminar: si el worker muere, se reintenta
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,      # reparto justo entre workers
    task_default_queue="default",
    task_routes={
        "app.tasks.payments.*": {"queue": "payments"},
        "app.tasks.notifications.*": {"queue": "notifications"},
        "app.tasks.jobs.emit_pending_webhooks": {"queue": "default"},
        "app.tasks.jobs.expire_holds": {"queue": "default"},
    },
    task_annotations={
        "*": {"max_retries": 3, "rate_limit": None},
        "app.tasks.notifications.send_otp": {"rate_limit": "60/m"},
    },
    result_expires=3600,
    beat_schedule={
        # Cada minuto: libera slots de apartados vencidos (edge case A1/A2)
        "expire-holds": {"task": "app.tasks.jobs.expire_holds", "schedule": 60.0},
        # Cada 5 min: reenvía recordatorios y solicitudes de reseña
        "booking-reminders": {"task": "app.tasks.jobs.send_booking_reminders", "schedule": 300.0},
        "review-requests": {"task": "app.tasks.jobs.request_reviews", "schedule": 3600.0},
        # Cada minuto: entrega webhooks salientes con backoff
        "webhook-deliveries": {"task": "app.tasks.jobs.emit_pending_webhooks", "schedule": 60.0},
        # Cada 10 min: reconcilia pagos cuyo hold expiró (edge case A2)
        "reconcile-payments": {"task": "app.tasks.payments.reconcile_orphan_payments", "schedule": 600.0},
        # Diario: limpieza y mantenimiento
        "cleanup-idempotency": {"task": "app.tasks.jobs.cleanup_idempotency_keys", "schedule": crontab(hour=4, minute=10)},
        "recalculate-ratings": {"task": "app.tasks.jobs.recalculate_ratings", "schedule": crontab(hour=3, minute=0)},
        "release-security-deposits": {"task": "app.tasks.payments.release_security_deposits", "schedule": crontab(hour=5, minute=0)},
        "purge-audit": {"task": "app.tasks.jobs.purge_old_audit", "schedule": crontab(day_of_month=1, hour=6, minute=0)},
    },
)

#: Colas esperadas por el despliegue (se crean solas al arrancar el worker)
QUEUES = ("default", "payments", "notifications")
