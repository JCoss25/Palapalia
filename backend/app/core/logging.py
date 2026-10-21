"""Logging estructurado y middleware de request-id.

Nunca se loguea PII en claro: emails/teléfonos pasan por `mask_pii`.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)


def mask_pii(value: str) -> str:
    """juan.perez@mail.com → j***z@mail.com · +525512345678 → +52*****5678"""
    if "@" in value:
        local, _, domain = value.partition("@")
        if len(local) <= 2:
            return f"*@{domain}"
        return f"{local[0]}***{local[-1]}@{domain}"
    if value.startswith("+") or value.replace(" ", "").isdigit():
        digits = "".join(ch for ch in value if ch.isdigit())
        return f"+{digits[:2]}*****{digits[-4:]}" if len(digits) > 6 else "***"
    return "***"


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.is_prod
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.debug else logging.INFO
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "quinta") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Añade request-id, mide latencia y publica cabeceras de trazabilidad."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        request_id_ctx.set(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        started = time.perf_counter()
        log = get_logger("http")
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed", duration_ms=round((time.perf_counter() - started) * 1000, 1))
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            status=response.status_code,
            duration_ms=duration_ms,
            user_id=user_id_ctx.get(),
        )
        return response
