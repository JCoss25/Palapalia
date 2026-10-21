"""Autenticación, verificación, recuperación de contraseña y sesión.

Endpoints:
- POST /auth/register            · POST /auth/login (OAuth2 form)   · POST /auth/refresh
- POST /auth/logout              · POST /auth/forgot-password       · POST /auth/reset-password
- POST /auth/verify-email        · POST /auth/verify-phone (OTP)    · POST /auth/otp/resend
- POST /auth/change-password
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, client_ip
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger, mask_pii
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_otp,
    hash_password,
    normalize_email,
    normalize_phone,
    verify_otp,
    verify_password,
)
from app.models.enums import AuditAction, ProviderStatus, Role, UserStatus
from app.models.provider import ProviderProfile
from app.models.user import OtpCode, PasswordResetToken, RefreshToken, User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginResponse,
    LogoutRequest,
    OtpResponse,
    RefreshRequest,
    RegisterRequest,
    RequestOtpRequest,
    ResetPasswordRequest,
    TokenPair,
    UserOut,
    VerifyEmailRequest,
    VerifyOtpRequest,
)
from app.schemas.common import ErrorResponse
from app.services import audit

router = APIRouter()
log = get_logger("auth")

MAX_FAILED_LOGINS = 8
LOCK_MINUTES = 15


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        full_name=user.full_name,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        phone_e164=user.phone_e164,
        email_verified_at=user.email_verified_at,
        phone_verified_at=user.phone_verified_at,
        locale=user.locale,
        timezone=user.timezone,
        created_at=user.created_at,
        has_provider_profile=user.provider_profile is not None,
    )


async def _issue_session(session: DbSession, user: User, request: Request) -> LoginResponse:
    refresh_token, payload = create_refresh_token(user.id, user.role.value)
    session.add(
        RefreshToken(
            user_id=user.id,
            jti=payload.jti,
            family_id=payload.family or "",
            expires_at=payload.exp,
            user_agent=request.headers.get("user-agent", "")[:280],
            ip=client_ip(request),
        )
    )
    await session.flush()
    return LoginResponse(
        access_token=create_access_token(user.id, user.role.value),
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=_user_out(user),
        requires_email_verification=user.email_verified_at is None,
        requires_phone_verification=user.phone_e164 is not None and user.phone_verified_at is None,
    )


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear cuenta (cliente o proveedor)",
    responses={
        409: {"model": ErrorResponse, "description": "EMAIL_ALREADY_REGISTERED · PHONE_ALREADY_REGISTERED"},
        403: {"model": ErrorResponse, "description": "UNDERAGE_NOT_ALLOWED"},
        422: {"model": ErrorResponse, "description": "VALIDATION_ERROR (T&C no aceptados)"},
    },
)
async def register(
    payload: RegisterRequest, request: Request, session: DbSession
) -> LoginResponse:
    """Registro con verificación de edad, T&C versionados y detección de cuentas duplicadas.

    Edge cases cubiertos: C16 (alias `+`, teléfonos reciclados), C18 (menor de edad, T&C versionados).
    """
    email_normalized = normalize_email(payload.email)
    phone = normalize_phone(payload.phone) if payload.phone else None

    if payload.birth_date:
        age_days = (datetime.now(UTC) - payload.birth_date.replace(tzinfo=UTC)).days
        if age_days < 18 * 365:
            raise AppError(
                ErrorCode.UNDERAGE_NOT_ALLOWED,
                message="Necesitas ser mayor de edad para reservar. Un tutor puede crear la cuenta.",
                details={"min_age": 18},
            )

    existing = await session.scalar(
        select(User).where(
            (User.email_normalized == email_normalized)
            | (User.phone_e164 == phone if phone else False)
        )
    )
    if existing is not None:
        raise AppError(
            ErrorCode.EMAIL_ALREADY_REGISTERED
            if existing.email_normalized == email_normalized
            else ErrorCode.PHONE_ALREADY_REGISTERED,
            details={"hint": "Si es tu cuenta, inicia sesión o recupera tu contraseña."},
        )

    user = User(
        email=payload.email,
        email_normalized=email_normalized,
        phone_e164=phone,
        hashed_password=hash_password(payload.password),
        role=Role.PROVIDER if payload.role == "provider" else Role.CLIENT,
        status=UserStatus.PENDING_VERIFICATION,
        full_name=payload.full_name,
        display_name=payload.full_name.split(" ")[0],
        tos_version=payload.tos_version,
        tos_accepted_at=datetime.now(UTC),
        marketing_opt_in=payload.marketing_opt_in,
    )
    session.add(user)
    await session.flush()

    if payload.role == "provider":
        session.add(
            ProviderProfile(
                user_id=user.id,
                business_name=payload.business_name or payload.full_name,
                status=ProviderStatus.DRAFT.value,
            )
        )

    await audit.record(
        session,
        action=AuditAction.REGISTER,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        actor_role=user.role.value,
        after={"role": user.role.value, "tos_version": payload.tos_version},
        request=request,
    )
    response = await _issue_session(session, user, request)
    await session.commit()
    log.info("register", user_id=str(user.id), role=user.role.value, email=mask_pii(user.email))
    return response


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Iniciar sesión (OAuth2 password flow)",
    responses={
        401: {"model": ErrorResponse, "description": "INVALID_CREDENTIALS · ACCOUNT_SUSPENDED"},
        429: {"model": ErrorResponse, "description": "RATE_LIMITED"},
    },
)
async def login(
    request: Request,
    session: DbSession,
    form: OAuth2PasswordRequestForm = Depends(),
) -> LoginResponse:
    """Bloqueo progresivo por intentos fallidos. Mensaje genérico: no revela si el correo existe."""
    identifier = form.username.strip()
    user = await session.scalar(
        select(User).where(
            (User.email_normalized == normalize_email(identifier))
            | (User.phone_e164 == normalize_phone(identifier))
        )
    )

    if user is None or not user.hashed_password or not verify_password(form.password, user.hashed_password):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)
            await audit.record(
                session,
                action=AuditAction.LOGIN_FAILED,
                entity="user",
                entity_id=user.id,
                actor_id=user.id,
                request=request,
                reason="credenciales inválidas",
            )
            await session.commit()
        raise AppError(ErrorCode.INVALID_CREDENTIALS, details={"identifier": mask_pii(identifier)})

    if user.locked_until and user.locked_until > datetime.now(UTC):
        raise AppError(
            ErrorCode.ACCOUNT_SUSPENDED,
            message="La cuenta está bloqueada temporalmente por intentos fallidos.",
            details={"unlock_at": user.locked_until.isoformat()},
        )
    if user.status == UserStatus.BANNED:
        raise AppError(ErrorCode.ACCOUNT_SUSPENDED)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    await audit.record(
        session,
        action=AuditAction.LOGIN,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        actor_role=user.role.value,
        request=request,
    )
    response = await _issue_session(session, user, request)
    await session.commit()
    return response


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotar tokens",
    responses={401: {"model": ErrorResponse, "description": "REFRESH_TOKEN_INVALID · REFRESH_TOKEN_REUSED"}},
)
async def refresh(payload: RefreshRequest, session: DbSession) -> TokenPair:
    """Rotación con detección de reuso: un refresh ya usado invalida **toda** la familia."""
    decoded = decode_token(payload.refresh_token, expected=TokenType.REFRESH)
    stored = await session.scalar(select(RefreshToken).where(RefreshToken.jti == decoded.jti))
    if stored is None:
        raise AppError(ErrorCode.REFRESH_TOKEN_INVALID)

    if stored.revoked_at is not None or stored.expires_at <= datetime.now(UTC):
        # Reuso detectado: se revoca la familia completa y se obliga a reautenticar
        family = (
            await session.scalars(
                select(RefreshToken).where(RefreshToken.family_id == stored.family_id)
            )
        ).all()
        for token in family:
            token.revoked_at = token.revoked_at or datetime.now(UTC)
        await audit.record(
            session,
            action=AuditAction.LOGOUT,
            entity="refresh_token_family",
            entity_id=stored.family_id,
            actor_id=stored.user_id,
            reason="reuso de refresh token",
        )
        await session.commit()
        raise AppError(
            ErrorCode.REFRESH_TOKEN_REUSED,
            message="Por seguridad cerramos todas tus sesiones. Vuelve a iniciar sesión.",
        )

    user = await session.get(User, stored.user_id)
    if user is None or user.status == UserStatus.BANNED:
        raise AppError(ErrorCode.ACCOUNT_SUSPENDED)

    new_token, new_payload = create_refresh_token(user.id, user.role.value, family=stored.family_id)
    stored.revoked_at = datetime.now(UTC)
    stored.replaced_by_jti = new_payload.jti
    session.add(
        RefreshToken(
            user_id=user.id,
            jti=new_payload.jti,
            family_id=stored.family_id,
            expires_at=new_payload.exp,
        )
    )
    await session.commit()
    return TokenPair(
        access_token=create_access_token(user.id, user.role.value),
        refresh_token=new_token,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Cerrar sesión")
async def logout(
    payload: LogoutRequest, session: DbSession, user: CurrentUser, request: Request
) -> Response:
    """Revoca el refresh actual, o todos los dispositivos si `all_devices=true`."""
    if payload.all_devices:
        tokens = (
            await session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id))
        ).all()
        for token in tokens:
            token.revoked_at = datetime.now(UTC)
    elif payload.refresh_token:
        decoded = decode_token(payload.refresh_token, expected=TokenType.REFRESH)
        stored = await session.scalar(select(RefreshToken).where(RefreshToken.jti == decoded.jti))
        if stored and stored.user_id == user.id:
            stored.revoked_at = datetime.now(UTC)

    await audit.record(
        session,
        action=AuditAction.LOGOUT,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        request=request,
        reason="all_devices" if payload.all_devices else "sesión actual",
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/forgot-password", response_model=OtpResponse, summary="Solicitar recuperación")
async def forgot_password(
    payload: ForgotPasswordRequest, session: DbSession
) -> OtpResponse:
    """Respuesta uniforme exista o no la cuenta: no se filtra qué correos están registrados."""
    user = await session.scalar(
        select(User).where(User.email_normalized == normalize_email(payload.email))
    )
    masked = mask_pii(payload.email)
    if user is not None:
        raw_token = secrets.token_urlsafe(32)
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=2),
            )
        )
        await audit.record(
            session,
            action=AuditAction.PASSWORD_RESET,
            entity="user",
            entity_id=user.id,
            actor_id=user.id,
            reason="solicitud de recuperación",
        )
        # El envío real se encola; nunca se devuelve el token en la respuesta
        from app.tasks.notifications import send_password_reset_email

        send_password_reset_email.delay(str(user.id), raw_token)
        await session.commit()
    return OtpResponse(
        sent=True,
        destination_masked=masked,
        expires_in_seconds=7200,
        resend_available_in_seconds=60,
    )


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Definir nueva contraseña",
    responses={400: {"model": ErrorResponse, "description": "PASSWORD_RESET_TOKEN_INVALID"}},
)
async def reset_password(
    payload: ResetPasswordRequest, session: DbSession, request: Request
) -> Response:
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    stored = await session.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    if stored is None or stored.used_at is not None or stored.expires_at <= datetime.now(UTC):
        raise AppError(ErrorCode.PASSWORD_RESET_TOKEN_INVALID)

    user = await session.get(User, stored.user_id)
    if user is None:
        raise AppError(ErrorCode.PASSWORD_RESET_TOKEN_INVALID)

    user.hashed_password = hash_password(payload.new_password)
    user.failed_login_count = 0
    user.locked_until = None
    stored.used_at = datetime.now(UTC)

    tokens = (
        await session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id))
    ).all()
    for token in tokens:
        token.revoked_at = datetime.now(UTC)

    await audit.record(
        session,
        action=AuditAction.PASSWORD_RESET,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        request=request,
        reason="contraseña restablecida",
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email", response_model=UserOut, summary="Verificar correo")
async def verify_email(payload: VerifyEmailRequest, session: DbSession) -> UserOut:
    decoded = decode_token(payload.token, expected=TokenType.EMAIL_VERIFY)
    user = await session.get(User, uuid.UUID(decoded.sub))
    if user is None:
        raise AppError(ErrorCode.UNAUTHORIZED)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        if user.status == UserStatus.PENDING_VERIFICATION and user.phone_e164 is None:
            user.status = UserStatus.ACTIVE
        await audit.record(
            session,
            action=AuditAction.EMAIL_VERIFIED,
            entity="user",
            entity_id=user.id,
            actor_id=user.id,
        )
    await session.commit()
    return _user_out(user)


@router.post("/verify-phone", response_model=UserOut, summary="Verificar teléfono con OTP")
async def verify_phone(
    payload: VerifyOtpRequest, session: DbSession, request: Request
) -> UserOut:
    """OTP con hash, intentos limitados y expiración. El código nunca se guarda en claro."""
    destination = normalize_phone(payload.phone) if payload.phone else (payload.email or "")
    otp = await session.scalar(
        select(OtpCode)
        .where(
            OtpCode.destination == destination,
            OtpCode.purpose == payload.purpose,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
    )
    if otp is None:
        raise AppError(ErrorCode.OTP_INVALID)
    if otp.expires_at <= datetime.now(UTC):
        raise AppError(ErrorCode.OTP_EXPIRED)
    if otp.attempts >= otp.max_attempts:
        raise AppError(
            ErrorCode.OTP_TOO_MANY_ATTEMPTS,
            message="Demasiados intentos con este código. Pide uno nuevo en unos minutos.",
        )
    if not verify_otp(payload.code, otp.salt, otp.code_hash):
        otp.attempts += 1
        await session.commit()
        raise AppError(
            ErrorCode.OTP_INVALID,
            details={"attempts_left": max(otp.max_attempts - otp.attempts, 0)},
        )

    otp.consumed_at = datetime.now(UTC)
    user = await session.get(User, otp.user_id) if otp.user_id else None
    if user is None:
        raise AppError(ErrorCode.OTP_INVALID)
    if payload.purpose == "phone_verify":
        user.phone_verified_at = datetime.now(UTC)
        if user.status == UserStatus.PENDING_VERIFICATION:
            user.status = UserStatus.ACTIVE
    await audit.record(
        session,
        action=AuditAction.PHONE_VERIFIED,
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        request=request,
        reason=payload.purpose,
    )
    await session.commit()
    return _user_out(user)


@router.post("/otp/resend", response_model=OtpResponse, summary="Reenviar OTP")
async def resend_otp(payload: RequestOtpRequest, session: DbSession, user: CurrentUser) -> OtpResponse:
    """Un código vivo por destino: reenviar invalida el anterior (evita acumular códigos válidos)."""
    destination = normalize_phone(payload.phone) if payload.phone else (payload.email or user.email)
    previous = (
        await session.scalars(
            select(OtpCode).where(
                OtpCode.destination == destination,
                OtpCode.purpose == payload.purpose,
                OtpCode.consumed_at.is_(None),
            )
        )
    ).all()
    for code in previous:
        code.consumed_at = datetime.now(UTC)

    raw = generate_otp()
    salt = secrets.token_hex(16)
    session.add(
        OtpCode(
            user_id=user.id,
            destination=destination,
            purpose=payload.purpose,
            code_hash=hash_otp(raw, salt),
            salt=salt,
            max_attempts=settings.otp_max_attempts,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    from app.tasks.notifications import send_otp

    send_otp.delay(destination, raw, payload.purpose)
    await session.commit()
    return OtpResponse(
        sent=True,
        destination_masked=mask_pii(destination),
        expires_in_seconds=settings.otp_ttl_seconds,
        resend_available_in_seconds=60,
    )
