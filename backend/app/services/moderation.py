"""Moderación: detección de bypass de plataforma y señales de fraude.

Principio (edge case C19): **advertir primero, revisar después, bloquear al final**.
Un mensaje con teléfono no se borra: se marca, se advierte al usuario y se encola revisión.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

# ── Detección de contacto ──────────────────────────────────────────────────────

PHONE_RE: Final = re.compile(
    r"(?:\+?52[\s.-]?)?(?:\(?\d{2,3}\)?[\s.-]?){2,4}\d{2,4}", re.IGNORECASE
)
EMAIL_RE: Final = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
URL_RE: Final = re.compile(r"(?:https?://|www\.)\S+|\b[a-z0-9-]+\.(?:com|mx|net|org|io|co)\b", re.IGNORECASE)
SOCIAL_RE: Final = re.compile(
    r"\b(?:whats|wsp|wa\.me|telegram|instagram|insta|ig|facebook|fb|tiktok|messenger)\b", re.IGNORECASE
)
HANDLE_RE: Final = re.compile(r"(?<!\w)@[a-z0-9._]{3,30}\b", re.IGNORECASE)

#: Palabras que indican intento explícito de sacar la transacción
BYPASS_PHRASES: Final = (
    "por fuera",
    "sin comision",
    "sin comisión",
    "te hago precio",
    "deposito directo",
    "depósito directo",
    "pago en efectivo afuera",
    "evitamos la comision",
    "evitamos la comisión",
    "te paso mi whats",
    "mi numero es",
    "mi número es",
    "transferencia directa",
)

#: Códigos postales y años se parecen a teléfonos: se descartan por contexto
FALSE_POSITIVE_CONTEXT: Final = (
    "cp ",
    "c.p.",
    "código postal",
    "codigo postal",
    "capacidad",
    "invitados",
    "personas",
    "metros",
    "m2",
    "precio",
    "pesos",
    "hora",
    "año",
)

OBFUSCATION_MAP: Final = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)


@dataclass(slots=True)
class ModerationVerdict:
    score: float
    entities: dict[str, list[str]] = field(default_factory=dict)
    action: str = "NONE"  # NONE | WARN | REVIEW | BLOCK
    warning_message: str | None = None

    @property
    def should_warn(self) -> bool:
        return self.action in {"WARN", "REVIEW"}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _has_false_positive_context(text: str, match_start: int) -> bool:
    window = text[max(0, match_start - 30) : match_start].lower()
    return any(token in window for token in FALSE_POSITIVE_CONTEXT)


def detect_contact(text: str) -> ModerationVerdict:
    """Puntaje 0..100 de intento de contacto fuera de plataforma.

    No decide sola: `action` sugiere, y las acciones > REVIEW pasan por revisión humana.
    """
    normalized = _normalize(text)
    deobfuscated = normalized.translate(OBFUSCATION_MAP)
    entities: dict[str, list[str]] = {}
    score = 0.0

    emails = EMAIL_RE.findall(text)
    if emails:
        entities["EMAIL"] = emails
        score += 45

    urls = [u for u in URL_RE.findall(text) if not u.endswith((".jpg", ".png", ".pdf"))]
    if urls:
        entities["URL"] = urls
        score += 40

    socials = SOCIAL_RE.findall(text)
    if socials:
        entities["SOCIAL"] = socials
        score += 30

    handles = HANDLE_RE.findall(text)
    if handles:
        entities["SOCIAL"] = [*entities.get("SOCIAL", []), *handles]
        score += 20

    phones: list[str] = []
    for match in PHONE_RE.finditer(text):
        raw = match.group(0)
        digits = _digits_only(raw)
        if len(digits) < 10 or len(digits) > 13:
            continue
        if _has_false_positive_context(text, match.start()):
            continue
        phones.append(raw.strip())
    if phones:
        entities["PHONE"] = phones
        score += 55

    # Formas ofuscadas ("cinco cinco uno dos...")
    spelled = re.findall(
        r"\b(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)(?:\s+(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)){6,}\b",
        deobfuscated,
    )
    if spelled:
        entities["PHONE_SPELLED"] = spelled[:2]
        score += 50

    if any(phrase in deobfuscated for phrase in BYPASS_PHRASES):
        entities["BYPASS_PHRASE"] = [
            phrase for phrase in BYPASS_PHRASES if phrase in deobfuscated
        ][:3]
        score += 35

    score = min(score, 100.0)
    if score >= 80:
        action = "REVIEW"
    elif score >= 45:
        action = "WARN"
    else:
        action = "NONE"

    warning = None
    if action != "NONE":
        warning = (
            "Compartir teléfonos, correos o redes fuera de la app deja tu reserva sin la "
            "protección de pago, sin contrato y sin soporte si algo sale mal. "
            "Puedes seguir platicando aquí con toda confianza."
        )
    return ModerationVerdict(score=score, entities=entities, action=action, warning_message=warning)


def entity_hash(value: str) -> str:
    """Se guarda el hash del dato detectado, no el dato: la bandera no es una libreta de teléfonos."""
    return hashlib.sha256(_digits_only(value).encode() or value.lower().encode()).hexdigest()[:32]


# ── Reseñas: detección de patrones anómalos ────────────────────────────────────


@dataclass(slots=True)
class ReviewRisk:
    score: float
    flags: list[str] = field(default_factory=list)
    requires_human: bool = False


def review_risk(
    *,
    rating: int,
    body: str | None,
    author_reviews_last_24h: int,
    author_account_age_days: int,
    same_ip_reviews: int,
    venue_rating_avg: float | None,
) -> ReviewRisk:
    """Señales de reseña falsa o extorsiva. Nunca se oculta sola: sólo se marca (edge case C17)."""
    flags: list[str] = []
    score = 0.0
    text = _normalize(body or "")

    if rating == 1 and venue_rating_avg and venue_rating_avg >= 4.5:
        flags.append("low_rating_on_high_rated_venue")
        score += 10
    if author_reviews_last_24h >= 3:
        flags.append("burst_publishing")
        score += 30
    if author_account_age_days < 2:
        flags.append("new_account")
        score += 20
    if same_ip_reviews >= 3:
        flags.append("same_ip_cluster")
        score += 35
    if any(word in text for word in ("reembolso o reseño", "dinero o publico", "te demando", "extorsion")):
        flags.append("extortion_language")
        score += 45
    if len(text) < 20 and rating <= 2:
        flags.append("low_effort_negative")
        score += 15
    if not body and rating <= 2:
        flags.append("no_text_negative")
        score += 10

    score = min(score, 100.0)
    return ReviewRisk(score=score, flags=flags, requires_human=score >= 40)


# ── Documentos: señales de falsificación ──────────────────────────────────────


def document_risk(
    *,
    duplicate_hash_users: int,
    mime_type: str,
    size_bytes: int,
    metadata_present: bool,
    submitted_in_burst: bool,
) -> ReviewRisk:
    """Señales para la revisión manual de documentos (edge case C15)."""
    flags: list[str] = []
    score = 0.0
    if duplicate_hash_users > 1:
        flags.append("same_file_multiple_accounts")
        score += 60
    if mime_type not in {"application/pdf"} and size_bytes < 40_000:
        flags.append("suspiciously_small_image")
        score += 15
    if not metadata_present:
        flags.append("metadata_stripped")
        score += 10
    if submitted_in_burst:
        flags.append("bulk_submission")
        score += 20
    score = min(score, 100.0)
    return ReviewRisk(score=score, flags=flags, requires_human=score >= 40)


def trust_penalty(flags: list[str]) -> Decimal:
    """Impacto en el ranking del venue por reportes verificados (edge case D22)."""
    weights = {
        "false_amenity": Decimal("4.0"),
        "wrong_address": Decimal("3.0"),
        "copyright_photo": Decimal("2.5"),
        "capacity_mismatch": Decimal("2.0"),
        "duplicate_venue": Decimal("5.0"),
        "unresponsive": Decimal("1.5"),
    }
    total = sum((weights.get(flag, Decimal("1.0")) for flag in flags), Decimal("0"))
    return min(total, Decimal("30"))
