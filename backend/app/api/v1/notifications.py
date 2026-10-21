"""Notificaciones in-app, preferencias y registro de dispositivos push."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select, update

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError, ErrorCode
from app.models.enums import NotificationKind
from app.models.notification import Notification, NotificationPreference, PushDevice
from app.schemas.common import Paginated
from app.schemas.content import (
    NotificationOut,
    NotificationPreferenceOut,
    NotificationPreferencesPut,
    PushDeviceRegister,
)

router = APIRouter()


@router.get("/notifications", response_model=Paginated[NotificationOut], summary="Mis notificaciones")
async def list_notifications(
    session: DbSession,
    user: CurrentUser,
    unread_only: bool = False,
    kind: NotificationKind | None = None,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
) -> Paginated[NotificationOut]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user.id, Notification.deleted_at.is_(None))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if kind:
        stmt = stmt.where(Notification.kind == kind.value)
    rows = (await session.scalars(stmt)).all()
    return Paginated[NotificationOut](
        items=[NotificationOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.patch(
    "/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Marcar una como leída",
)
async def mark_read(notification_id: uuid.UUID, session: DbSession, user: CurrentUser) -> Response:
    notification = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if notification is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"notification_id": str(notification_id)})
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/notifications/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Marcar todas como leídas",
)
async def mark_all_read(session: DbSession, user: CurrentUser) -> Response:
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/notification-preferences",
    response_model=list[NotificationPreferenceOut],
    summary="Mis preferencias de notificación",
)
async def get_preferences(session: DbSession, user: CurrentUser) -> list[NotificationPreferenceOut]:
    """Devuelve la matriz completa: las que el usuario configuró y los valores por defecto."""
    from app.services.notifications import DEFAULT_CHANNELS

    configured = {
        row.kind: row
        for row in await session.scalars(
            select(NotificationPreference).where(NotificationPreference.user_id == user.id)
        )
    }
    out: list[NotificationPreferenceOut] = []
    for kind in NotificationKind:
        row = configured.get(kind.value)
        if row is None:
            defaults = DEFAULT_CHANNELS.get(kind, set())
            out.append(
                NotificationPreferenceOut(
                    kind=kind,
                    push=any(channel.value == "PUSH" for channel in defaults),
                    email=any(channel.value == "EMAIL" for channel in defaults),
                    sms=False,
                    whatsapp=False,
                )
            )
        else:
            out.append(NotificationPreferenceOut.model_validate(row))
    return out


@router.put(
    "/me/notification-preferences",
    response_model=list[NotificationPreferenceOut],
    summary="Actualizar preferencias",
)
async def put_preferences(
    payload: NotificationPreferencesPut, session: DbSession, user: CurrentUser
) -> list[NotificationPreferenceOut]:
    """Las notificaciones urgentes (dinero, cancelaciones) siempre conservan email."""
    for item in payload.preferences:
        row = await session.scalar(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user.id,
                NotificationPreference.kind == item.kind.value,
            )
        )
        if row is None:
            row = NotificationPreference(user_id=user.id, kind=item.kind.value)
            session.add(row)
        row.push = item.push
        row.email = item.email
        row.sms = item.sms
        row.whatsapp = item.whatsapp
        row.quiet_hours_start = item.quiet_hours_start or payload.global_quiet_hours_start
        row.quiet_hours_end = item.quiet_hours_end or payload.global_quiet_hours_end
    await session.commit()
    return await get_preferences(session, user)


@router.post(
    "/me/push-devices",
    status_code=status.HTTP_201_CREATED,
    summary="Registrar dispositivo para push",
)
async def register_device(
    payload: PushDeviceRegister, session: DbSession, user: CurrentUser
) -> dict:
    existing = await session.scalar(
        select(PushDevice).where(
            PushDevice.user_id == user.id, PushDevice.expo_token == payload.expo_token
        )
    )
    if existing is None:
        existing = PushDevice(
            user_id=user.id,
            expo_token=payload.expo_token,
            platform=payload.platform,
            app_version=payload.app_version,
            device_model=payload.device_model,
        )
        session.add(existing)
    existing.is_active = True
    existing.last_seen_at = datetime.now(UTC)
    await session.commit()
    return {"device_id": str(existing.id), "active": True}


@router.delete(
    "/me/push-devices/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Dar de baja un dispositivo",
)
async def unregister_device(token: str, session: DbSession, user: CurrentUser) -> Response:
    device = await session.scalar(
        select(PushDevice).where(PushDevice.user_id == user.id, PushDevice.expo_token == token)
    )
    if device is not None:
        device.is_active = False
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
