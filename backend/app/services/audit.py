"""Auditoría append-only con cadena de integridad (hash encadenado).

Se usa en: transiciones de reserva, cambios de precio/disponibilidad, acciones admin,
impersonación, moderación, reembolsos y borrado de datos.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import AuditAction
from app.models.notification import AuditLog

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "hashed_password",
        "token",
        "refresh_token",
        "access_token",
        "client_secret",
        "code",
        "code_hash",
        "secret",
        "rfc",
        "tax_id",
        "phone_e164",
        "email",
        "card",
        "cvv",
    }
)


def scrub(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Quita PII y secretos del payload de auditoría. La auditoría no es una copia de la BD."""
    if payload is None:
        return None
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in SENSITIVE_KEYS:
            clean[key] = "***"
        elif isinstance(value, dict):
            clean[key] = scrub(value)
        elif isinstance(value, list):
            clean[key] = [scrub(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, (uuid.UUID, datetime)):
            clean[key] = str(value)
        else:
            clean[key] = value
    return clean


def _entry_hash(prev_hash: str | None, payload: dict[str, Any]) -> str:
    raw = json.dumps({"prev": prev_hash, **payload}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def record(
    session: AsyncSession,
    *,
    action: AuditAction | str,
    entity: str,
    entity_id: str | uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Request | None = None,
    impersonated_by_id: uuid.UUID | None = None,
) -> AuditLog:
    """Inserta un registro de auditoría encadenado con el anterior."""
    previous = await session.scalar(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(1))
    prev_hash = previous.entry_hash if previous else None

    entry = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        impersonated_by_id=impersonated_by_id,
        action=action.value if isinstance(action, AuditAction) else action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        before=scrub(before),
        after=scrub(after),
        reason=reason,
        ip=(request.client.host if request and request.client else None),
        user_agent=(request.headers.get("user-agent") if request else None),
        request_id=(getattr(request.state, "request_id", None) if request else None),
        prev_hash=prev_hash,
    )
    entry.entry_hash = _entry_hash(
        prev_hash,
        {
            "action": entry.action,
            "entity": entry.entity,
            "entity_id": entry.entity_id,
            "actor_id": str(entry.actor_id) if entry.actor_id else None,
            "after": entry.after,
        },
    )
    session.add(entry)
    await session.flush()
    return entry


async def verify_chain(session: AsyncSession, limit: int = 1000) -> tuple[bool, int]:
    """Verificación de integridad: devuelve (cadena_ok, registros_revisados)."""
    rows = (
        await session.scalars(
            select(AuditLog).order_by(AuditLog.created_at.asc()).limit(limit)
        )
    ).all()
    prev: str | None = None
    for row in rows:
        expected = _entry_hash(
            prev,
            {
                "action": row.action,
                "entity": row.entity,
                "entity_id": row.entity_id,
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "after": row.after,
            },
        )
        if row.entry_hash != expected:
            return False, len(rows)
        prev = row.entry_hash
    return True, len(rows)


async def purge_old(session: AsyncSession) -> int:
    """Retención: la auditoría se conserva `audit_retention_days` (por defecto 730)."""
    from sqlalchemy import delete

    cutoff = datetime.now(UTC) - timedelta(days=settings.audit_retention_days)
    result = await session.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    return int(result.rowcount or 0)
