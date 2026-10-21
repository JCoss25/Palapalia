"""Pruebas del filtro anti-bypass y de las señales de fraude (unitarias)."""

from __future__ import annotations

import pytest

from app.services import moderation


@pytest.mark.parametrize(
    ("text", "expected_entity"),
    [
        ("mándame whatsapp al 9991234567", "PHONE"),
        ("mi correo es ruben@palapaxcambo.mx", "EMAIL"),
        ("te paso mi insta @palapa.xcambo", "SOCIAL"),
        ("checa https://miespacio.com/precios", "URL"),
        ("cinco cinco uno dos tres cuatro cinco seis siete ocho nueve cero", "PHONE_SPELLED"),
        ("mejor te hago precio por fuera sin comision", "BYPASS_PHRASE"),
    ],
)
def test_detecta_contacto(text: str, expected_entity: str) -> None:
    verdict = moderation.detect_contact(text)
    assert expected_entity in verdict.entities
    assert verdict.score >= 45
    assert verdict.warning_message is not None


@pytest.mark.parametrize(
    "text",
    [
        "La capacidad es 120 personas y el CP 97430",
        "Somos 60 invitados, el precio son 8400 pesos",
        "Montaje de 14:00 a 15:00, 2 horas",
        "¿El asador está incluido en el precio?",
        "Nos casamos el 24 de octubre, 90 personas",
    ],
)
def test_no_marca_falsos_positivos(text: str) -> None:
    """Códigos postales, capacidades y horas se parecen a teléfonos: no deben marcarse (C19)."""
    verdict = moderation.detect_contact(text)
    assert verdict.score < 45, f"falso positivo en: {text} → {verdict.entities}"


def test_accion_escalona_y_no_bloquea_sola() -> None:
    """Nunca `BLOCK` automático: la decisión humana define el bloqueo (edge case C19)."""
    low = moderation.detect_contact("nos vemos el sábado a las 15:00")
    medium = moderation.detect_contact("te dejo mi correo ruben@palapa.mx")
    high = moderation.detect_contact("llámame al 9991234567 o escríbeme a ruben@palapa.mx, mi insta @palapa")
    assert low.action == "NONE"
    assert medium.action in {"WARN", "REVIEW"}
    assert high.action == "REVIEW"
    assert high.score == 100.0


def test_bandera_guarda_hash_no_el_dato() -> None:
    """La tabla de banderas no es una libreta de teléfonos: se guarda el hash."""
    phone = "9991234567"
    digest = moderation.entity_hash(phone)
    assert phone not in digest
    assert len(digest) == 32
    assert moderation.entity_hash("+52 999 123 4567") == digest  # normaliza dígitos


def test_riesgo_de_resena_detecta_rafaga_y_extorsion() -> None:
    risk = moderation.review_risk(
        rating=1,
        body="pésimo, o me devuelves el dinero o publico esto en todos lados",
        author_reviews_last_24h=4,
        author_account_age_days=1,
        same_ip_reviews=3,
        venue_rating_avg=4.9,
    )
    assert risk.requires_human is True
    assert "burst_publishing" in risk.flags
    assert "new_account" in risk.flags
    assert "same_ip_cluster" in risk.flags
    assert "extortion_language" in risk.flags


def test_resena_normal_no_requiere_humano() -> None:
    risk = moderation.review_risk(
        rating=5,
        body="La palapa estaba impecable y el anfitrión nos ayudó con el montaje.",
        author_reviews_last_24h=1,
        author_account_age_days=400,
        same_ip_reviews=0,
        venue_rating_avg=4.8,
    )
    assert risk.requires_human is False
    assert risk.flags == []


def test_documento_duplicado_entre_cuentas_dispara_revision() -> None:
    risk = moderation.document_risk(
        duplicate_hash_users=2,
        mime_type="image/jpeg",
        size_bytes=20_000,
        metadata_present=False,
        submitted_in_burst=True,
    )
    assert risk.requires_human is True
    assert "same_file_multiple_accounts" in risk.flags


def test_penalizacion_de_confianza_tiene_tope() -> None:
    penalty = moderation.trust_penalty(
        ["false_amenity", "wrong_address", "copyright_photo", "capacity_mismatch", "duplicate_venue"]
    )
    assert penalty <= 30
    assert moderation.trust_penalty([]) == 0
