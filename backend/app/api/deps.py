"""Dependencias compartidas: sesión, usuario actual, RBAC, proveedor, paginación."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import user_id_ctx
from app.core.security import TokenType, decode_token
from app.db.session import get_db
from app.models.enums import ProviderStatus, Role, UserStatus
from app.models.provider import ProviderProfile
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login",
    auto_error=False,
    description="JWT de acceso (Bearer). El refresh se envía en el body de /auth/refresh.",
)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    request: Request,
    session: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> User:
    """Resuelve el usuario desde el JWT. Nunca desde un `user_id` del cliente."""
    if not token:
        raise AppError(ErrorCode.UNAUTHORIZED, message="Falta el token de acceso.")
    payload = decode_token(token, expected=TokenType.ACCESS)
    try:
        user_uuid = uuid.UUID(payload.sub)
    except ValueError as exc:
        raise AppError(ErrorCode.UNAUTHORIZED) from exc

    user = await session.scalar(select(User).where(User.id == user_uuid))
    if user is None or user.deleted_at is not None:
        raise AppError(ErrorCode.UNAUTHORIZED, message="La cuenta no existe o fue eliminada.")
    if user.status == UserStatus.BANNED:
        raise AppError(
            ErrorCode.ACCOUNT_SUSPENDED,
            message="Tu cuenta está suspendida. Escribe a soporte@quinta.mx.",
            details={"banned_at": user.banned_at.isoformat() if user.banned_at else None},
        )
    if user.status == UserStatus.SUSPENDED and request.method not in {"GET", "HEAD"}:
        raise AppError(ErrorCode.ACCOUNT_SUSPENDED)

    request.state.user_id = str(user.id)
    user_id_ctx.set(str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role):  # type: ignore[no-untyped-def]
    """RBAC explícito por endpoint. Se declara, no se infiere."""

    async def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise AppError(
                ErrorCode.FORBIDDEN,
                message="No tienes permisos para esta operación.",
                details={"required_roles": [role.value for role in roles], "your_role": user.role.value},
            )
        return user

    return dependency


AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
StaffUser = Annotated[User, Depends(require_roles(Role.ADMIN, Role.SUPPORT))]
ProviderUser = Annotated[User, Depends(require_roles(Role.PROVIDER, Role.ADMIN))]


async def get_my_provider(
    session: DbSession, user: CurrentUser
) -> ProviderProfile:
    """Perfil de proveedor del usuario autenticado, con validación de estado."""
    profile = await session.scalar(
        select(ProviderProfile).where(
            ProviderProfile.user_id == user.id, ProviderProfile.deleted_at.is_(None)
        )
    )
    if profile is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            message="Todavía no tienes perfil de anfitrión. Completa la solicitud primero.",
            details={"next": "POST /providers/apply"},
        )
    return profile


MyProvider = Annotated[ProviderProfile, Depends(get_my_provider)]


async def get_published_provider(
    session: DbSession, user: CurrentUser
) -> ProviderProfile:
    profile = await get_my_provider(session, user)
    if profile.status not in {ProviderStatus.APPROVED}:
        raise AppError(
            ErrorCode.PROVIDER_NOT_APPROVED,
            message="Tu perfil de anfitrión está en revisión. Podrás publicar en cuanto se apruebe.",
            details={"status": profile.status.value},
        )
    return profile


PublishedProvider = Annotated[ProviderProfile, Depends(get_published_provider)]


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key or len(idempotency_key) < 8:
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            details={"header": "Idempotency-Key", "min_length": 8},
        )
    return idempotency_key


IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]


async def pagination(
    limit: int = 20, cursor: str | None = None
) -> dict[str, object]:
    from app.core.pagination import Cursor, clamp_limit

    parsed = None
    if cursor:
        parsed = Cursor.decode(cursor)
    return {"limit": clamp_limit(limit), "cursor": parsed}


Pagination = Annotated[dict, Depends(pagination)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else None)


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Para tareas Celery y scripts: sesión con commit/rollback automáticos."""
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
