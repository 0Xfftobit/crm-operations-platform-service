"""Application settings.

pydantic-settings; every field binds to an UPPER_SNAKE env var via ``Field(alias=...)`` and is
read from the environment or a local ``.env``. Routes/services depend on ``get_settings()``
(cached). Keep this the single source of runtime configuration.
"""
import json
import os
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Lists we accept as a comma-separated string OR JSON from .env. NoDecode stops
# pydantic-settings from JSON-decoding them first, so `_split_list` handles both forms.
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = Field("crequity-followup-sequence", alias="APP_NAME")
    env: str = Field("local", alias="ENV")
    debug: bool = Field(True, alias="DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_json: bool = Field(False, alias="LOG_JSON")
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")
    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS"
    )

    # ── Distributed tracing (OpenTelemetry — optional) ─────────────────────
    tracing_enabled: bool = Field(False, alias="TRACING_ENABLED")
    otel_exporter_otlp_endpoint: str = Field("", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field("followup-sequence", alias="OTEL_SERVICE_NAME")

    # ── Auth (write/ops endpoints, X-API-Key) ──────────────────────────────
    api_key: str = Field("change-me-dev-key", alias="API_KEY")

    # ── Persistence ────────────────────────────────────────────────────────
    # memory (default, volatile) | postgres (durable, needs DATABASE_URL + '.[postgres]').
    storage_gateway: str = Field("memory", alias="STORAGE_GATEWAY")
    database_url: str = Field("", alias="DATABASE_URL")
    db_auto_create: bool = Field(False, alias="DB_AUTO_CREATE")

    # ── Hot cache (shared, ephemeral) ──────────────────────────────────────
    # memory (default, in-process) | redis (shared across replicas + native TTL).
    cache_gateway: str = Field("memory", alias="CACHE_GATEWAY")
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # ── Upstream: the trigger / interaction event source ───────────────────
    # local (no upstream) | http (poll REST) | kafka (subscribe the trigger stream).
    event_source_gateway: str = Field("local", alias="EVENT_SOURCE_GATEWAY")
    sequence_source_url: str = Field("", alias="SEQUENCE_SOURCE_URL")
    sequence_source_timeout: float = Field(20.0, alias="SEQUENCE_SOURCE_TIMEOUT")
    kafka_bootstrap_servers: str = Field("localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_topic: str = Field("followup-events", alias="KAFKA_TOPIC")   # inbound (subscribed)

    # ── Cross-agent compliance precheck (CMS) ──────────────────────────────
    precheck_enabled: bool = Field(False, alias="FUS_PRECHECK_ENABLED")
    compliance_monitor_url: str = Field("", alias="COMPLIANCE_MONITOR_URL")
    compliance_api_key: str = Field("", alias="COMPLIANCE_API_KEY")
    precheck_timeout: float = Field(3.0, alias="FUS_PRECHECK_TIMEOUT")

    # ── Notifications (FR-016: LO/owner reply notification) ────────────────
    notification_service_url: str = Field("", alias="NOTIFICATION_SERVICE_URL")
    notification_api_key: str = Field("", alias="NOTIFICATION_API_KEY")
    integration_timeout_s: float = Field(5.0, alias="INTEGRATION_TIMEOUT_S")

    # ── Real channel provider credentials (optional — falls back to mock if unset) ──
    twilio_account_sid: str = Field("", alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field("", alias="TWILIO_AUTH_TOKEN")
    twilio_from_number: str = Field("", alias="TWILIO_FROM_NUMBER")
    sendgrid_api_key: str = Field("", alias="SENDGRID_API_KEY")
    sendgrid_from_email: str = Field("", alias="SENDGRID_FROM_EMAIL")
    whatsapp_api_url: str = Field("", alias="WHATSAPP_BUSINESS_API_URL")
    whatsapp_api_token: str = Field("", alias="WHATSAPP_BUSINESS_API_TOKEN")

    # ── Cadence / compliance parameters (System Design §7) ─────────────────
    quiet_hours_start: int = Field(21, alias="QUIET_HOURS_START")      # TCPA quiet hours begin
    quiet_hours_end: int = Field(8, alias="QUIET_HOURS_END")           # allowed = [end, start)
    freq_cap_day: int = Field(2, alias="FREQ_CAP_DAY")                 # max msgs/day, all cadences
    freq_cap_week: int = Field(5, alias="FREQ_CAP_WEEK")               # max msgs/week
    consent_cache_ttl_s: int = Field(60, alias="CONSENT_CACHE_TTL_S")  # opt-out propagation bound
    manual_outreach_cooldown_h: int = Field(24, alias="MANUAL_OUTREACH_COOLDOWN_H")   # LO cooldown
    provider_retry_max: int = Field(4, alias="PROVIDER_RETRY_MAX")     # channel retry attempts
    scheduler_interval_s: int = Field(0, alias="SCHEDULER_INTERVAL_S")  # 0 = off (tick via API)
    default_timezone: str = Field("America/New_York", alias="DEFAULT_TIMEZONE")   # tz fallback
    lead_score_threshold: float = Field(70.0, alias="LEAD_SCORE_THRESHOLD")   # auto-enroll trigger

    @model_validator(mode="after")
    def _reject_default_key_in_prod(self) -> "Settings":
        env = os.environ.get("ENV", self.env).lower()
        allowed = {"local", "dev", "development", "test"}
        if self.api_key == "change-me-dev-key" and env not in allowed:
            raise ValueError("Default dev API key is not allowed in non-local environments")
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_list(cls, v: object) -> object:
        """Accept a JSON array OR a comma-separated string for list env vars."""
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    @property
    def uses_sql(self) -> bool:
        """True when a durable SQL backend is configured."""
        return self.storage_gateway == "postgres" and bool(self.database_url)

    @property
    def uses_cache(self) -> bool:
        """True when the Redis hot-cache tier is enabled."""
        return self.cache_gateway == "redis"


@lru_cache
def get_settings() -> Settings:
    return Settings()
