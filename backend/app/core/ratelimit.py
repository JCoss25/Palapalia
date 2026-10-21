"""Rate limiting: ventana deslizante en Redis con fallback en memoria.

Reglas por ruta y por usuario/IP. `X-RateLimit-*` siempre en la respuesta; `Retry-After`
en 429. Endpoints de pago/hold tienen cubeta propia (edge case F30: backpressure).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.errors import ErrorCode
from app.core.redis import sliding_window_count

_MEMORY: dict[str, list[float]] = defaultdict(list)


@dataclass(frozen=True, slots=True)
class Limit:
    limit: int
    window_seconds: int


# Ruta (prefijo) → límite. El orden importa: primero el más específico.
RULES: tuple[tuple[str, Limit], ...] = (
    ("/api/v1/auth/login", Limit(10, 60)),
    ("/api/v1/auth/register", Limit(5, 3600)),
    ("/api/v1/auth/otp", Limit(5, 3600)),
    ("/api/v1/auth/forgot-password", Limit(5, 3600)),
    ("/api/v1/auth/refresh", Limit(60, 60)),
    ("/api/v1/bookings/hold", Limit(10, 60)),
    ("/api/v1/payments/intent", Limit(10, 60)),
    ("/api/v1/refunds", Limit(10, 60)),
    ("/api/v1/threads", Limit(120, 60)),
    ("/api/v1/reviews", Limit(20, 60)),
    ("/api/v1/venues", Limit(240, 60)),
    ("/api/v1/search", Limit(240, 60)),
    ("/api/v1", Limit(120, 60)),
)

EXEMPT_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json", "/api/v1/payments/webhook")


def _limit_for(path: str) -> Limit:
    for prefix, limit in RULES:
        if path.startswith(prefix):
            return limit
    return Limit(120, 60)


def _identity(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"u:{user_id}"
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    return f"ip:{ip}"


def _memory_count(key: str, window_seconds: int) -> int:
    now = time.time()
    bucket = [t for t in _MEMORY[key] if t > now - window_seconds]
    bucket.append(now)
    _MEMORY[key] = bucket
    if len(_MEMORY) > 20_000:  # poda defensiva
        for stale in [k for k, v in _MEMORY.items() if not v or v[-1] < now - 3600]:
            _MEMORY.pop(stale, None)
    return len(bucket)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return await call_next(request)

        limit = _limit_for(path)
        key = f"rl:{limit.window_seconds}:{_identity(request)}:{path}"
        try:
            count = await sliding_window_count(key, limit.window_seconds)
        except Exception:  # noqa: BLE001 — Redis caído: cubeta local por proceso
            count = _memory_count(key, limit.window_seconds)

        remaining = max(limit.limit - count, 0)
        headers = {
            "X-RateLimit-Limit": str(limit.limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Window": str(limit.window_seconds),
        }
        if count > limit.limit:
            retry_after = limit.window_seconds
            headers["Retry-After"] = str(retry_after)
            return JSONResponse(
                status_code=429,
                headers=headers,
                content={
                    "error": {
                        "code": ErrorCode.RATE_LIMITED.value,
                        "message": "Demasiadas solicitudes. Espera un momento y vuelve a intentar.",
                        "details": {"retry_after_seconds": retry_after},
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
            )

        response = await call_next(request)
        response.headers.update(headers)
        return response


def adaptive_limit(base: str, load_factor: float) -> str:
    """Ajusta el límite cuando el sistema está bajo presión (edge case F30).

    `load_factor` > 1 = sistema cargado ⇒ se reduce el cupo hasta 50%.
    """
    number, _, unit = base.partition("/")
    scaled = max(int(int(number) / max(load_factor, 1.0)), int(int(number) * 0.5))
    return f"{scaled}/{unit}"


__all__ = ["RateLimitMiddleware", "adaptive_limit", "settings"]
