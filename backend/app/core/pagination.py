"""Paginación: por cursor (listados grandes) y por página (catálogos)."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.errors import AppError, ErrorCode

T = TypeVar("T")

MAX_LIMIT = 50
DEFAULT_LIMIT = 20


@dataclass(frozen=True, slots=True)
class Cursor:
    """Cursor opaco: codifica el último valor de orden + id para desempatar."""

    sort_value: str | None
    id: str

    def encode(self) -> str:
        raw = json.dumps({"v": self.sort_value, "i": self.id}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
            return cls(sort_value=data.get("v"), id=data["i"])
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                message="El cursor de paginación no es válido. Reinicia la lista.",
                details={"field": "cursor"},
            ) from exc


class PageMeta(BaseModel):
    limit: int
    has_more: bool
    next_cursor: str | None = None
    total_estimate: int | None = Field(
        default=None, description="Estimado barato (EXPLAIN); nunca un COUNT(*) sobre millones."
    )


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


def clamp_limit(limit: int | None, *, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="El tamaño de página debe ser al menos 1.",
            details={"field": "limit"},
        )
    return min(limit, maximum)


def build_page(rows: Sequence[Any], limit: int, key: str = "created_at") -> Page[Any]:
    """`rows` debe traer `limit + 1` elementos: el extra indica si hay más."""
    has_more = len(rows) > limit
    visible = list(rows[:limit])
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        value = getattr(last, key, None)
        next_cursor = Cursor(
            sort_value=value.isoformat() if isinstance(value, datetime) else (str(value) if value else None),
            id=str(getattr(last, "id", "")),
        ).encode()
    return Page[Any](
        items=visible,
        meta=PageMeta(limit=limit, has_more=has_more, next_cursor=next_cursor),
    )
