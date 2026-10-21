"""Cupones: validación pública y CRUD administrativo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.errors import AppError, ErrorCode
from app.models.enums import AuditAction
from app.models.notification import Coupon, CouponRedemption
from app.schemas.common import Paginated
from app.schemas.venue import CouponOut, CouponUpsert, CouponValidateRequest, CouponValidateResponse
from app.services import audit
from app.services import bookings as booking_svc

router = APIRouter()


@router.post(
    "/validate",
    response_model=CouponValidateResponse,
    summary="Validar cupón para una fecha y espacio",
)
async def validate_coupon(
    payload: CouponValidateRequest, session: DbSession, user: CurrentUser
) -> CouponValidateResponse:
    """Devuelve `valid=false` con motivo en vez de 4xx: la UI muestra el porqué sin romper el flujo."""
    venue = await booking_svc.load_venue_for_quote(session, payload.venue_id)
    try:
        quote = await booking_svc.build_venue_quote(
            session,
            venue=venue,
            start_at=payload.start_at,
            end_at=payload.end_at,
            guests=payload.guests,
            extras=[],
            user_id=user.id,
            coupon_code=payload.code,
        )
    except AppError as exc:
        if exc.code.value.startswith("COUPON_"):
            return CouponValidateResponse(
                valid=False,
                code=payload.code.upper(),
                discount_cents=0,
                message=exc.message,
                reason=exc.code.value,
            )
        raise

    discount = quote.coupon.discount_cents if quote.coupon else 0
    return CouponValidateResponse(
        valid=discount > 0,
        code=payload.code.upper(),
        discount_cents=discount,
        message=(
            f"Cupón aplicado: {discount / 100:,.2f} {quote.currency} de descuento."
            if discount
            else "El cupón no genera descuento en esta reserva."
        ),
    )


admin_router = APIRouter()


@admin_router.get("", response_model=Paginated[CouponOut], summary="Listar cupones (admin)")
async def list_coupons(
    session: DbSession,
    admin: AdminUser,
    active_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
) -> Paginated[CouponOut]:
    stmt = select(Coupon).where(Coupon.deleted_at.is_(None)).order_by(Coupon.created_at.desc()).limit(limit)
    if active_only:
        stmt = stmt.where(Coupon.is_active.is_(True))
    rows = (await session.scalars(stmt)).all()
    return Paginated[CouponOut](
        items=[CouponOut.model_validate(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@admin_router.post("", response_model=CouponOut, status_code=status.HTTP_201_CREATED, summary="Crear cupón")
async def create_coupon(
    payload: CouponUpsert, session: DbSession, admin: AdminUser, request: Request
) -> CouponOut:
    code = payload.code.upper()
    existing = await session.scalar(select(Coupon).where(Coupon.code == code))
    if existing is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            message=f"El cupón {code} ya existe.",
            details={"coupon_id": str(existing.id), "hint": "Usa PATCH para editarlo."},
        )
    coupon = Coupon(
        **payload.model_dump(exclude={"code"}),
        code=code,
        created_by_id=admin.id,
    )
    session.add(coupon)
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="coupon",
        entity_id=coupon.id,
        actor_id=admin.id,
        actor_role=admin.role.value,
        after={"code": code, "kind": payload.kind, "value": payload.value},
        request=request,
    )
    await session.commit()
    return CouponOut.model_validate(coupon)


@admin_router.patch("/{coupon_id}", response_model=CouponOut, summary="Editar cupón")
async def update_coupon(
    coupon_id: uuid.UUID, payload: CouponUpsert, session: DbSession, admin: AdminUser, request: Request
) -> CouponOut:
    coupon = await session.get(Coupon, coupon_id)
    if coupon is None or coupon.deleted_at is not None:
        raise AppError(ErrorCode.COUPON_NOT_FOUND, details={"coupon_id": str(coupon_id)})
    before = {"kind": coupon.kind, "value": coupon.value, "is_active": coupon.is_active}
    for field, value in payload.model_dump(exclude={"code"}).items():
        setattr(coupon, field, value)
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="coupon",
        entity_id=coupon.id,
        actor_id=admin.id,
        before=before,
        after={"kind": coupon.kind, "value": coupon.value, "is_active": coupon.is_active},
        request=request,
    )
    await session.commit()
    return CouponOut.model_validate(coupon)


@admin_router.delete(
    "/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Desactivar cupón (soft)"
)
async def delete_coupon(
    coupon_id: uuid.UUID, session: DbSession, admin: AdminUser, request: Request
) -> Response:
    coupon = await session.get(Coupon, coupon_id)
    if coupon is None:
        raise AppError(ErrorCode.COUPON_NOT_FOUND, details={"coupon_id": str(coupon_id)})
    coupon.deleted_at = datetime.now(UTC)
    coupon.is_active = False
    active_redemptions = await session.scalar(
        select(func.count())
        .select_from(CouponRedemption)
        .where(CouponRedemption.coupon_id == coupon.id, CouponRedemption.released_at.is_(None))
    )
    await audit.record(
        session,
        action=AuditAction.VENUE_UPDATED,
        entity="coupon",
        entity_id=coupon.id,
        actor_id=admin.id,
        reason=f"desactivado; {int(active_redemptions or 0)} canjes vigentes conservan su descuento",
        request=request,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
