"""Cliente Redis: cache, locks, rate limit y contadores.

Todo degrada con gracia: si Redis no responde, la API sigue funcionando (sin cache ni
rate limit distribuido) y se marca `degraded` en los logs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            str(settings.redis_url),
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_timeout=1.5,
            socket_connect_timeout=1.5,
            health_check_interval=30,
        )
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:  # noqa: BLE001 — degradación explícita
        logger.warning("redis_unavailable", extra={"event": "redis_unavailable"})
        return False


# ── Cache ──────────────────────────────────────────────────────────────────────


async def cache_get(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
    except Exception:  # noqa: BLE001
        return None
    return json.loads(raw) if raw else None


async def cache_set(key: str, value: Any, ttl_seconds: int = 60) -> None:
    with contextlib.suppress(Exception):
        await get_redis().set(key, json.dumps(value, default=str), ex=ttl_seconds)


async def cache_delete_prefix(prefix: str) -> int:
    """Invalida por prefijo con SCAN (nunca KEYS en producción)."""
    deleted = 0
    try:
        client = get_redis()
        async for key in client.scan_iter(match=f"{prefix}*", count=500):
            deleted += await client.delete(key)
    except Exception:  # noqa: BLE001
        return deleted
    return deleted


# ── Locks (advisory en app; la garantía real es la constraint de exclusión) ────


@contextlib.asynccontextmanager
async def distributed_lock(key: str, ttl_ms: int = 8000, wait_ms: int = 1500) -> AsyncIterator[bool]:
    client = get_redis()
    token = f"{time.time_ns()}"
    acquired = False
    deadline = time.monotonic() + wait_ms / 1000
    try:
        while time.monotonic() < deadline:
            acquired = bool(await client.set(key, token, nx=True, px=ttl_ms))
            if acquired:
                break
            await _sleep(0.05)
        yield acquired
    except Exception:  # noqa: BLE001 — Redis caído ⇒ seguimos sin lock (la DB protege)
        logger.warning("lock_unavailable", extra={"key": key})
        yield True
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                if await client.get(key) == token:
                    await client.delete(key)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


# ── Contadores (rate limit, cupos de hold) ────────────────────────────────────


async def incr_with_ttl(key: str, ttl_seconds: int) -> int:
    client = get_redis()
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl_seconds, nx=True)
    value, _ = await pipe.execute()
    return int(value)


async def sliding_window_count(key: str, window_seconds: int) -> int:
    """Ventana deslizante por miembro único; devuelve el total en la ventana."""
    client = get_redis()
    now = time.time()
    member = f"{now:.6f}"
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds + 1)
    _, _, count, _ = await pipe.execute()
    return int(count)
