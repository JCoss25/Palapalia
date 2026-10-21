"""Pagos: intents, webhook del PSP, reembolsos, payouts, facturas e invoices."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Header, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DbSession, IdempotencyKey, MyProvider
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.booking import Booking
from app.models.enums import AuditAction, BookingState, PaymentKind, PaymentStatus, PayoutStatus, Role
from app.models.payment import Invoice, Payment, Payout
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.payment import (
    InvoiceOut,
    InvoiceRequest,
    PaymentIntentRequest,
    PaymentIntentResponse,
    PaymentOut,
    PayoutOut,
    PayoutReleaseRequest,
    RefundCreateRequest,
    RefundOut,
    WebhookAckOut,
)
from app.services import audit
from app.services import payments as svc
from app.services.idempotency import run_idempotent

router = APIRouter()


@router.post(
    "/payments/intent",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear intento de cobro",
    responses={
        402: {"model": ErrorResponse, "description": "CARD_DECLINED · PAYMENT_FAILED · PAYMENT_REQUIRES_ACTION"},
        409: {"model": ErrorResponse, "description": "PAYMENT_DUPLICATED · PROVIDER_PAYOUTS_NOT_ENABLED"},
    },
)
async def create_intent(
    payload: PaymentIntentRequest,
    session: DbSession,
    user: CurrentUser,
    idempotency_key: IdempotencyKey,
) -> PaymentIntentResponse:
    """El monto lo decide el servidor. El `client_secret` sólo se devuelve al titular de la reserva."""
    endpoint = "POST /payments/intent"

    async def operation() -> tuple[dict, str, str]:
        booking = await session.scalar(select(Booking).where(Booking.id == payload.booking_id))
        if booking is None or booking.deleted_at is not None:
            raise AppError(ErrorCode.BOOKING_NOT_FOUND, details={"booking_id": str(payload.booking_id)})
        if booking.client_id != user.id:
            raise AppError(ErrorCode.FORBIDDEN, message="Esa reserva no es tuya.")

        payment = await svc.create_payment_intent(
            session,
            booking=booking,
            client_id=user.id,
            kind=PaymentKind(payload.kind),
            amount_cents=payload.amount_cents,
            method=payload.method,
            idempotency_key=idempotency_key,
            payment_method_id=payload.payment_method_id,
            return_url=payload.return_url,
        )
        if BookingState(booking.state) == BookingState.HOLD:
            booking.state = BookingState.PENDING_PAYMENT.value
            booking.previous_state = BookingState.HOLD.value

        body = PaymentIntentResponse(
            payment_id=payment.id,
            booking_id=booking.id,
            status=PaymentStatus(payment.status),
            amount_cents=payment.amount_cents,
            currency=payment.currency,
            client_secret=payment.client_secret,
            next_action_url=payment.next_action_url,
            requires_action=payment.requires_action_reason is not None,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        ).model_dump(mode="json")
        return body, "payment", str(payment.id)

    body, replay = await run_idempotent(
        session,
        user_id=user.id,
        endpoint=endpoint,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        status_code=201,
        operation=operation,
    )
    await session.commit()
    return PaymentIntentResponse(**{**body, "idempotent_replay": replay})


@router.post(
    "/payments/webhook",
    response_model=WebhookAckOut,
    summary="Webhook del proveedor de pagos (firma obligatoria)",
    responses={
        400: {"model": ErrorResponse, "description": "WEBHOOK_SIGNATURE_INVALID"},
        200: {"description": "Siempre 200 si el evento se procesó o ya estaba aplicado"},
    },
    include_in_schema=True,
)
async def payment_webhook(
    request: Request,
    session: DbSession,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> WebhookAckOut:
    """Idempotente y tolerante a desorden. Nunca se responde 5xx por un evento ya aplicado."""
    payload = await request.body()
    signature = stripe_signature or x_signature or ""
    if not signature:
        raise AppError(
            ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            message="Falta la cabecera de firma del webhook.",
        )
    outcome = await svc.handle_payment_webhook(session, payload=payload, signature=signature)
    return WebhookAckOut(
        received=True,
        event_id=None,
        duplicated=outcome.duplicated,
        action=outcome.action,  # type: ignore[arg-type]
    )


@router.get("/payments/{payment_id}", response_model=PaymentOut, summary="Detalle de un pago")
async def get_payment(payment_id: uuid.UUID, session: DbSession, user: CurrentUser) -> PaymentOut:
    payment = await session.scalar(select(Payment).where(Payment.id == payment_id))
    if payment is None:
        raise AppError(ErrorCode.PAYMENT_NOT_FOUND, details={"payment_id": str(payment_id)})
    booking = await session.scalar(select(Booking).where(Booking.id == payment.booking_id))
    allowed = (
        payment.client_id == user.id
        or user.role in {Role.ADMIN.value, Role.SUPPORT.value}
        or (booking is not None and booking.provider_id is not None and user.role == Role.PROVIDER.value)
    )
    if not allowed:
        raise AppError(ErrorCode.FORBIDDEN)
    return PaymentOut.model_validate(payment)


@router.get("/me/payments", response_model=Paginated[PaymentOut], summary="Mis pagos")
async def my_payments(
    session: DbSession,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=50),
) -> Paginated[PaymentOut]:
    rows = (
        await session.scalars(
            select(Payment)
            .where(Payment.client_id == user.id, Payment.deleted_at.is_(None))
            .order_by(Payment.created_at.desc())
            .limit(limit)
        )
    ).all()
    return Paginated[PaymentOut](
        items=[PaymentOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.post(
    "/refunds",
    response_model=RefundOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear reembolso",
    responses={
        409: {"model": ErrorResponse, "description": "REFUND_NOT_ALLOWED · REFUND_ALREADY_EXISTS"},
        422: {"model": ErrorResponse, "description": "REFUND_EXCEEDS_PAYMENT"},
    },
)
async def create_refund(
    payload: RefundCreateRequest,
    session: DbSession,
    user: CurrentUser,
    idempotency_key: IdempotencyKey,
) -> RefundOut:
    """Con `apply_policy=false` sólo un ADMIN puede reembolsar por encima de la política."""
    if not payload.apply_policy and user.role not in {Role.ADMIN.value, Role.SUPPORT.value}:
        raise AppError(
            ErrorCode.FORBIDDEN,
            message="Sólo soporte puede reembolsar fuera de la política de cancelación.",
        )
    endpoint = "POST /refunds"

    async def operation() -> tuple[dict, str, str]:
        payment = await session.scalar(select(Payment).where(Payment.id == payload.payment_id))
        if payment is None:
            raise AppError(ErrorCode.PAYMENT_NOT_FOUND, details={"payment_id": str(payload.payment_id)})
        if payment.client_id != user.id and user.role not in {Role.ADMIN.value, Role.SUPPORT.value}:
            raise AppError(ErrorCode.FORBIDDEN)

        amount = payload.amount_cents
        booking = await session.scalar(select(Booking).where(Booking.id == payment.booking_id))
        if payload.apply_policy and booking is not None:
            from app.services import bookings as booking_svc

            tier, _, _ = booking_svc.refund_tier_for(booking)
            base = max(payment.amount_cents - payment.refunded_cents, 0)
            amount = min(base * tier // 100 or base, base)

        refund = await svc.create_refund(
            session,
            payment=payment,
            amount_cents=amount,
            reason=payload.reason,
            initiated_by=user.id,
            notes=payload.notes,
            idempotency_key=idempotency_key,
        )
        if payload.release_coupon and booking is not None and booking.coupon_code:
            from app.models.notification import CouponRedemption

            redemptions = (
                await session.scalars(
                    select(CouponRedemption).where(CouponRedemption.booking_id == booking.id)
                )
            ).all()
            for redemption in redemptions:
                redemption.released_at = datetime.now(UTC)
                redemption.release_reason = payload.reason
        return RefundOut.model_validate(refund).model_dump(mode="json"), "refund", str(refund.id)

    body, _ = await run_idempotent(
        session,
        user_id=user.id,
        endpoint=endpoint,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        status_code=201,
        operation=operation,
    )
    return RefundOut(**body)


@router.get("/refunds/{refund_id}", response_model=RefundOut, summary="Detalle de un reembolso")
async def get_refund(refund_id: uuid.UUID, session: DbSession, user: CurrentUser) -> RefundOut:
    from app.models.payment import Refund

    refund = await session.scalar(select(Refund).where(Refund.id == refund_id))
    if refund is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"refund_id": str(refund_id)})
    payment = await session.scalar(select(Payment).where(Payment.id == refund.payment_id))
    if payment is None or (payment.client_id != user.id and user.role not in {Role.ADMIN.value, Role.SUPPORT.value}):
        raise AppError(ErrorCode.FORBIDDEN)
    return RefundOut.model_validate(refund)


@router.get(
    "/providers/me/payouts",
    response_model=Paginated[PayoutOut],
    summary="Mis liquidaciones (proveedor)",
)
async def my_payouts(
    session: DbSession,
    profile: MyProvider,
    limit: int = Query(20, ge=1, le=50),
) -> Paginated[PayoutOut]:
    rows = (
        await session.scalars(
            select(Payout)
            .where(Payout.provider_id == profile.id)
            .order_by(Payout.period_start.desc())
            .limit(limit)
        )
    ).all()
    return Paginated[PayoutOut](
        items=[PayoutOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.post(
    "/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar factura (CFDI 4.0)",
)
async def request_invoice(
    payload: InvoiceRequest, session: DbSession, user: CurrentUser
) -> InvoiceOut:
    """En MVP se registra la solicitud para timbrado asistido; F2 automatiza con PAC."""
    booking = await session.scalar(select(Booking).where(Booking.id == payload.booking_id))
    if booking is None:
        raise AppError(ErrorCode.BOOKING_NOT_FOUND)
    if booking.client_id != user.id and user.role not in {Role.ADMIN.value, Role.SUPPORT.value}:
        raise AppError(ErrorCode.FORBIDDEN)

    invoice = Invoice(
        booking_id=booking.id,
        kind="CLIENT",
        rfc=payload.rfc.upper(),
        legal_name=payload.legal_name,
        tax_regime=payload.tax_regime,
        cfdi_use=payload.cfdi_use,
        postal_code=payload.postal_code,
        subtotal_cents=booking.subtotal_cents,
        tax_cents=booking.tax_cents,
        total_cents=booking.total_cents,
        currency=booking.currency,
        cfdi_status="PENDING",
        retention_until=datetime.now(UTC) + timedelta(days=365 * settings.data_retention_invoice_years),
    )
    session.add(invoice)
    await audit.record(
        session,
        action=AuditAction.PAYMENT_CREATED,
        entity="invoice",
        entity_id=invoice.id,
        actor_id=user.id,
        after={"rfc": "***", "total_cents": invoice.total_cents},
    )
    await session.commit()
    return InvoiceOut.model_validate(invoice)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut, summary="Descargar factura")
async def get_invoice(invoice_id: uuid.UUID, session: DbSession, user: CurrentUser) -> InvoiceOut:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"invoice_id": str(invoice_id)})
    booking = await session.scalar(select(Booking).where(Booking.id == invoice.booking_id))
    if booking is None or (booking.client_id != user.id and user.role not in {Role.ADMIN.value, Role.SUPPORT.value}):
        raise AppError(ErrorCode.FORBIDDEN)
    from app.services import storage

    out = InvoiceOut.model_validate(invoice)
    return out.model_copy(
        update={
            "pdf_url": storage.presign_download(invoice.pdf_key) if invoice.pdf_key else None,
            "xml_url": storage.presign_download(invoice.xml_key) if invoice.xml_key else None,
        }
    )


admin_router = APIRouter()


@admin_router.get("", response_model=Paginated[PayoutOut], summary="Liquidaciones por liberar (admin)")
async def admin_list_payouts(
    session: DbSession,
    admin: AdminUser,
    status_filter: PayoutStatus | None = Query(default=None, alias="status"),
    limit: int = Query(20, ge=1, le=50),
) -> Paginated[PayoutOut]:
    stmt = select(Payout)
    if status_filter:
        stmt = stmt.where(Payout.status == status_filter.value)
    rows = (await session.scalars(stmt.order_by(Payout.period_start.desc()).limit(limit))).all()
    return Paginated[PayoutOut](
        items=[PayoutOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@admin_router.post(
    "/{payout_id}/release",
    response_model=PayoutOut,
    summary="Liberar liquidación (admin)",
    responses={409: {"model": ErrorResponse, "description": "PAYOUT_FROZEN"}},
)
async def admin_release_payout(
    payout_id: uuid.UUID,
    payload: PayoutReleaseRequest,
    session: DbSession,
    admin: AdminUser,
    request: Request,
) -> PayoutOut:
    payout = await session.scalar(select(Payout).where(Payout.id == payout_id))
    if payout is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"payout_id": str(payout_id)})
    await svc.release_payout(session, payout=payout, released_by=admin.id, force=payload.force)
    await audit.record(
        session,
        action=AuditAction.PAYOUT_RELEASED,
        entity="payout",
        entity_id=payout.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        after={"net_cents": payout.net_cents, "forced": payload.force, "notes": payload.notes},
        request=request,
    )
    await session.commit()
    return PayoutOut.model_validate(payout)
