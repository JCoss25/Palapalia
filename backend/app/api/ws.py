"""WebSocket de mensajería: /ws/threads/{id} con message.created, typing y read.

Autenticación: `?token=<access JWT>` (los navegadores no permiten cabeceras en WS).
El servidor valida pertenencia al hilo **en cada conexión**, no sólo al abrir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import TokenType, decode_token
from app.db.session import SessionLocal
from app.models.enums import Role
from app.models.messaging import Message, MessageThread
from app.models.provider import ProviderProfile
from app.models.user import User

ws_router = APIRouter()
log = get_logger("ws")


class ConnectionManager:
    """Registro en memoria por proceso. Con varias réplicas se usa Redis pub/sub como fan-out."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, room: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(room, set()).add(websocket)

    def disconnect(self, room: str, websocket: WebSocket) -> None:
        self._rooms.get(room, set()).discard(websocket)

    async def broadcast(self, room: str, payload: dict, *, exclude: WebSocket | None = None) -> None:
        for socket in list(self._rooms.get(room, set())):
            if socket is exclude:
                continue
            try:
                await socket.send_json(payload)
            except Exception:  # noqa: BLE001
                self.disconnect(room, socket)


manager = ConnectionManager()


async def _authorize(token: str, thread_id: uuid.UUID) -> tuple[User, MessageThread] | None:
    """Valida token y pertenencia. Devuelve None si algo no cuadra: nunca se filtra el motivo."""
    try:
        payload = decode_token(token, expected=TokenType.ACCESS)
        user_id = uuid.UUID(payload.sub)
    except (AppError, ValueError):
        return None

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        thread = await session.scalar(select(MessageThread).where(MessageThread.id == thread_id))
        if user is None or thread is None or user.deleted_at is not None:
            return None
        if user.role in {Role.ADMIN.value, Role.SUPPORT.value}:
            return user, thread
        is_provider = False
        if user.role == Role.PROVIDER.value:
            profile_id = await session.scalar(
                select(ProviderProfile.id).where(ProviderProfile.user_id == user.id)
            )
            is_provider = profile_id is not None and profile_id == thread.provider_id
        if thread.client_id != user.id and not is_provider:
            return None
        return user, thread


@ws_router.websocket("/ws/threads/{thread_id}")
async def thread_socket(
    websocket: WebSocket,
    thread_id: uuid.UUID,
    token: str = Query(..., min_length=20),
) -> None:
    """Protocolo de eventos:
    - cliente → servidor: `{"type": "typing", "is_typing": true}`, `{"type": "read", "message_id": "..."}`
    - servidor → cliente: `message.created`, `typing`, `read`, `error`
    """
    authorized = await _authorize(token, thread_id)
    if authorized is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user, thread = authorized
    room = f"thread:{thread_id}"
    await manager.connect(room, websocket)
    log.info("ws_connected", thread_id=str(thread_id), user_id=str(user.id))

    try:
        await websocket.send_json(
            {
                "type": "ready",
                "thread_id": str(thread_id),
                "server_time": datetime.now(UTC).isoformat(),
                "is_locked": thread.is_locked,
            }
        )
        while True:
            event = await websocket.receive_json()
            kind = event.get("type")

            if kind == "typing":
                await manager.broadcast(
                    room,
                    {
                        "type": "typing",
                        "thread_id": str(thread_id),
                        "user_id": str(user.id),
                        "is_typing": bool(event.get("is_typing", True)),
                    },
                    exclude=websocket,
                )

            elif kind == "read":
                message_id = event.get("message_id")
                if not message_id:
                    continue
                async with SessionLocal() as session:
                    message = await session.get(Message, uuid.UUID(str(message_id)))
                    if message and message.thread_id == thread_id and message.read_at is None:
                        message.read_at = datetime.now(UTC)
                        await session.commit()
                await manager.broadcast(
                    room,
                    {
                        "type": "read",
                        "thread_id": str(thread_id),
                        "message_id": str(message_id),
                        "read_at": datetime.now(UTC).isoformat(),
                    },
                    exclude=websocket,
                )

            elif kind == "ping":
                await websocket.send_json({"type": "pong", "server_time": datetime.now(UTC).isoformat()})

            else:
                await websocket.send_json(
                    {"type": "error", "code": "UNKNOWN_EVENT", "detail": f"Evento no soportado: {kind}"}
                )

    except WebSocketDisconnect:
        manager.disconnect(room, websocket)
        log.info("ws_disconnected", thread_id=str(thread_id), user_id=str(user.id))
    except Exception:  # noqa: BLE001
        manager.disconnect(room, websocket)
        log.exception("ws_error", thread_id=str(thread_id))


async def publish_message_created(thread_id: uuid.UUID, message: Message) -> None:
    """Se llama desde el worker/HTTP tras persistir un mensaje."""
    await manager.broadcast(
        f"thread:{thread_id}",
        {
            "type": "message.created",
            "thread_id": str(thread_id),
            "message": {
                "id": str(message.id),
                "sender_id": str(message.sender_id) if message.sender_id else None,
                "body": message.body,
                "kind": message.kind,
                "created_at": message.created_at.isoformat(),
            },
        },
    )
