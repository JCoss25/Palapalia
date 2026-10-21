from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración tipada. Un solo lugar; nada de `os.getenv` disperso."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # App
    environment: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    secret_key: str = Field(min_length=32)
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    cors_origins: list[str] = []

    # DB
    database_url: PostgresDsn
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_statement_timeout_ms: int = 8000

    # Redis / Celery
    redis_url: RedisDsn
    celery_broker_url: str
    celery_result_backend: str

    # Pagos
    payments_gateway: Literal["stripe", "mercadopago"] = "stripe"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_connect_country: str = "MX"
    platform_fee_bps: int = 750
    mercadopago_access_token: str = ""

    # Archivos
    s3_endpoint_url: AnyHttpUrl | None = None
    s3_region: str = "auto"
    s3_bucket: str = "quinta-media"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""
    presign_ttl_seconds: int = 900

    # Mapas
    maps_provider: Literal["mapbox", "google"] = "mapbox"
    mapbox_access_token: str = ""
    geocoding_timeout_seconds: float = 4.0

    # Comunicaciones
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "hola@quinta.mx"
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    expo_push_enabled: bool = True

    # Reglas de negocio
    hold_ttl_seconds: int = 900
    hold_max_active_per_user: int = 3
    cleaning_buffer_minutes: int = 60
    cancel_full_refund_days: int = 30
    cancel_half_refund_days: int = 7
    search_max_radius_km: int = 120
    search_default_radius_km: int = 25
    rate_limit_default: str = "120/minute"
    rate_limit_hold: str = "10/minute"
    rate_limit_otp: str = "5/hour"

    # Webhooks
    webhook_max_attempts: int = 5
    webhook_timeout_seconds: float = 8.0
    webhook_signature_tolerance_seconds: int = 300

    # Cumplimiento
    data_retention_invoice_years: int = 5
    audit_retention_days: int = 730
    tos_version: str = "2026-01-15"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def platform_fee_rate(self) -> float:
        return self.platform_fee_bps / 10_000


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
