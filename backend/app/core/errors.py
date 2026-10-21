"""Errores con códigos estables.

El cliente móvil decide UX por `code`, nunca por el texto. Renombrar un código es un
breaking change: hay que versionar y mantener alias.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # Genéricos
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_DEGRADED = "SERVICE_DEGRADED"

    # Auth / identidad
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    PHONE_ALREADY_REGISTERED = "PHONE_ALREADY_REGISTERED"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    PHONE_NOT_VERIFIED = "PHONE_NOT_VERIFIED"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    OTP_TOO_MANY_ATTEMPTS = "OTP_TOO_MANY_ATTEMPTS"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID"
    REFRESH_TOKEN_REUSED = "REFRESH_TOKEN_REUSED"
    PASSWORD_RESET_TOKEN_INVALID = "PASSWORD_RESET_TOKEN_INVALID"
    TOS_NOT_ACCEPTED = "TOS_NOT_ACCEPTED"
    UNDERAGE_NOT_ALLOWED = "UNDERAGE_NOT_ALLOWED"
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"

    # Proveedores
    PROVIDER_NOT_APPROVED = "PROVIDER_NOT_APPROVED"
    PROVIDER_PAYOUTS_NOT_ENABLED = "PROVIDER_PAYOUTS_NOT_ENABLED"
    VERIFICATION_DOCUMENT_INVALID = "VERIFICATION_DOCUMENT_INVALID"
    VERIFICATION_ALREADY_PENDING = "VERIFICATION_ALREADY_PENDING"

    # Venues
    VENUE_NOT_FOUND = "VENUE_NOT_FOUND"
    VENUE_SUSPENDED = "VENUE_SUSPENDED"
    VENUE_NOT_AVAILABLE = "VENUE_NOT_AVAILABLE"
    VENUE_DUPLICATE = "VENUE_DUPLICATE"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    ADDRESS_NOT_GEOCODABLE = "ADDRESS_NOT_GEOCODABLE"
    MEDIA_LIMIT_REACHED = "MEDIA_LIMIT_REACHED"
    MEDIA_TYPE_NOT_ALLOWED = "MEDIA_TYPE_NOT_ALLOWED"

    # Disponibilidad / precios
    AVAILABILITY_RULE_CONFLICT = "AVAILABILITY_RULE_CONFLICT"
    BLACKOUT_OVERLAPS_CONFIRMED_BOOKING = "BLACKOUT_OVERLAPS_CONFIRMED_BOOKING"
    DATE_IN_PAST = "DATE_IN_PAST"
    INVALID_DURATION = "INVALID_DURATION"
    MIN_HOURS_NOT_MET = "MIN_HOURS_NOT_MET"
    GUESTS_OUT_OF_RANGE = "GUESTS_OUT_OF_RANGE"
    NO_PRICING_RULE = "NO_PRICING_RULE"
    TIMEZONE_INVALID = "TIMEZONE_INVALID"
    QUOTE_EXPIRED = "QUOTE_EXPIRED"

    # Reservas
    HOLD_EXPIRED = "HOLD_EXPIRED"
    HOLD_NOT_FOUND = "HOLD_NOT_FOUND"
    HOLD_LIMIT_REACHED = "HOLD_LIMIT_REACHED"
    HOLD_ALREADY_CONVERTED = "HOLD_ALREADY_CONVERTED"
    BOOKING_NOT_FOUND = "BOOKING_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    BOOKING_ALREADY_STARTED = "BOOKING_ALREADY_STARTED"
    RESCHEDULE_TARGET_UNAVAILABLE = "RESCHEDULE_TARGET_UNAVAILABLE"
    CHECK_IN_NOT_ALLOWED_YET = "CHECK_IN_NOT_ALLOWED_YET"
    QR_TOKEN_INVALID = "QR_TOKEN_INVALID"
    CONTRACT_NOT_READY = "CONTRACT_NOT_READY"

    # Pagos
    PAYMENT_DUPLICATED = "PAYMENT_DUPLICATED"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_REQUIRES_ACTION = "PAYMENT_REQUIRES_ACTION"
    CARD_DECLINED = "CARD_DECLINED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"
    WEBHOOK_EVENT_DUPLICATED = "WEBHOOK_EVENT_DUPLICATED"
    REFUND_NOT_ALLOWED = "REFUND_NOT_ALLOWED"
    REFUND_EXCEEDS_PAYMENT = "REFUND_EXCEEDS_PAYMENT"
    REFUND_ALREADY_EXISTS = "REFUND_ALREADY_EXISTS"
    PAYOUT_FROZEN = "PAYOUT_FROZEN"

    # Cupones
    COUPON_NOT_FOUND = "COUPON_NOT_FOUND"
    COUPON_EXPIRED = "COUPON_EXPIRED"
    COUPON_EXHAUSTED = "COUPON_EXHAUSTED"
    COUPON_NOT_APPLICABLE = "COUPON_NOT_APPLICABLE"
    COUPON_ALREADY_REDEEMED = "COUPON_ALREADY_REDEEMED"

    # Reseñas / mensajería
    REVIEW_NOT_ALLOWED = "REVIEW_NOT_ALLOWED"
    REVIEW_ALREADY_EXISTS = "REVIEW_ALREADY_EXISTS"
    REVIEW_WINDOW_CLOSED = "REVIEW_WINDOW_CLOSED"
    REVIEW_UNDER_MODERATION = "REVIEW_UNDER_MODERATION"
    THREAD_NOT_FOUND = "THREAD_NOT_FOUND"
    MESSAGE_BLOCKED_BY_MODERATION = "MESSAGE_BLOCKED_BY_MODERATION"
    CONTACT_SHARING_FLAGGED = "CONTACT_SHARING_FLAGGED"

    # Disputas / admin
    DISPUTE_NOT_FOUND = "DISPUTE_NOT_FOUND"
    DISPUTE_ALREADY_OPEN = "DISPUTE_ALREADY_OPEN"
    DISPUTE_DEADLINE_PASSED = "DISPUTE_DEADLINE_PASSED"
    EVIDENCE_LIMIT_REACHED = "EVIDENCE_LIMIT_REACHED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"


# HTTP status por defecto de cada código (permite `raise AppError(ErrorCode.X)` sin status).
DEFAULT_STATUS: dict[str, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_DEGRADED: 503,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.EMAIL_ALREADY_REGISTERED: 409,
    ErrorCode.PHONE_ALREADY_REGISTERED: 409,
    ErrorCode.EMAIL_NOT_VERIFIED: 403,
    ErrorCode.PHONE_NOT_VERIFIED: 403,
    ErrorCode.OTP_INVALID: 400,
    ErrorCode.OTP_EXPIRED: 410,
    ErrorCode.OTP_TOO_MANY_ATTEMPTS: 429,
    ErrorCode.REFRESH_TOKEN_INVALID: 401,
    ErrorCode.REFRESH_TOKEN_REUSED: 401,
    ErrorCode.PASSWORD_RESET_TOKEN_INVALID: 400,
    ErrorCode.TOS_NOT_ACCEPTED: 400,
    ErrorCode.UNDERAGE_NOT_ALLOWED: 403,
    ErrorCode.ACCOUNT_SUSPENDED: 403,
    ErrorCode.PROVIDER_NOT_APPROVED: 403,
    ErrorCode.PROVIDER_PAYOUTS_NOT_ENABLED: 409,
    ErrorCode.VERIFICATION_DOCUMENT_INVALID: 422,
    ErrorCode.VERIFICATION_ALREADY_PENDING: 409,
    ErrorCode.VENUE_NOT_FOUND: 404,
    ErrorCode.VENUE_SUSPENDED: 409,
    ErrorCode.VENUE_NOT_AVAILABLE: 409,
    ErrorCode.VENUE_DUPLICATE: 409,
    ErrorCode.CAPACITY_EXCEEDED: 422,
    ErrorCode.ADDRESS_NOT_GEOCODABLE: 422,
    ErrorCode.MEDIA_LIMIT_REACHED: 409,
    ErrorCode.MEDIA_TYPE_NOT_ALLOWED: 415,
    ErrorCode.AVAILABILITY_RULE_CONFLICT: 422,
    ErrorCode.BLACKOUT_OVERLAPS_CONFIRMED_BOOKING: 409,
    ErrorCode.DATE_IN_PAST: 422,
    ErrorCode.INVALID_DURATION: 422,
    ErrorCode.MIN_HOURS_NOT_MET: 422,
    ErrorCode.GUESTS_OUT_OF_RANGE: 422,
    ErrorCode.NO_PRICING_RULE: 422,
    ErrorCode.TIMEZONE_INVALID: 422,
    ErrorCode.QUOTE_EXPIRED: 409,
    ErrorCode.HOLD_EXPIRED: 410,
    ErrorCode.HOLD_NOT_FOUND: 404,
    ErrorCode.HOLD_LIMIT_REACHED: 429,
    ErrorCode.HOLD_ALREADY_CONVERTED: 409,
    ErrorCode.BOOKING_NOT_FOUND: 404,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.BOOKING_ALREADY_STARTED: 409,
    ErrorCode.RESCHEDULE_TARGET_UNAVAILABLE: 409,
    ErrorCode.CHECK_IN_NOT_ALLOWED_YET: 409,
    ErrorCode.QR_TOKEN_INVALID: 400,
    ErrorCode.CONTRACT_NOT_READY: 409,
    ErrorCode.PAYMENT_DUPLICATED: 409,
    ErrorCode.PAYMENT_NOT_FOUND: 404,
    ErrorCode.PAYMENT_FAILED: 402,
    ErrorCode.PAYMENT_REQUIRES_ACTION: 402,
    ErrorCode.CARD_DECLINED: 402,
    ErrorCode.CURRENCY_MISMATCH: 422,
    ErrorCode.AMOUNT_MISMATCH: 422,
    ErrorCode.WEBHOOK_SIGNATURE_INVALID: 400,
    ErrorCode.WEBHOOK_EVENT_DUPLICATED: 200,
    ErrorCode.REFUND_NOT_ALLOWED: 409,
    ErrorCode.REFUND_EXCEEDS_PAYMENT: 422,
    ErrorCode.REFUND_ALREADY_EXISTS: 409,
    ErrorCode.PAYOUT_FROZEN: 409,
    ErrorCode.COUPON_NOT_FOUND: 404,
    ErrorCode.COUPON_EXPIRED: 410,
    ErrorCode.COUPON_EXHAUSTED: 409,
    ErrorCode.COUPON_NOT_APPLICABLE: 422,
    ErrorCode.COUPON_ALREADY_REDEEMED: 409,
    ErrorCode.REVIEW_NOT_ALLOWED: 403,
    ErrorCode.REVIEW_ALREADY_EXISTS: 409,
    ErrorCode.REVIEW_WINDOW_CLOSED: 410,
    ErrorCode.REVIEW_UNDER_MODERATION: 409,
    ErrorCode.THREAD_NOT_FOUND: 404,
    ErrorCode.MESSAGE_BLOCKED_BY_MODERATION: 422,
    ErrorCode.CONTACT_SHARING_FLAGGED: 202,
    ErrorCode.DISPUTE_NOT_FOUND: 404,
    ErrorCode.DISPUTE_ALREADY_OPEN: 409,
    ErrorCode.DISPUTE_DEADLINE_PASSED: 409,
    ErrorCode.EVIDENCE_LIMIT_REACHED: 409,
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: 400,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: 409,
}

# Mensajes por defecto en es-MX. La UI puede sobreescribir con copy propio,
# pero el texto debe ser accionable (qué hacer, no sólo qué falló).
DEFAULT_MESSAGE: dict[str, str] = {
    ErrorCode.VENUE_NOT_AVAILABLE: "Ese horario acaba de ocuparse. Elige otra fecha u otro espacio.",
    ErrorCode.HOLD_EXPIRED: "Tu apartado venció y el horario se liberó. Vuelve a apartarlo para pagar.",
    ErrorCode.HOLD_LIMIT_REACHED: "Tienes demasiados apartados activos. Completa o cancela uno para continuar.",
    ErrorCode.HOLD_ALREADY_CONVERTED: "Este apartado ya se convirtió en reserva.",
    ErrorCode.PAYMENT_DUPLICATED: "Este pago ya fue registrado; no se cobró dos veces.",
    ErrorCode.CARD_DECLINED: "El banco rechazó la tarjeta. Prueba con otra o usa transferencia SPEI.",
    ErrorCode.REFUND_NOT_ALLOWED: "La política de cancelación de esta fecha ya no permite reembolso.",
    ErrorCode.COUPON_EXHAUSTED: "Este cupón alcanzó su límite de usos.",
    ErrorCode.COUPON_NOT_APPLICABLE: "El cupón no aplica a este espacio o tipo de evento.",
    ErrorCode.REVIEW_NOT_ALLOWED: "Sólo puedes reseñar espacios donde ya realizaste un evento.",
    ErrorCode.REVIEW_WINDOW_CLOSED: "El plazo para reseñar este evento terminó.",
    ErrorCode.CONTACT_SHARING_FLAGGED: "Tu mensaje se envió, pero compartir datos de contacto fuera de la app quita la protección de pago y soporte.",
    ErrorCode.BLACKOUT_OVERLAPS_CONFIRMED_BOOKING: "No puedes cerrar una fecha que ya tiene una reserva confirmada. Cancélala o resuélvelo con soporte.",
    ErrorCode.DATE_IN_PAST: "Esa fecha ya pasó. Elige una fecha futura.",
    ErrorCode.MIN_HOURS_NOT_MET: "Este espacio se renta por bloques; revisa la duración mínima.",
    ErrorCode.PROVIDER_PAYOUTS_NOT_ENABLED: "Completa tu cuenta de pagos para poder recibir dinero.",
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: "Falta el encabezado Idempotency-Key en una operación que lo requiere.",
    ErrorCode.IDEMPOTENCY_KEY_REUSED: "Reutilizaste una llave de idempotencia con datos distintos. Genera una nueva.",
    ErrorCode.SERVICE_DEGRADED: "Un servicio externo está lento. Reintentamos automáticamente; puedes intentar de nuevo en un momento.",
}


class AppError(Exception):
    """Error de dominio. Siempre se traduce al sobre `{"error": {...}}`."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = ErrorCode(code)
        self.message = message or DEFAULT_MESSAGE.get(self.code, "Ocurrió un error inesperado.")
        self.details = details or {}
        self.status_code = status_code or DEFAULT_STATUS.get(self.code, 400)
        self.headers = headers or {}
        super().__init__(f"{self.code}: {self.message}")


# Atajos usados en servicios
def not_found(code: ErrorCode, **details: Any) -> AppError:
    return AppError(code, details=details)


def conflict(code: ErrorCode, **details: Any) -> AppError:
    return AppError(code, details=details)


def invalid(code: ErrorCode, **details: Any) -> AppError:
    return AppError(code, details=details)
