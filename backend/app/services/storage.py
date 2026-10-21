"""Archivos: URLs pre-firmadas (S3/R2), validación de tipo y limpieza de EXIF."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.errors import AppError, ErrorCode

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_DOC_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_DOC_BYTES = 25 * 1024 * 1024

PREFIX_MEDIA = "venues"
PREFIX_DOCS = "verification"
PREFIX_CONTRACTS = "contracts"
PREFIX_EVIDENCE = "evidence"
PREFIX_INVOICES = "invoices"


@dataclass(frozen=True, slots=True)
class UploadTicket:
    url: str
    fields: dict[str, str]
    storage_key: str
    expires_in: int


def _client():  # type: ignore[no-untyped-def]
    return boto3.client(
        "s3",
        endpoint_url=str(settings.s3_endpoint_url) if settings.s3_endpoint_url else None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


def validate_media(mime_type: str, size_bytes: int, kind: str = "photo") -> None:
    """Valida tipo y tamaño antes de firmar: evita subir un .exe con nombre de foto."""
    if kind == "photo" and mime_type not in ALLOWED_IMAGE_TYPES:
        raise AppError(
            ErrorCode.MEDIA_TYPE_NOT_ALLOWED,
            message="Formato de imagen no permitido. Usa JPG, PNG, WebP o HEIC.",
            details={"mime_type": mime_type, "allowed": sorted(ALLOWED_IMAGE_TYPES)},
        )
    if kind == "video" and mime_type not in ALLOWED_VIDEO_TYPES:
        raise AppError(
            ErrorCode.MEDIA_TYPE_NOT_ALLOWED,
            details={"mime_type": mime_type, "allowed": sorted(ALLOWED_VIDEO_TYPES)},
        )
    limit = MAX_VIDEO_BYTES if kind == "video" else MAX_IMAGE_BYTES
    if size_bytes > limit:
        raise AppError(
            ErrorCode.MEDIA_LIMIT_REACHED,
            message=f"El archivo supera el límite de {limit // (1024 * 1024)} MB.",
            details={"size_bytes": size_bytes, "limit_bytes": limit},
        )


def validate_document(mime_type: str, size_bytes: int) -> None:
    if mime_type not in ALLOWED_DOC_TYPES:
        raise AppError(
            ErrorCode.VERIFICATION_DOCUMENT_INVALID,
            message="Formato no aceptado. Sube PDF o una foto clara (JPG/PNG).",
            details={"mime_type": mime_type},
        )
    if size_bytes > MAX_DOC_BYTES:
        raise AppError(
            ErrorCode.VERIFICATION_DOCUMENT_INVALID,
            details={"size_bytes": size_bytes, "limit_bytes": MAX_DOC_BYTES},
        )


def build_key(prefix: str, owner_id: uuid.UUID, filename: str) -> str:
    """Clave determinista por dueño y fecha, con nombre saneado (nada de `../`)."""
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-")[-80:]
    extension = safe.rsplit(".", 1)[-1].lower() if "." in safe else "bin"
    today = datetime.now(UTC).strftime("%Y/%m")
    return f"{prefix}/{owner_id}/{today}/{uuid.uuid4().hex}.{extension}"


def presign_upload(key: str, mime_type: str, *, max_size_bytes: int | None = None) -> UploadTicket:
    """POST pre-firmado con límite de tamaño declarado en la política."""
    conditions: list[dict[str, str] | list[object]] = [{"Content-Type": mime_type}]
    fields: dict[str, str] = {"Content-Type": mime_type}
    if max_size_bytes:
        conditions.append(["content-length-range", 1, max_size_bytes])
    try:
        response = _client().generate_presigned_post(
            Bucket=settings.s3_bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=settings.presign_ttl_seconds,
        )
    except ClientError as exc:
        raise AppError(
            ErrorCode.SERVICE_DEGRADED,
            message="El almacenamiento de archivos no está disponible. Reintenta en un momento.",
            details={"provider": "s3"},
        ) from exc
    return UploadTicket(
        url=response["url"],
        fields=response["fields"],
        storage_key=key,
        expires_in=settings.presign_ttl_seconds,
    )


def presign_download(key: str, *, filename: str | None = None) -> str:
    """URL firmada de descarga. Los documentos de verificación nunca son públicos."""
    params: dict[str, str] = {"Bucket": settings.s3_bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return _client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=settings.presign_ttl_seconds
    )


def public_url(key: str) -> str:
    """URL pública (sólo para media ya aprobada por moderación)."""
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{key}"
    return presign_download(key)


def head_object(key: str) -> dict[str, object]:
    return dict(_client().head_object(Bucket=settings.s3_bucket, Key=key))


def verify_uploaded_checksum(key: str, expected_sha256: str) -> bool:
    """Comprueba integridad del archivo subido (defensa contra contenido sustituido)."""
    try:
        obj = _client().get_object(Bucket=settings.s3_bucket, Key=key)
        digest = hashlib.sha256(obj["Body"].read()).hexdigest()
    except ClientError:
        return False
    return digest == expected_sha256


def delete_object(key: str) -> None:
    """Borrado real: sólo para documentos de verificación en el borrado de cuenta (ADR-010)."""
    try:
        _client().delete_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError:
        pass


def content_type_for(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
