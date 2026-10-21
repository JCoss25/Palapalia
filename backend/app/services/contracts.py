"""Contrato de apartado en PDF, generado **desde el snapshot** (nunca desde reglas vivas)."""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.models.booking import Booking

TEMPLATE_VERSION = "2026.1"

INK = colors.HexColor("#15201B")
SEA = colors.HexColor("#0E5F52")
LINE = colors.HexColor("#D8D3C6")
BONE = colors.HexColor("#F2EFE7")


def _money(cents: int, currency: str = "MXN") -> str:
    return f"${cents / 100:,.2f} {currency}"


def _local(dt: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    return dt.astimezone(tz).strftime("%d/%m/%Y %H:%M")


def build_contract_pdf(
    booking: Booking,
    *,
    client_name: str,
    provider_name: str,
    venue_name: str,
    venue_address: str,
    client_rfc: str | None = None,
) -> tuple[bytes, str]:
    """Devuelve (pdf_bytes, sha256). El hash se guarda para detectar alteraciones."""
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, textColor=INK, leading=24)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=SEA, spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13.5, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#5D6660"))

    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Contrato de apartado {booking.folio}",
        author="Quinta",
    )
    snapshot: dict[str, Any] = booking.quote_snapshot or {}
    totals = snapshot.get("totals", {})
    tz = booking.venue_timezone

    story: list[Any] = [
        Paragraph("Contrato de apartado de espacio", h1),
        Paragraph(
            f"Folio <b>{booking.folio}</b> · Emitido el {datetime.now(UTC).strftime('%d/%m/%Y %H:%M')} UTC · "
            f"Versión de plantilla {TEMPLATE_VERSION}",
            small,
        ),
        Spacer(1, 10),
        Paragraph("Partes", h2),
    ]

    parties = [
        ["Cliente", client_name, "Anfitrión", provider_name],
        ["Correo", "—", "Espacio", venue_name],
        ["RFC", client_rfc or "No proporcionado", "Domicilio", venue_address],
    ]
    story.append(
        Table(
            parties,
            colWidths=[2.6 * cm, 6.2 * cm, 2.6 * cm, 5.6 * cm],
            style=TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                    ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
                    ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.5),
                    ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
                ]
            ),
        )
    )

    story += [
        Paragraph("Evento", h2),
        Table(
            [
                ["Inicio", _local(booking.start_at, tz), "Fin", _local(booking.end_at, tz)],
                ["Zona horaria", tz, "Invitados", str(booking.guests)],
                [
                    "Bloqueo del calendario",
                    f"{_local(booking.blocked_from, tz)} → {_local(booking.blocked_to, tz)}",
                    "Tipo",
                    booking.slot_kind,
                ],
            ],
            colWidths=[3.4 * cm, 5.4 * cm, 3.4 * cm, 4.8 * cm],
            style=TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                    ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
                    ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.5),
                    ("BACKGROUND", (0, 0), (-1, -1), BONE),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        ),
        Paragraph("Desglose económico congelado", h2),
    ]

    lines = [["Concepto", "Detalle", "Importe"]]
    for line in snapshot.get("lines", []):
        lines.append(
            [
                line.get("concept", ""),
                line.get("detail") or "",
                ("−" if line.get("is_discount") else "") + _money(line.get("amount_cents", 0), booking.currency),
            ]
        )
    lines.append(["Total", "", _money(booking.total_cents, booking.currency)])
    lines.append(["Anticipo pagado", "", _money(booking.paid_cents, booking.currency)])
    lines.append(["Saldo pendiente", "", _money(booking.balance_cents, booking.currency)])
    story.append(
        Table(
            lines,
            colWidths=[5.6 * cm, 6.2 * cm, 5.2 * cm],
            style=TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                    ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
                    ("LINEBELOW", (0, -4), (-1, -4), 0.6, INK),
                    ("FONT", (0, -3), (-1, -3), "Helvetica-Bold", 9.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        )
    )

    story += [
        Paragraph("Política de cancelación", h2),
        Paragraph(
            f"Reembolso del 100% hasta {settings.cancel_full_refund_days} días antes del evento; "
            f"50% entre {settings.cancel_half_refund_days} y {settings.cancel_full_refund_days} días; "
            f"sin reembolso a menos de {settings.cancel_half_refund_days} días. "
            "El depósito de garantía es reembolsable y se libera 48 h después del evento si no hay daños "
            "documentados. Si el anfitrión cancela, el reembolso es total.",
            body,
        ),
        Paragraph("Obligaciones del cliente", h2),
        Paragraph(
            "Usar el espacio para el evento declarado y dentro del horario acordado; respetar el aforo y la "
            "hora límite; responder por daños causados por sus invitados; no subarrendar ni ceder el espacio.",
            body,
        ),
        Paragraph("Obligaciones del anfitrión", h2),
        Paragraph(
            "Entregar el espacio en las condiciones descritas en el anuncio (capacidad, amenidades, servicios "
            "incluidos); permitir el montaje en el horario indicado; mantener los permisos y seguros vigentes; "
            "avisar de inmediato cualquier impedimento.",
            body,
        ),
        Paragraph("Datos personales", h2),
        Paragraph(
            "Las partes aceptan el aviso de privacidad de Quinta. Los datos se tratan conforme a la LFPDPPP; "
            "los registros contables se conservan por el plazo fiscal aplicable, ya anonimizados.",
            small,
        ),
        Spacer(1, 14),
        Paragraph(
            f"Documento generado automáticamente desde el apartado {booking.hold_id or '—'} con la versión de "
            f"motor de precios {snapshot.get('engine_version', 'n/d')}. "
            "La aceptación electrónica al pagar equivale a firma para todos los efectos legales aplicables.",
            small,
        ),
    ]

    doc.build(story)
    pdf = buffer.getvalue()
    return pdf, hashlib.sha256(pdf).hexdigest()


def contract_key(booking_id: uuid.UUID, version: int = 1) -> str:
    return f"contracts/{booking_id}/contrato-v{version}.pdf"
