"""Esquemas base: dinero, coordenadas, errores, paginación y validadores compartidos."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base para respuestas construidas desde objetos SQLAlchemy."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")


class Money(BaseModel):
    """Dinero explícito: centavos + moneda. Evita el error clásico de `float`."""

    model_config = ConfigDict(frozen=True)

    amount_cents: int = Field(ge=0, description="Monto en centavos (enteros, sin decimales)")
    currency: str = Field(default="MXN", min_length=3, max_length=3)

    @property
    def as_decimal(self) -> Decimal:
        return Decimal(self.amount_cents) / 100

    @field_serializer("amount_cents")
    def _serialize_amount(self, value: int) -> int:
        return int(value)

    def plus(self, other: Money) -> Money:
        if other.currency != self.currency:
            raise ValueError("No se pueden sumar montos de distinta moneda")
        return Money(amount_cents=self.amount_cents + other.amount_cents, currency=self.currency)


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ErrorBody(BaseModel):
    code: str = Field(examples=["VENUE_NOT_AVAILABLE"])
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class PageMetaOut(BaseModel):
    limit: int
    has_more: bool
    next_cursor: str | None = None
    total_estimate: int | None = None


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMetaOut


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class VenueSort(StrEnum):
    RECOMMENDED = "recommended"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING_DESC = "rating_desc"
    DISTANCE_ASC = "distance_asc"
    NEWEST = "newest"


class TimeWindow(BaseModel):
    """Ventana temporal en UTC. La validación de duración real usa la tz del venue."""

    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Se requiere zona horaria (ISO-8601 con offset, p. ej. 2026-10-17T15:00:00-06:00)")
        return value.astimezone(UTC)

    def model_post_init(self, _context: Any) -> None:
        if self.end_at <= self.start_at:
            raise ValueError("end_at debe ser posterior a start_at")

    @property
    def duration_hours(self) -> float:
        return (self.end_at - self.start_at).total_seconds() / 3600


Locale = Annotated[str, Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")]
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]
PhoneE164 = Annotated[str, Field(pattern=r"^\+[1-9]\d{7,14}$")]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]
UUIDStr = Annotated[str, Field(min_length=36, max_length=36)]


class CursorQuery(BaseModel):
    cursor: str | None = Field(default=None, description="Cursor opaco devuelto por `meta.next_cursor`")
    limit: int = Field(default=20, ge=1, le=50)


class IdempotentResponse(BaseModel):
    """Sobre común de las operaciones con `Idempotency-Key`."""

    id: uuid.UUID
    idempotent_replay: bool = Field(
        default=False, description="True si esta respuesta viene de un reintento, no de un cobro nuevo"
    )


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "degraded"]
    redis: Literal["ok", "degraded"]
    payments: Literal["ok", "degraded", "disabled"]
    maps: Literal["ok", "degraded", "disabled"]
    time: datetime
