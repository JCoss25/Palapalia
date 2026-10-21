"""Sesión async, engine y dependencia `get_db`."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    str(settings.database_url),
    echo=False,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        # Corta consultas que se salen de presupuesto (búsquedas geoespaciales, edge case F29)
        "server_settings": {
            "statement_timeout": str(settings.database_statement_timeout_ms),
            "application_name": "quinta-api",
            "jit": "off",
        }
    },
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependencia de sesión. El commit lo hace el servicio, no el router."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()
