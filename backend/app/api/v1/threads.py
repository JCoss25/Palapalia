"""Mensajería: hilos, mensajes, moderación de bypass y banderas de contacto."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession, MyProvider, StaffUser
from app.core.errors import AppError, ErrorCode
from app.models.booking import Booking
from app.models.enums import AuditAction, ContactFlagAction, MessageKind, Role
from app.models.messaging import ContactFlag, Message, MessageThread
from app.models.provider import ProviderProfile
from app.models.user import User
from app.models.venue import Venue
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.content import (
    ContactFlagOut,
    ContactFlagReviewRequest,
    MessageCreateRequest,
    MessageOut,
    ThreadCreateRequest,
    ThreadOut,
)
from app.services import audit, moderation

router = APIRouter()

MAX_FLAGS_BEFORE_LOCK = 3


def _thread_out(thread: MessageThread, *, viewer_id: uuid.UUID, counterpart: User | None) -> ThreadOut:
    unread = thread.provider_unread if thread.client_id == viewer_id else thread.client_unread
    return ThreadOut(
        id=thread.id,
        venue_id=thread.venue_id,
        booking_id=thread.booking_id,
        subject=thread.subject,
        last_message_at=thread.last_message_at,
        last_message_preview=thread.last_message_preview,
        unread_count=unread,
        is_locked=thread.is_locked,
        counterpart_name=(counterpart.display_name or counterpart.full_name) if counterpart else None,
        counterpart_avatar_url=counterpart.avatar_url if counterpart else None,
    )


async def _thread_for_participant(session: DbSession, thread_id: uuid.UUID, user: User) -> MessageThread:
    thread = await session.scalar(select(MessageThread).where(MessageThread.id == thread_id))
    if thread is None or thread.deleted_at is not None:
        raise AppError(ErrorCode.THREAD_NOT_FOUND, details={"thread_id": str(thread_id)})
    is_provider = False
    if user.role == Role.PROVIDER.value:
        profile_id = await session.scalar(
            select(ProviderProfile.id).where(ProviderProfile.user_id == user.id)
        )
        is_provider = profile_id is not None and profile_id == thread.provider_id
    if not (thread.client_id == user.id or is_provider or user.role in {Role.ADMIN.value, Role.SUPPORT.value}):
        raise AppError(ErrorCode.FORBIDDEN, message="Esa conversación no es tuya.")
    return thread


@router.post(
    "/threads",
    response_model=ThreadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir conversación con el anfitrión",
)
async def create_thread(
    payload: ThreadCreateRequest, session: DbSession, user: CurrentUser, request: Request
) -> ThreadOut:
    """Un hilo por (venue, cliente, reserva). Reutiliza el existente en vez de duplicar."""
    venue = await session.scalar(select(Venue).where(Venue.id == payload.venue_id))
    if venue is None:
        raise AppError(ErrorCode.VENUE_NOT_FOUND, details={"venue_id": str(payload.venue_id)})

    existing = await session.scalar(
        select(MessageThread).where(
            MessageThread.venue_id == venue.id,
            MessageThread.client_id == user.id,
            MessageThread.booking_id == payload.booking_id,
            MessageThread.deleted_at.is_(None),
        )
    )
    thread = existing or MessageThread(
        venue_id=venue.id,
        provider_id=venue.provider_id,
        client_id=user.id,
        booking_id=payload.booking_id,
        subject=payload.subject or f"Consulta sobre {venue.name}",
    )
    if existing is None:
        session.add(thread)
        await session.flush()

    if payload.message:
        await _store_message(session, thread=thread, sender=user, payload=MessageCreateRequest(body=payload.message))
    await audit.record(
        session,
        action=AuditAction.PROFILE_UPDATED,
        entity="message_thread",
        entity_id=thread.id,
        actor_id=user.id,
        reason="apertura de conversación",
        request=request,
    )
    await session.commit()
    return _thread_out(thread, viewer_id=user.id, counterpart=None)


@router.get("/threads", response_model=Paginated[ThreadOut], summary="Mis conversaciones")
async def list_threads(
    session: DbSession,
    user: CurrentUser,
    role: str = Query("client", pattern="^(client|provider)$"),
    limit: int = Query(20, ge=1, le=50),
) -> Paginated[ThreadOut]:
    stmt = select(MessageThread).where(MessageThread.deleted_at.is_(None))
    if role == "provider":
        profile_id = await session.scalar(
            select(ProviderProfile.id).where(ProviderProfile.user_id == user.id)
        )
        if profile_id is None:
            raise AppError(ErrorCode.FORBIDDEN, message="No tienes perfil de anfitrión.")
        stmt = stmt.where(MessageThread.provider_id == profile_id)
    else:
        stmt = stmt.where(MessageThread.client_id == user.id)
    threads = (
        await session.scalars(
            stmt.order_by(MessageThread.last_message_at.desc().nulls_last()).limit(limit)
        )
    ).all()

    out: list[ThreadOut] = []
    for thread in threads:
        counterpart_id = thread.provider_id if thread.client_id == user.id else thread.client_id
        counterpart = await session.scalar(
            select(User).where(
                User.id == counterpart_id
                if thread.client_id == user.id
                else User.provider_profile.has(ProviderProfile.id == counterpart_id)
            )
        )
        out.append(_thread_out(thread, viewer_id=user.id, counterpart=counterpart))
    return Paginated[ThreadOut](
        items=out, meta={"limit": limit, "has_more": len(threads) == limit, "next_cursor": None}
    )


@router.get(
    "/threads/{thread_id}/messages",
    response_model=Paginated[MessageOut],
    summary="Mensajes del hilo",
)
async def list_messages(
    thread_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    limit: int = Query(30, ge=1, le=100),
    before: datetime | None = None,
) -> Paginated[MessageOut]:
    thread = await _thread_for_participant(session, thread_id, user)
    stmt = select(Message).where(Message.thread_id == thread.id, Message.deleted_at.is_(None))
    if before:
        stmt = stmt.where(Message.created_at < before)
    rows = (await session.scalars(stmt.order_by(Message.created_at.desc()).limit(limit))).all()
    messages = list(reversed(rows))
    return Paginated[MessageOut](
        items=[
            MessageOut(
                id=message.id,
                thread_id=message.thread_id,
                sender_id=message.sender_id,
                kind=MessageKind(message.kind),
                body=message.body,
                attachments=message.attachments or [],
                read_at=message.read_at,
                created_at=message.created_at,
                moderation_status=message.moderation_status,
                bypass_score=float(message.bypass_score),
                is_mine=message.sender_id == user.id,
            )
            for message in messages
        ],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


async def _store_message(
    session: DbSession, *, thread: MessageThread, sender: User, payload: MessageCreateRequest
) -> tuple[Message, str | None]:
    """Guarda el mensaje **siempre** y devuelve la advertencia de moderación si aplica (C19)."""
    verdict = moderation.detect_contact(payload.body)
    warning = verdict.warning_message if verdict.should_warn else None
    if thread.is_locked and verdict.score >= 45:
        raise AppError(
            ErrorCode.MESSAGE_BLOCKED_BY_MODERATION,
            message="Esta conversación está limitada por intentos repetidos de sacarla de la app. Escribe a soporte si necesitas ayuda.",
            details={"thread_id": str(thread.id)},
        )

    message = Message(
        thread_id=thread.id,
        sender_id=sender.id,
        kind=payload.kind.value,
        body=payload.body,
        attachments=payload.attachments,
        bypass_score=verdict.score,
        detected_entities=verdict.entities,
        moderation_status="APPROVED" if verdict.score < 80 else "NEEDS_REVIEW",
    )
    session.add(message)
    await session.flush()

    thread.last_message_at = datetime.now(UTC)
    thread.last_message_preview = payload.body[:160]
    if sender.id == thread.client_id:
        thread.provider_unread += 1
    else:
        thread.client_unread += 1

    if verdict.score >= 45:
        for entity_type, values in verdict.entities.items():
            for value in values[:3]:
                session.add(
                    ContactFlag(
                        message_id=message.id,
                        thread_id=thread.id,
                        user_id=sender.id,
                        entity_type=entity_type,
                        entity_hash=moderation.entity_hash(value),
                        score=verdict.score,
                        action=verdict.action,
                        warning_shown=warning is not None,
                    )
                )
        thread.risk_score = float(thread.risk_score) + verdict.score / 10
        flag_count = await session.scalar(
            select(func.count()).select_from(ContactFlag).where(ContactFlag.thread_id == thread.id)
        )
        if (flag_count or 0) >= MAX_FLAGS_BEFORE_LOCK and thread.risk_score >= 20:
            # Se limita la conversación, no se expulsa: la revisión humana decide (C19)
            thread.is_locked = True
            thread.locked_reason = "intentos repetidos de compartir contacto fuera de la plataforma"

    from app.tasks.notifications import notify_new_message

    notify_new_message.delay(str(message.id))
    return message, warning


@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar mensaje",
    responses={
        422: {"model": ErrorResponse, "description": "MESSAGE_BLOCKED_BY_MODERATION"},
    },
)
async def send_message(
    thread_id: uuid.UUID, payload: MessageCreateRequest, session: DbSession, user: CurrentUser
) -> MessageOut:
    """El filtro anti-bypass advierte y marca; sólo bloquea cuando el hilo ya está limitado."""
    thread = await _thread_for_participant(session, thread_id, user)
    message, warning = await _store_message(session, thread=thread, sender=user, payload=payload)
    await session.commit()
    return MessageOut(
        id=message.id,
        thread_id=message.thread_id,
        sender_id=message.sender_id,
        kind=MessageKind(message.kind),
        body=message.body,
        attachments=message.attachments or [],
        created_at=message.created_at,
        moderation_status=message.moderation_status,
        contact_warning=warning,
        bypass_score=float(message.bypass_score),
        is_mine=True,
    )


@router.patch("/messages/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT, summary="Marcar como leído")
async def mark_read(message_id: uuid.UUID, session: DbSession, user: CurrentUser) -> None:
    message = await session.scalar(select(Message).where(Message.id == message_id))
    if message is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"message_id": str(message_id)})
    thread = await _thread_for_participant(session, message.thread_id, user)
    if message.sender_id != user.id and message.read_at is None:
        message.read_at = datetime.now(UTC)
        if user.id == thread.client_id:
            thread.client_unread = 0
        else:
            thread.provider_unread = 0
    await session.commit()


admin_router = APIRouter()


@admin_router.get("/contact-flags", response_model=Paginated[ContactFlagOut], summary="Banderas de bypass")
async def list_contact_flags(
    session: DbSession,
    staff: StaffUser,
    action: ContactFlagAction | None = None,
    limit: int = Query(30, ge=1, le=100),
) -> Paginated[ContactFlagOut]:
    stmt = select(ContactFlag).order_by(ContactFlag.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(ContactFlag.action == action.value)
    rows = (await session.scalars(stmt)).all()
    return Paginated[ContactFlagOut](
        items=[ContactFlagOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@admin_router.patch("/contact-flags/{flag_id}", response_model=ContactFlagOut, summary="Revisar bandera")
async def review_contact_flag(
    flag_id: uuid.UUID,
    payload: ContactFlagReviewRequest,
    session: DbSession,
    staff: StaffUser,
    request: Request,
) -> ContactFlagOut:
    """La decisión humana define la acción final: WARN, REVIEW o BLOCK (nunca automático)."""
    flag = await session.scalar(select(ContactFlag).where(ContactFlag.id == flag_id))
    if flag is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"flag_id": str(flag_id)})
    flag.action = payload.action.value
    flag.reviewed_by_id = staff.id
    flag.reviewed_at = datetime.now(UTC)
    flag.review_note = payload.note

    thread = await session.get(MessageThread, flag.thread_id)
    if thread is not None and payload.action == ContactFlagAction.BLOCK:
        thread.is_locked = True
        thread.locked_reason = payload.note
    if payload.warn_user:
        from app.tasks.notifications import notify_contact_warning

        notify_contact_warning.delay(str(flag.user_id), payload.note)
    await audit.record(
        session,
        action=AuditAction.CONTACT_FLAG_REVIEWED,
        entity="contact_flag",
        entity_id=flag.id,
        actor_id=staff.id,
        actor_role=staff.role.value,
        after={"action": payload.action.value},
        reason=payload.note,
        request=request,
    )
    await session.commit()
    return ContactFlagOut.model_validate(flag)
