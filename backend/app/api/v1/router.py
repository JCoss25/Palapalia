"""Agregador de routers v1. Cada módulo expone `router` con su prefijo y etiquetas."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    availability,
    bookings,
    catalog,
    coupons,
    me,
    notifications,
    payments,
    providers,
    reviews,
    threads,
    venues,
    webhooks,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, tags=["me"])
api_router.include_router(providers.router, prefix="/providers", tags=["providers"])
api_router.include_router(providers.admin_router, prefix="/admin/providers", tags=["admin"])
api_router.include_router(venues.router, prefix="/venues", tags=["venues"])
api_router.include_router(venues.admin_router, prefix="/admin/venues", tags=["admin"])
api_router.include_router(availability.router, prefix="/venues", tags=["availability"])
api_router.include_router(bookings.router, prefix="/bookings", tags=["bookings"])
api_router.include_router(bookings.me_router, prefix="/me/bookings", tags=["bookings"])
api_router.include_router(bookings.admin_router, prefix="/admin/bookings", tags=["admin"])
api_router.include_router(payments.router, tags=["payments"])
api_router.include_router(payments.admin_router, prefix="/admin/payouts", tags=["admin"])
api_router.include_router(reviews.router, tags=["reviews"])
api_router.include_router(threads.router, tags=["messaging"])
api_router.include_router(notifications.router, tags=["notifications"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(coupons.router, prefix="/coupons", tags=["coupons"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
