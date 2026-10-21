"""Esquemas de autenticación, identidad y sesión."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.enums import Role, UserStatus
from app.schemas.common import ORMModel, PhoneE164


class RegisterRequest(BaseModel):
    role: Literal["client", "provider"] = "client"
    email: EmailStr
    password: str = Field(min_length=8, max_length=72, examples=["Palapa2026!"])
    full_name: str = Field(min_length=3, max_length=160)
    phone: PhoneE164 | None = None
    tos_version: str = Field(description="Versión de T&C aceptada", examples=["2026-01-15"])
    accept_tos: bool = Field(description="Debe ser true; el servidor no asume consentimiento")
    marketing_opt_in: bool = False
    #: Proveedor: datos mínimos para abrir el expediente
    business_name: str | None = Field(default=None, max_length=160)
    birth_date: datetime | None = Field(
        default=None,
        description="Requerido para validar mayoría de edad (edge case C18); se descarta tras validar",
    )
    referral_code: str | None = Field(default=None, max_length=40)

    @field_validator("accept_tos")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Debes aceptar los términos y el aviso de privacidad")
        return value

    @model_validator(mode="after")
    def _provider_requires_business(self) -> RegisterRequest:
        if self.role == "provider" and not self.business_name:
            raise ValueError("business_name es obligatorio para registrarse como proveedor")
        return self


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Segundos de vida del access token")
    user: UserOut
    requires_email_verification: bool = False
    requires_phone_verification: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    all_devices: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20)
    new_password: str = Field(min_length=8, max_length=72)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20)


class RequestOtpRequest(BaseModel):
    phone: PhoneE164 | None = None
    email: EmailStr | None = None
    purpose: Literal["phone_verify", "login", "reset", "booking_confirm"] = "phone_verify"

    @model_validator(mode="after")
    def _one_destination(self) -> RequestOtpRequest:
        if not self.phone and not self.email:
            raise ValueError("Indica teléfono o correo")
        return self


class VerifyOtpRequest(BaseModel):
    phone: PhoneE164 | None = None
    email: EmailStr | None = None
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d{4,8}$")
    purpose: Literal["phone_verify", "login", "reset", "booking_confirm"] = "phone_verify"


class OtpResponse(BaseModel):
    sent: bool = True
    destination_masked: str = Field(examples=["+52*****5678"])
    expires_in_seconds: int
    resend_available_in_seconds: int


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    role: Role
    status: UserStatus
    full_name: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    phone_e164: str | None = None
    email_verified_at: datetime | None = None
    phone_verified_at: datetime | None = None
    locale: str = "es-MX"
    timezone: str = "America/Mexico_City"
    created_at: datetime
    has_provider_profile: bool = False


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=3, max_length=160)
    display_name: str | None = Field(default=None, max_length=80)
    avatar_url: str | None = Field(default=None, max_length=512)
    phone: PhoneE164 | None = None
    locale: str | None = Field(default=None, pattern=r"^[a-z]{2}-[A-Z]{2}$")
    timezone: str | None = Field(default=None, max_length=64)
    marketing_opt_in: bool | None = None


class DeleteAccountRequest(BaseModel):
    password: str | None = None
    reason: str | None = Field(default=None, max_length=300)
    confirm: bool = Field(description="Debe ser true")

    @field_validator("confirm")
    @classmethod
    def _confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Debes confirmar la eliminación de la cuenta")
        return value


class DeleteAccountResponse(BaseModel):
    deleted_at: datetime
    pii_erased_at: datetime | None
    retained_records: list[str] = Field(
        description="Registros conservados por obligación fiscal, ya anonimizados",
        examples=[["invoices", "payments", "payouts"]],
    )
    purge_scheduled_for: datetime


class AdminUserOut(UserOut):
    failed_login_count: int = 0
    banned_at: datetime | None = None
    ban_reason: str | None = None
    pii_erased_at: datetime | None = None
    bookings_as_client: int = 0


class AdminUserPatch(BaseModel):
    status: UserStatus | None = None
    role: Role | None = None
    full_name: str | None = None
    note: str | None = Field(default=None, max_length=500)


class BanUserRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=280)
    notify_user: bool = True
    suspend_venues: bool = True


# `LoginResponse` usa `UserOut` definido más abajo: se reconstruye al final del módulo.
LoginResponse.model_rebuild()
