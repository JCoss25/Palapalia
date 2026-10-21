"""Punto de entrada FastAPI: middlewares, handlers de error, routers y health."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from app.api.v1.router import api_router
from app.api.ws import ws_router
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import RequestContextMiddleware, configure_logging, get_logger
from app.core.ratelimit import RateLimitMiddleware
from app.core.redis import close_redis, ping as redis_ping
from app.db.session import dispose_engine, engine
from app.schemas.common import HealthOut
from app.services import maps

configure_logging()
log = get_logger("app")

DESCRIPTION = """
API del marketplace **Quinta**: palapas, quintas y salones para eventos.

- Autenticación JWT (access 15 min / refresh 30 días) con OAuth2 password flow.
- Escrituras críticas requieren `Idempotency-Key`.
- Errores con códigos estables en `{"error": {"code", ...}}`.
- Toda reserva garantiza no-solapamiento por constraint de exclusión en PostgreSQL.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    log.info("startup", environment=settings.environment, version=app.version)
    yield
    await close_redis()
    await dispose_engine()
    log.info("shutdown")


app = FastAPI(
    title="Quinta API",
    description=DESCRIPTION,
    version="0.1.0",
    openapi_version="3.1.0",
    default_response_class=ORJSONResponse,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    contact={"name": "Quinta", "email": "api@quinta.mx", "url": "https://quinta.mx"},
    license_info={"name": "Proprietary"},
    servers=[
        {"url": "https://api.quinta.mx", "description": "Producción"},
        {"url": "https://api.staging.quinta.mx", "description": "Staging"},
        {"url": "http://localhost:8000", "description": "Local"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "Accept-Language"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)


# ── Handlers de error ─────────────────────────────────────────────────────────


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> ORJSONResponse:
    """Formato único de error. El código es contrato público (ADR-009)."""
    if exc.status_code >= 500:
        log.error("domain_error", code=exc.code.value, detail=exc.details)
    return ORJSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> ORJSONResponse:
    """Pydantic v2: se devuelven los campos exactos, sin filtrar el body completo."""
    fields: list[dict[str, Any]] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        fields.append(
            {
                "field": location or "body",
                "message": error["msg"],
                "type": error["type"],
            }
        )
    return ORJSONResponse(
        status_code=422,
        content={
            "error": {
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "Revisa los datos enviados: hay campos con formato inválido.",
                "details": {"fields": fields},
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> ORJSONResponse:
    log.exception("unhandled_error", path=request.url.path)
    return ORJSONResponse(
        status_code=500,
        content={
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "Algo salió mal de nuestro lado. Ya lo registramos; intenta de nuevo.",
                "details": {},
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


# ── Infra ─────────────────────────────────────────────────────────────────────


@app.middleware("http")
async def add_security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    # Señal de capacidad: permite a los clientes (web, móvil, integraciones) confirmar que están
    # hablando con Quinta y no con otra API que también viva en `/api/v1`.
    response.headers["X-Quinta-API"] = "1"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    if settings.is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/health", response_model=HealthOut, tags=["infra"], summary="Estado del sistema")
async def health() -> HealthOut:
    """`degraded` es un estado de primera clase: la app muestra avisos, no errores (edge case F31)."""
    started = time.perf_counter()
    database = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database = "degraded"
    redis_state = "ok" if await redis_ping() else "degraded"
    payments_state = "ok" if settings.stripe_secret_key else "disabled"
    log.info("health", duration_ms=round((time.perf_counter() - started) * 1000, 2))
    overall = "ok" if database == "ok" and redis_state == "ok" else "degraded"
    from datetime import UTC, datetime

    return HealthOut(
        status=overall,
        version=app.version,
        database=database,
        redis=redis_state,
        payments=payments_state,
        maps=maps.maps_status(),
        time=datetime.now(UTC),
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> dict[str, Any]:
    """Métricas mínimas para el scraper de Prometheus (en prod lo sirve el exporter)."""
    from app.core.redis import get_redis

    counters: dict[str, Any] = {"environment": settings.environment}
    try:
        client = get_redis()
        keys = [key async for key in client.scan_iter(match="metric:*", count=100)]
        counters["custom"] = {key: await client.get(key) for key in keys[:100]}
    except Exception:  # noqa: BLE001
        counters["custom"] = {}
    return counters


app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(ws_router)
