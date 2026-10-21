"""Webhooks salientes: suscripciones del proveedor y consulta de entregas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from app.api.deps import DbSession, MyProvider
from app.core.errors import AppError, ErrorCode
from app.core.security import new_secret
from app.models.notification import WebhookDelivery, WebhookSubscription
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.content import (
    WebhookDeliveryOut,
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreated,
    WebhookSubscriptionOut,
)

router = APIRouter()

MAX_SUBSCRIPTIONS = 5


@router.post(
    "/subscriptions",
    response_model=WebhookSubscriptionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Suscribirse a eventos",
    responses={409: {"model": ErrorResponse, "description": "CONFLICT (URL duplicada o máximo alcanzado)"}},
)
async def create_subscription(
    payload: WebhookSubscriptionCreate, session: DbSession, profile: MyProvider
) -> WebhookSubscriptionCreated:
    """El `secret` se muestra **una sola vez**: sirve para verificar la firma HMAC."""
    existing = await session.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.provider_id == profile.id,
            WebhookSubscription.url == payload.url,
            WebhookSubscription.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            message="Ya tienes una suscripción activa a esa URL.",
            details={"subscription_id": str(existing.id)},
        )
    count = len(
        (
            await session.scalars(
                select(WebhookSubscription.id).where(
                    WebhookSubscription.provider_id == profile.id,
                    WebhookSubscription.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if count >= MAX_SUBSCRIPTIONS:
        raise AppError(
            ErrorCode.CONFLICT,
            message=f"Máximo {MAX_SUBSCRIPTIONS} suscripciones por proveedor.",
        )

    secret = new_secret()
    subscription = WebhookSubscription(
        provider_id=profile.id,
        created_by_id=profile.user_id,
        url=payload.url,
        secret=secret,
        events=[event.value for event in payload.events],
        description=payload.description,
    )
    session.add(subscription)
    await session.commit()
    return WebhookSubscriptionCreated(
        id=subscription.id,
        url=subscription.url,
        events=subscription.events,
        description=subscription.description,
        is_active=True,
        created_at=subscription.created_at,
        secret=secret,
    )


@router.get("/subscriptions", response_model=list[WebhookSubscriptionOut], summary="Mis suscripciones")
async def list_subscriptions(session: DbSession, profile: MyProvider) -> list[WebhookSubscriptionOut]:
    rows = (
        await session.scalars(
            select(WebhookSubscription)
            .where(
                WebhookSubscription.provider_id == profile.id,
                WebhookSubscription.deleted_at.is_(None),
            )
            .order_by(WebhookSubscription.created_at.desc())
        )
    ).all()
    return [WebhookSubscriptionOut.model_validate(row) for row in rows]


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar suscripción",
)
async def delete_subscription(
    subscription_id: uuid.UUID, session: DbSession, profile: MyProvider
) -> Response:
    subscription = await session.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.id == subscription_id,
            WebhookSubscription.provider_id == profile.id,
        )
    )
    if subscription is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"subscription_id": str(subscription_id)})
    subscription.deleted_at = datetime.now(UTC)
    subscription.is_active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/deliveries",
    response_model=Paginated[WebhookDeliveryOut],
    summary="Entregas recientes (diagnóstico)",
)
async def list_deliveries(
    session: DbSession,
    profile: MyProvider,
    limit: int = Query(30, ge=1, le=100),
) -> Paginated[WebhookDeliveryOut]:
    """Sin payloads de otros proveedores: sólo las entregas de tus suscripciones."""
    subscription_ids = [
        row
        for row in await session.scalars(
            select(WebhookSubscription.id).where(WebhookSubscription.provider_id == profile.id)
        )
    ]
    if not subscription_ids:
        return Paginated[WebhookDeliveryOut](
            items=[], meta={"limit": limit, "has_more": False, "next_cursor": None}
        )
    rows = (
        await session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.subscription_id.in_(subscription_ids))
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        )
    ).all()
    return Paginated[WebhookDeliveryOut](
        items=[WebhookDeliveryOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )
