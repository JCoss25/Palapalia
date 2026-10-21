"""Mapas y geocodificación con circuit breaker y degradación explícita.

Si el proveedor de mapas falla, la publicación del venue **no** se bloquea: se guarda sin
coordenadas exactas y se marca para validación manual (edge case C21, F31).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
import pybreaker

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import cache_get, cache_set

logger = logging.getLogger(__name__)

#: 5 fallos consecutivos abren el circuito 60 s: deja de castigar al proveedor caído
breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60, name="maps")


@dataclass(slots=True)
class GeocodeResult:
    lat: float
    lng: float
    formatted_address: str
    confidence: float
    provider: str
    is_approximate: bool = False


async def geocode(address_line: str, *, city: str, state: str, postal_code: str | None = None) -> GeocodeResult:
    query = ", ".join(part for part in (address_line, city, state, postal_code, "México") if part)
    cache_key = f"geo:{hash(query)}"
    cached = await cache_get(cache_key)
    if cached:
        return GeocodeResult(**cached)

    if settings.maps_provider == "mapbox":
        result = await _mapbox(query)
    else:
        result = await _google(query)

    await cache_set(cache_key, result.__dict__, ttl_seconds=60 * 60 * 24 * 30)
    return result


async def _mapbox(query: str) -> GeocodeResult:
    if not settings.mapbox_access_token:
        raise AppError(ErrorCode.SERVICE_DEGRADED, details={"provider": "mapbox", "reason": "sin token"})

    async def call() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.geocoding_timeout_seconds) as client:
            response = await client.get(
                "https://api.mapbox.com/geocoding/v5/mapbox.places/"
                f"{httpx.QueryParams({'q': query})['q']}.json",
                params={
                    "access_token": settings.mapbox_access_token,
                    "country": "mx",
                    "limit": 1,
                    "language": "es",
                },
            )
            response.raise_for_status()
            return response.json()

    try:
        data = await breaker.call_async(call)
    except (pybreaker.CircuitBreakerError, httpx.HTTPError) as exc:
        logger.warning("geocode_degraded", extra={"provider": "mapbox", "error": str(exc)})
        raise AppError(
            ErrorCode.ADDRESS_NOT_GEOCODABLE,
            message="No pudimos ubicar la dirección exacta. Guarda el espacio y ajusta el pin en el mapa.",
            details={"provider": "mapbox", "degraded": True, "manual_pin_allowed": True},
        ) from exc

    features = data.get("features") or []
    if not features:
        raise AppError(
            ErrorCode.ADDRESS_NOT_GEOCODABLE,
            message="No encontramos esa dirección. Revisa calle y número, o coloca el pin a mano.",
            details={"query": query, "manual_pin_allowed": True},
        )
    feature = features[0]
    lng, lat = feature["center"]
    relevance = float(feature.get("relevance", 0.5))
    return GeocodeResult(
        lat=lat,
        lng=lng,
        formatted_address=feature.get("place_name", query),
        confidence=relevance,
        provider="mapbox",
        is_approximate=relevance < 0.75,
    )


async def _google(query: str) -> GeocodeResult:
    if not settings.mapbox_access_token:
        raise AppError(ErrorCode.SERVICE_DEGRADED, details={"provider": "google"})
    async with httpx.AsyncClient(timeout=settings.geocoding_timeout_seconds) as client:
        response = await client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": query, "region": "mx", "key": settings.mapbox_access_token},
        )
        response.raise_for_status()
        data = response.json()
    results = data.get("results") or []
    if not results:
        raise AppError(ErrorCode.ADDRESS_NOT_GEOCODABLE, details={"query": query})
    location = results[0]["geometry"]["location"]
    return GeocodeResult(
        lat=location["lat"],
        lng=location["lng"],
        formatted_address=results[0]["formatted_address"],
        confidence=0.9,
        provider="google",
    )


async def reverse_geocode(lat: float, lng: float) -> GeocodeResult:
    """Se usa cuando el proveedor ajusta el pin a mano: la dirección se recalcula del punto."""
    if settings.maps_provider != "mapbox" or not settings.mapbox_access_token:
        raise AppError(ErrorCode.SERVICE_DEGRADED, details={"provider": settings.maps_provider})
    async with httpx.AsyncClient(timeout=settings.geocoding_timeout_seconds) as client:
        response = await client.get(
            f"https://api.mapbox.com/geocoding/v5/mapbox.places/{lng},{lat}.json",
            params={
                "access_token": settings.mapbox_access_token,
                "language": "es",
                "types": "address,place,neighborhood",
            },
        )
        response.raise_for_status()
        data = response.json()
    features = data.get("features") or []
    if not features:
        raise AppError(ErrorCode.ADDRESS_NOT_GEOCODABLE, details={"lat": lat, "lng": lng})
    return GeocodeResult(
        lat=lat,
        lng=lng,
        formatted_address=features[0].get("place_name", ""),
        confidence=float(features[0].get("relevance", 0.5)),
        provider="mapbox",
    )


def point_wkt(lat: float, lng: float) -> str:
    """WKT para PostGIS. Orden (lng lat): el clásico error que mete el venue en el océano."""
    return f"SRID=4326;POINT({lng} {lat})"


def maps_status() -> str:
    if breaker.current_state == "open":
        return "degraded"
    if not settings.mapbox_access_token:
        return "disabled"
    return "ok"
