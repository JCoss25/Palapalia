"""Reseñas: crear (sólo tras evento completado), editar, eliminar, responder, reportar y moderar."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, MyProvider, StaffUser
from app.core.errors import AppError, ErrorCode
from app.models.booking import Booking
from app.models.enums import AuditAction, BookingState, ReportReason, ReviewStatus
from app.models.provider import ProviderProfile
from app.models.review import Review, ReviewReply, ReviewReport
from app.models.venue import Venue
from app.schemas.common import ErrorResponse, Paginated
from app.schemas.content import (
    RatingSummaryOut,
    ReviewCreateRequest,
    ReviewModerateRequest,
    ReviewOut,
    ReviewReplyCreate,
    ReviewReplyOut,
    ReviewReportCreate,
)
from app.services import audit, moderation

router = APIRouter()

REVIEW_WINDOW_DAYS = 30


def _out(review: Review, *, viewer_id: uuid.UUID | None = None) -> ReviewOut:
    reply = review.reply
    return ReviewOut(
        id=review.id,
        venue_id=review.venue_id,
        booking_id=review.booking_id,
        rating=review.rating,
        title=review.title,
        body=review.body,
        subratings=review.subratings or {},
        status=review.status,
        is_verified_booking=review.is_verified_booking,
        helpful_count=review.helpful_count,
        created_at=review.created_at,
        author_display_name=(
            review.author.display_name or (review.author.full_name or "").split(" ")[0]
            if review.author
            else None
        ),
        author_avatar_url=review.author.avatar_url if review.author else None,
        event_type_slug=review.event_type_slug,
        guest_count=review.guest_count,
        reply=ReviewReplyOut(
            id=reply.id,
            body=reply.body,
            created_at=reply.created_at,
            edit_count=reply.edit_count,
            can_edit=bool(reply.editable_until and reply.editable_until > datetime.now(UTC)),
        )
        if reply
        else None,
    )


@router.post(
    "/reviews",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reseñar un evento completado",
    responses={
        403: {"model": ErrorResponse, "description": "REVIEW_NOT_ALLOWED"},
        409: {"model": ErrorResponse, "description": "REVIEW_ALREADY_EXISTS"},
        410: {"model": ErrorResponse, "description": "REVIEW_WINDOW_CLOSED"},
    },
)
async def create_review(
    payload: ReviewCreateRequest, session: DbSession, user: CurrentUser, request: Request
) -> ReviewOut:
    """Sólo la persona que hizo la reserva, con evento completado y dentro de 30 días (edge case C17)."""
    booking = await session.scalar(select(Booking).where(Booking.id == payload.booking_id))
    if booking is None or booking.deleted_at is not None:
        raise AppError(ErrorCode.BOOKING_NOT_FOUND, details={"booking_id": str(payload.booking_id)})
    if booking.client_id != user.id:
        raise AppError(
            ErrorCode.REVIEW_NOT_ALLOWED,
            message="Sólo puedes reseñar espacios donde tú hiciste la reserva.",
        )
    if BookingState(booking.state) != BookingState.COMPLETED:
        raise AppError(
            ErrorCode.REVIEW_NOT_ALLOWED,
            message="Podrás reseñar cuando el evento haya terminado.",
            details={"state": booking.state, "end_at": booking.end_at.isoformat()},
        )
    if booking.end_at + timedelta(days=REVIEW_WINDOW_DAYS) < datetime.now(UTC):
        raise AppError(
            ErrorCode.REVIEW_WINDOW_CLOSED,
            details={"window_days": REVIEW_WINDOW_DAYS},
        )
    existing = await session.scalar(select(Review).where(Review.booking_id == booking.id))
    if existing is not None:
        raise AppError(
            ErrorCode.REVIEW_ALREADY_EXISTS,
            details={"review_id": str(existing.id), "hint": "Usa PATCH /reviews/{id} para editarla."},
        )

    recent = await session.scalar(
        select(func.count())
        .select_from(Review)
        .where(Review.author_id == user.id, Review.created_at >= datetime.now(UTC) - timedelta(hours=24))
    )
    same_ip = await session.scalar(
        select(func.count())
        .select_from(Review)
        .where(Review.created_at >= datetime.now(UTC) - timedelta(hours=24))
    )
    venue = await session.scalar(select(Venue).where(Venue.id == booking.venue_id))
    risk = moderation.review_risk(
        rating=payload.rating,
        body=payload.body,
        author_reviews_last_24h=int(recent or 0),
        author_account_age_days=(datetime.now(UTC) - user.created_at).days,
        same_ip_reviews=int(same_ip or 0),
        venue_rating_avg=float(venue.rating_avg) if venue and venue.rating_avg else None,
    )

    review = Review(
        venue_id=booking.venue_id,
        provider_id=booking.provider_id,
        booking_id=booking.id,
        author_id=user.id,
        rating=payload.rating,
        title=payload.title,
        body=payload.body,
        subratings=payload.subratings,
        event_type_slug=booking.event_type_slug,
        guest_count=payload.guest_count or booking.guests,
        status=(ReviewStatus.PENDING.value if risk.requires_human else ReviewStatus.PUBLISHED.value),
        moderation_flags={"flags": risk.flags, "score": risk.score},
        editable_until=datetime.now(UTC) + timedelta(days=7),
    )
    session.add(review)
    await session.flush()

    await _recalculate_venue_rating(session, review.venue_id, review.provider_id)
    await audit.record(
        session,
        action=AuditAction.REVIEW_MODERATED,
        entity="review",
        entity_id=review.id,
        actor_id=user.id,
        after={"rating": payload.rating, "status": review.status, "flags": risk.flags},
        request=request,
    )
    from app.tasks.jobs import on_review_created

    on_review_created.delay(str(review.id))
    await session.commit()
    return _out(review, viewer_id=user.id)


async def _recalculate_venue_rating(session: DbSession, venue_id: uuid.UUID, provider_id: uuid.UUID) -> None:
    """Promedios desnormalizados. Se recalculan por job, nunca a mano."""
    for entity, column, identifier in (
        (Venue, Venue.rating_avg, venue_id),
        (ProviderProfile, ProviderProfile.rating_avg, provider_id),
    ):
        row = await session.execute(
            select(func.avg(Review.rating), func.count())
            .where(
                Review.status == ReviewStatus.PUBLISHED.value,
                (Review.venue_id == identifier) if entity is Venue else (Review.provider_id == identifier),
            )
        )
        average, count = row.one()
        target = await session.get(entity, identifier)
        if target is not None:
            target.rating_avg = round(float(average), 2) if average else None
            target.rating_count = int(count or 0)
    await session.flush()


@router.patch("/reviews/{review_id}", response_model=ReviewOut, summary="Editar mi reseña")
async def patch_review(
    review_id: uuid.UUID, payload: ReviewCreateRequest, session: DbSession, user: CurrentUser
) -> ReviewOut:
    review = await session.scalar(select(Review).where(Review.id == review_id))
    if review is None or review.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, details={"review_id": str(review_id)})
    if review.author_id != user.id:
        raise AppError(ErrorCode.FORBIDDEN)
    if review.editable_until and review.editable_until < datetime.now(UTC):
        raise AppError(
            ErrorCode.REVIEW_WINDOW_CLOSED,
            message="Ya no puedes editar esta reseña (pasaron los 7 días).",
        )
    review.rating = payload.rating
    review.title = payload.title
    review.body = payload.body
    review.subratings = payload.subratings
    await _recalculate_venue_rating(session, review.venue_id, review.provider_id)
    await session.commit()
    return _out(review, viewer_id=user.id)


@router.delete(
    "/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar mi reseña (soft)"
)
async def delete_review(review_id: uuid.UUID, session: DbSession, user: CurrentUser) -> Response:
    review = await session.scalar(select(Review).where(Review.id == review_id))
    if review is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if review.author_id != user.id:
        raise AppError(ErrorCode.FORBIDDEN)
    review.deleted_at = datetime.now(UTC)
    review.deleted_by_id = user.id
    review.status = ReviewStatus.HIDDEN.value
    await _recalculate_venue_rating(session, review.venue_id, review.provider_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/reviews/{review_id}/reply",
    response_model=ReviewReplyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Responder una reseña (proveedor)",
)
async def reply_review(
    review_id: uuid.UUID, payload: ReviewReplyCreate, session: DbSession, profile: MyProvider, user: CurrentUser
) -> ReviewReplyOut:
    """Una réplica por reseña; editable 72 h. Sin insultos ni datos personales del cliente."""
    review = await session.scalar(select(Review).where(Review.id == review_id))
    if review is None or review.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, details={"review_id": str(review_id)})
    if review.provider_id != profile.id:
        raise AppError(ErrorCode.FORBIDDEN, message="Esa reseña es de otro anfitrión.")

    verdict = moderation.detect_contact(payload.body)
    if verdict.entities.get("PHONE") or verdict.entities.get("EMAIL"):
        raise AppError(
            ErrorCode.MESSAGE_BLOCKED_BY_MODERATION,
            message="No incluyas teléfonos ni correos en la respuesta pública. Los datos de contacto se comparten por mensajería.",
            details={"fields": list(verdict.entities.keys())},
        )

    existing = await session.scalar(select(ReviewReply).where(ReviewReply.review_id == review.id))
    if existing is not None:
        if existing.editable_until and existing.editable_until < datetime.now(UTC):
            raise AppError(
                ErrorCode.CONFLICT,
                message="La respuesta ya quedó publicada y no se puede editar.",
            )
        existing.body = payload.body
        existing.edit_count += 1
        await session.commit()
        return ReviewReplyOut(
            id=existing.id, body=existing.body, created_at=existing.created_at, edit_count=existing.edit_count
        )

    reply = ReviewReply(
        review_id=review.id,
        provider_id=profile.id,
        author_id=user.id,
        body=payload.body,
        editable_until=datetime.now(UTC) + timedelta(hours=72),
    )
    session.add(reply)
    await audit.record(
        session,
        action=AuditAction.REVIEW_REPLY_CREATED,
        entity="review",
        entity_id=review.id,
        actor_id=user.id,
    )
    await session.commit()
    return ReviewReplyOut(id=reply.id, body=reply.body, created_at=reply.created_at, can_edit=True)


@router.post(
    "/reviews/{review_id}/report",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reportar una reseña",
)
async def report_review(
    review_id: uuid.UUID, payload: ReviewReportCreate, session: DbSession, user: CurrentUser
) -> dict:
    """Reportar no oculta la reseña: pasa a revisión humana (edge case C17)."""
    review = await session.scalar(select(Review).where(Review.id == review_id))
    if review is None:
        raise AppError(ErrorCode.NOT_FOUND)
    existing = await session.scalar(
        select(ReviewReport).where(
            ReviewReport.review_id == review_id, ReviewReport.reporter_id == user.id
        )
    )
    if existing is not None:
        return {"report_id": str(existing.id), "status": existing.status, "duplicated": True}

    report = ReviewReport(
        review_id=review.id,
        reporter_id=user.id,
        reason=ReportReason(payload.reason),
        detail=payload.detail,
    )
    session.add(report)
    review.reported_count += 1
    if review.reported_count >= 3 and payload.reason in {ReportReason.EXTORTION, ReportReason.PERSONAL_DATA}:
        review.status = ReviewStatus.PENDING.value
    await session.commit()
    return {"report_id": str(report.id), "status": report.status, "duplicated": False}


@router.get(
    "/venues/{venue_id}/reviews",
    response_model=Paginated[ReviewOut],
    summary="Reseñas públicas de un espacio",
)
async def list_venue_reviews(
    venue_id: uuid.UUID,
    session: DbSession,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,
    rating: int | None = Query(None, ge=1, le=5),
) -> Paginated[ReviewOut]:
    stmt = (
        select(Review)
        .where(
            Review.venue_id == venue_id,
            Review.deleted_at.is_(None),
            Review.status == ReviewStatus.PUBLISHED.value,
        )
        .order_by(Review.created_at.desc())
        .limit(limit)
    )
    if rating:
        stmt = stmt.where(Review.rating == rating)
    rows = (await session.scalars(stmt)).all()
    return Paginated[ReviewOut](
        items=[_out(row) for row in rows],
        meta={"limit": limit, "has_more": len(rows) == limit, "next_cursor": None},
    )


@router.get(
    "/venues/{venue_id}/rating-summary",
    response_model=RatingSummaryOut,
    summary="Resumen de calificaciones del espacio",
)
async def venue_rating_summary(session: DbSession, venue_id: uuid.UUID) -> RatingSummaryOut:
    rows = (
        await session.execute(
            select(Review.rating, func.count())
            .where(Review.venue_id == venue_id, Review.status == ReviewStatus.PUBLISHED.value)
            .group_by(Review.rating)
        )
    ).all()
    distribution = {str(star): 0 for star in range(1, 6)}
    total = weighted = 0
    for rating_value, count in rows:
        distribution[str(rating_value)] = int(count)
        total += int(count)
        weighted += int(rating_value) * int(count)
    sub = await session.execute(
        select(func.count(), func.avg(Review.rating)).where(
            Review.venue_id == venue_id, Review.status == ReviewStatus.PUBLISHED.value
        )
    )
    _, average = sub.one()
    return RatingSummaryOut(
        venue_id=venue_id,
        rating_avg=round(float(average), 2) if average else None,
        rating_count=total,
        distribution=distribution,
    )


admin_router = APIRouter()


@admin_router.patch("/{review_id}", summary="Moderar reseña (staff)")
async def moderate_review(
    review_id: uuid.UUID,
    payload: ReviewModerateRequest,
    session: DbSession,
    staff: StaffUser,
    request: Request,
) -> dict:
    """Ocultar una reseña exige motivo y queda auditado: la moderación no es un borrado silencioso."""
    review = await session.scalar(select(Review).where(Review.id == review_id))
    if review is None:
        raise AppError(ErrorCode.NOT_FOUND, details={"review_id": str(review_id)})
    before = review.status
    review.status = payload.status.value
    await _recalculate_venue_rating(session, review.venue_id, review.provider_id)
    await audit.record(
        session,
        action=AuditAction.REVIEW_MODERATED,
        entity="review",
        entity_id=review.id,
        actor_id=staff.id,
        actor_role=staff.role.value,
        before={"status": before},
        after={"status": review.status},
        reason=payload.reason,
        request=request,
    )
    await session.commit()
    return {"review_id": str(review.id), "status": review.status, "reason": payload.reason}
