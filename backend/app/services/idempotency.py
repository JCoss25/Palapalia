"""Idempotencia de POST críticos (ADR-005).

Contrato:
- `Idempotency-Key` obligatorio en bookings/hold, payments, refunds, payouts, webhooks salientes.
- Misma llave + mismo body ⇒ se devuelve la **respuesta guardada** (misma que la primera vez).
- Misma llave + body distinto ⇒ `409 IDEMPOTENCY_KEY_REUSED`.
- Ejecución en curso ⇒ `409 IDEMPOTENCY_IN_PROGRESS` con `Retry-After`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from fastapi import Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.redis import distributed_lock
from app.models.enums import IdempotencyStatus
from app.models.notification import IdempotencyKey

T = TypeVar("T")
IDEMPOTENCY_TTL_DAYS = 30
LOCK_TTL_MS = 20_000
MAX_KEY_LENGTH = 120


def request_fingerprint(payload: Any, path: str) -> str:
    """Hash estable del body + ruta. Orden de claves irrelevante."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{path}|{canonical}".encode()).hexdigest()


async def _lock_key(user_id: uuid.UUID, endpoint: str, key: str) -> str:
    return f"idem:{user_id}:{endpoint}:{key}"


class IdempotencyStore:
    """Lee y escribe resultados idempotentes. La fila se crea antes de ejecutar."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        endpoint: str,
        key: str,
        request_hash: str,
    ) -> tuple[IdempotencyKey, bool]:
        """Devuelve (registro, es_replay). Si es replay, `record.response_body` ya está listo."""
        if len(key) > MAX_KEY_LENGTH:
            raise AppError(
                ErrorCode.IDEMPOTENCY_KEY_REUSED,
                message="La llave de idempotencia es demasiado larga (máx. 120 caracteres).",
            )

        existing = await self.session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
        )

        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    details={
                        "endpoint": endpoint,
                        "hint": "Usa una llave nueva para una operación distinta.",
                    },
                )
            if existing.status == IdempotencyStatus.IN_PROGRESS.value:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_IN_PROGRESS,
                    message="Esta operación ya se está procesando. Espera un momento.",
                    headers={"Retry-After": "3"},
                )
            return existing, True

        record = IdempotencyKey(
            user_id=user_id,
            endpoint=endpoint,
            key=key,
            request_hash=request_hash,
            status=IdempotencyStatus.IN_PROGRESS.value,
            locked_until=datetime.now(UTC) + timedelta(milliseconds=LOCK_TTL_MS),
            expires_at=datetime.now(UTC) + timedelta(days=IDEMPOTENCY_TTL_DAYS),
        )
        self.session.add(record)
        try:
            await self.session.flush()
        except IntegrityError:
            # Carrera: otra request ganó. Se relee y se trata como replay/en curso.
            await self.session.rollback()
            existing = await self.session.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.endpoint == endpoint,
                    IdempotencyKey.key == key,
                )
            )
            if existing is None:
                raise
            if existing.status == IdempotencyStatus.IN_PROGRESS.value:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_IN_PROGRESS,
                    headers={"Retry-After": "3"},
                ) from None
            return existing, True
        return record, False

    async def complete(
        self,
        record: IdempotencyKey,
        *,
        status_code: int,
        body: dict[str, Any],
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        record.status = IdempotencyStatus.COMPLETED.value
        record.status_code = status_code
        record.response_body = body
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.locked_until = None
        await self.session.flush()

    async def fail(self, record: IdempotencyKey, *, reason: str) -> None:
        """Un fallo **no** se guarda como resultado: se libera la llave para permitir reintento."""
        record.status = IdempotencyStatus.FAILED.value
        record.response_body = {"error": {"code": "INTERNAL_ERROR", "message": reason}}
        record.locked_until = None
        await self.session.flush()


async def run_idempotent(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    key: str,
    payload: Any,
    status_code: int,
    operation: Callable[[], Awaitable[tuple[dict[str, Any], str, str]]],
) -> tuple[dict[str, Any], bool]:
    """Ejecuta `operation` una sola vez por (usuario, endpoint, llave).

    `operation` devuelve `(body, resource_type, resource_id)`.
    """
    store = IdempotencyStore(session)
    request_hash = request_fingerprint(payload, endpoint)
    record, is_replay = await store.begin(
        user_id=user_id, endpoint=endpoint, key=key, request_hash=request_hash
    )
    if is_replay:
        return (record.response_body or {}), True

    async with distributed_lock(await _lock_key(user_id, endpoint, key), ttl_ms=LOCK_TTL_MS) as acquired:
        if not acquired:
            raise AppError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, headers={"Retry-After": "3"})
        try:
            body, resource_type, resource_id = await operation()
        except AppError:
            await store.fail(record, reason="domain_error")
            raise
        except Exception as exc:  # noqa: BLE001
            await store.fail(record, reason=str(exc)[:200])
            raise
        await store.complete(
            record,
            status_code=status_code,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        return body, False


def require_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """Dependencia de FastAPI: exige el encabezado con un mensaje accionable."""
    if not idempotency_key:
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            details={"header": "Idempotency-Key", "example": "4f0b7f1e-9a4e-4c3f-9b5a-1e2d3c4b5a69"},
        )
    return idempotency_key


def key_from_request(request: Request) -> str | None:
    return request.headers.get("Idempotency-Key")


async def cleanup_expired(session: AsyncSession, batch: int = 5000) -> int:
    """Job diario: borra llaves vencidas (30 días). Mantiene la tabla pequeña."""
    from sqlalchemy import delete

    result = await session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.id.in_(
                select(IdempotencyKey.id)
                .where(IdempotencyKey.expires_at < datetime.now(UTC))
                .limit(batch)
            )
        )
    )
    return int(result.rowcount or 0)
