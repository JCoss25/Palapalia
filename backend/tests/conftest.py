"""Fixtures de pruebas: app, cliente async, base de datos por test y factorías.

Estrategia:
- Unitarias puras (máquina de estados, precios, moderación): sin base de datos, instantáneas.
- Integración de API: Postgres real (PostGIS) — la constraint de exclusión **no** existe en SQLite,
  así que probar el no-solapamiento exige el motor real.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app as fastapi_app
from app.models import (
    Address,
    Amenity,
    AvailabilityRule,
    Category,
    EventType,
    ProviderProfile,
    User,
    Venue,
)
from app.models.enums import ProviderStatus, Role, UserStatus, VenueStatus

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://quinta:quinta@localhost:5432/quinta_test"
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[Any]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: Any) -> AsyncIterator[AsyncSession]:
    """Cada test corre en su propia transacción que se revierte al final."""
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db_session:
        # `commit()` dentro del código de la app sólo confirma el savepoint
        await db_session.begin_nested()
        yield db_session
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app real, con la sesión de prueba inyectada."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    fastapi_app.dependency_overrides.clear()


# ── Factorías ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client_user(session: AsyncSession) -> User:
    user = User(
        email="mariana@example.com",
        email_normalized="mariana@example.com",
        phone_e164="+529991234567",
        hashed_password=hash_password("Palapa2026!"),
        role=Role.CLIENT.value,
        status=UserStatus.ACTIVE.value,
        full_name="Mariana Coss",
        display_name="Mariana",
        email_verified_at=datetime.now(UTC),
        phone_verified_at=datetime.now(UTC),
        tos_version=settings.tos_version,
        tos_accepted_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def provider_user(session: AsyncSession) -> User:
    user = User(
        email="anfitrion@example.com",
        email_normalized="anfitrion@example.com",
        hashed_password=hash_password("Palapa2026!"),
        role=Role.PROVIDER.value,
        status=UserStatus.ACTIVE.value,
        full_name="Rubén Pech",
        email_verified_at=datetime.now(UTC),
        tos_version=settings.tos_version,
        tos_accepted_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    profile = ProviderProfile(
        user_id=user.id,
        business_name="Palapa Xcambó",
        status=ProviderStatus.APPROVED.value,
        payouts_enabled=True,
        payment_account_id="acct_test_123",
        cancellation_policy="TIERED_STANDARD",
    )
    session.add(profile)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def provider_profile(session: AsyncSession, provider_user: User) -> ProviderProfile:
    from sqlalchemy import select

    return await session.scalar(
        select(ProviderProfile).where(ProviderProfile.user_id == provider_user.id)
    )


@pytest_asyncio.fixture
async def venue(session: AsyncSession, provider_profile: ProviderProfile) -> Venue:
    """Venue publicable: abre todos los días 08:00–23:59 con tarifa base y buffer de 60 min."""
    address = Address(
        street="Calle 21",
        exterior_number="120",
        neighborhood="Centro",
        city="Telchac Puerto",
        state="Yucatán",
        postal_code="97430",
        is_approximate=False,
        geocoded_at=datetime.now(UTC),
        geocoding_provider="test",
    )
    session.add(address)
    await session.flush()

    venue = Venue(
        provider_id=provider_profile.id,
        address_id=address.id,
        name="Palapa Xcambó",
        slug=f"palapa-xcambo-{uuid.uuid4().hex[:6]}",
        headline="Palapa frente al mar para 120 invitados",
        city="Telchac Puerto",
        state="Yucatán",
        timezone="America/Merida",
        capacity_min=20,
        capacity_max=120,
        min_hours=4,
        max_hours=24,
        buffer_minutes=60,
        base_price_cents=840_000,
        currency="MXN",
        deposit_percent=30,
        security_deposit_cents=200_000,
        cleaning_fee_cents=65_000,
        status=VenueStatus.PUBLISHED.value,
        published_at=datetime.now(UTC),
        instant_book=True,
        min_notice_hours=24,
        rating_avg=4.9,
        rating_count=38,
    )
    session.add(venue)
    await session.flush()

    for weekday in range(7):
        session.add(
            AvailabilityRule(
                venue_id=venue.id,
                weekday=weekday,
                slot_kind="FULL_DAY",
                open_time=datetime(2026, 1, 1, 8, 0).time(),
                close_time=datetime(2026, 1, 1, 23, 59).time(),
                crosses_midnight=False,
                slot_minutes=1440,
                min_booking_minutes=240,
            )
        )
    from app.models import PricingRule

    session.add(
        PricingRule(
            venue_id=venue.id,
            kind="BASE",
            name="Tarifa base",
            amount_cents=840_000,
            per_hour=False,
            priority=10,
            min_guests=20,
            extra_guest_cents=3_000,
        )
    )

    category = Category(slug=f"palapa-{uuid.uuid4().hex[:4]}", name="Palapa", sort_order=1)
    amenity = Amenity(slug=f"asador-{uuid.uuid4().hex[:4]}", name="Asador", category="COCINA")
    event_type = EventType(slug=f"xv-{uuid.uuid4().hex[:4]}", name="XV años")
    session.add_all([category, amenity, event_type])
    await session.flush()
    await session.commit()
    return venue


def future_window(days: int = 45, hours: int = 11) -> tuple[datetime, datetime]:
    """Ventana futura válida (respeta la anticipación mínima de 24 h)."""
    start = (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    return start, start + timedelta(hours=hours)


@pytest.fixture
def auth_headers(client_user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(client_user.id, client_user.role.value)}"}


@pytest.fixture
def provider_headers(provider_user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    return {
        "Authorization": f"Bearer {create_access_token(provider_user.id, provider_user.role.value)}"
    }
