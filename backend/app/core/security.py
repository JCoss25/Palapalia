"""Primitivas de seguridad: hash de contraseñas, JWT, OTP, HMAC y tokens de check-in."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.errors import AppError, ErrorCode

ALGORITHM: Final = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# Palabras prohibidas como contraseña (evita el top-100 de siempre)
COMMON_PASSWORDS: Final = frozenset(
    {"12345678", "contrasena", "password", "qwerty123", "iloveyou", "quinta123", "00000000"}
)


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"
    EMAIL_VERIFY = "email_verify"
    PASSWORD_RESET = "password_reset"
    QR_CHECKIN = "qr_checkin"


def hash_password(raw: str) -> str:
    if len(raw) < 8 or raw.lower() in COMMON_PASSWORDS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            message="La contraseña debe tener al menos 8 caracteres y no ser una de las más usadas.",
            details={"field": "password"},
        )
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except ValueError:
        # hash corrupto o algoritmo desconocido
        return False


def normalize_email(email: str) -> str:
    """Colapsa el alias `+` de Gmail/Outlook para detectar cuentas duplicadas (edge case C16).

    Se guarda `email` (como lo escribió el usuario, para enviar correo) y
    `email_normalized` (para unicidad). Nunca se usa el normalizado para enviar.
    """
    local, _, domain = email.strip().lower().partition("@")
    local = local.split("+", 1)[0]
    domain = domain.removeprefix("googlemail.com") if domain == "googlemail.com" else domain
    return f"{local}@{domain}"


def normalize_phone(phone: str) -> str:
    """E.164 sin espacios. México: se asume +52 si no trae lada."""
    digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        return digits
    digits = digits.lstrip("0")
    if len(digits) == 10:
        return f"+52{digits}"
    if len(digits) == 12 and digits.startswith("52"):
        return f"+{digits}"
    return f"+{digits}"


@dataclass(slots=True)
class TokenPayload:
    sub: str
    type: TokenType
    jti: str
    exp: datetime
    role: str | None = None
    family: str | None = None
    scope: str | None = None


def create_token(
    subject: str | uuid.UUID,
    token_type: TokenType,
    *,
    expires_in: timedelta,
    role: str | None = None,
    family: str | None = None,
    scope: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, TokenPayload]:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type.value,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "iss": "quinta",
        "aud": "quinta-app",
    }
    if role:
        payload["role"] = role
    if family:
        payload["family"] = family
    if scope:
        payload["scope"] = scope
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    parsed = TokenPayload(
        sub=str(subject),
        type=token_type,
        jti=payload["jti"],
        exp=now + expires_in,
        role=role,
        family=family,
        scope=scope,
    )
    return token, parsed


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    token, _ = create_token(
        user_id,
        TokenType.ACCESS,
        expires_in=timedelta(minutes=settings.access_token_ttl_minutes),
        role=role,
    )
    return token


def create_refresh_token(user_id: uuid.UUID, role: str, family: str | None = None) -> tuple[str, TokenPayload]:
    return create_token(
        user_id,
        TokenType.REFRESH,
        expires_in=timedelta(days=settings.refresh_token_ttl_days),
        role=role,
        family=family or str(uuid.uuid4()),
    )


def decode_token(token: str, expected: TokenType | None = None) -> TokenPayload:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[ALGORITHM], audience="quinta-app", issuer="quinta"
        )
    except JWTError as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, message="Tu sesión no es válida o expiró.") from exc

    token_type = TokenType(payload.get("type", ""))
    if expected and token_type is not expected:
        raise AppError(ErrorCode.UNAUTHORIZED, message="Tipo de token incorrecto para esta operación.")
    return TokenPayload(
        sub=payload["sub"],
        type=token_type,
        jti=payload["jti"],
        exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
        role=payload.get("role"),
        family=payload.get("family"),
        scope=payload.get("scope"),
    )


# ── OTP ────────────────────────────────────────────────────────────────────────


def generate_otp(length: int = 6) -> str:
    """OTP numérico con `secrets`. Nunca `random`. Evita códigos triviales repetidos."""
    while True:
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        if len(set(code)) > 2 and code not in {"123456", "654321", "000000", "111111"}:
            return code


def hash_otp(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def verify_otp(code: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code, salt), expected_hash)


# ── Firmas HMAC (webhooks salientes y tokens de check-in) ─────────────────────


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = timestamp or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(
    secret: str,
    body: bytes,
    signature_header: str,
    *,
    tolerance_seconds: int | None = None,
) -> bool:
    tolerance = tolerance_seconds or settings.webhook_signature_tolerance_seconds
    parts = dict(
        item.split("=", 1) for item in signature_header.split(",") if "=" in item
    )
    try:
        ts = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def new_secret(prefix: str = "whsec") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def create_qr_checkin_token(booking_id: uuid.UUID, expires_in: timedelta = timedelta(hours=36)) -> str:
    """Token corto para el QR de check-in. No es un JWT de sesión: sólo abre el check-in."""
    token, _ = create_token(
        booking_id,
        TokenType.QR_CHECKIN,
        expires_in=expires_in,
        scope="checkin",
    )
    return token


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")
