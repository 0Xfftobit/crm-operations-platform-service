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
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = Field("crequity-compliance-monitoring", alias="APP_NAME")
    env: str = Field("local", alias="ENV")
    debug: bool = Field(True, alias="DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_json: bool = Field(False, alias="LOG_JSON")
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")
    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS"
    )

    # ── Distributed tracing (OpenTelemetry) ────────────────────────────────
    tracing_enabled: bool = Field(False, alias="TRACING_ENABLED")
    otel_exporter_otlp_endpoint: str = Field("", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field("compliance-monitoring", alias="OTEL_SERVICE_NAME")

    # ── Auth (write/ops endpoints, X-API-Key) ──────────────────────────────
    api_key: str = Field("change-me-dev-key", alias="API_KEY")

    # ── Persistence ────────────────────────────────────────────────────────
    # memory (default, volatile) | postgres (durable, needs DATABASE_URL).
    storage_gateway: str = Field("memory", alias="STORAGE_GATEWAY")
    database_url: str = Field("", alias="DATABASE_URL")
    db_auto_create: bool = Field(False, alias="DB_AUTO_CREATE")

    # ── Hot cache (shared, ephemeral) ──────────────────────────────────────
    # memory (default, in-process) | redis (shared across replicas + native TTL).
    cache_gateway: str = Field("memory", alias="CACHE_GATEWAY")
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # ── Upstream: the platform audit / event bus ───────────────────────────
    # local (no upstream) | http (poll REST) | kafka (subscribe the audit stream).
    event_source_gateway: str = Field("local", alias="EVENT_SOURCE_GATEWAY")
    audit_source_url: str = Field("", alias="AUDIT_SOURCE_URL")
    audit_source_timeout: float = Field(20.0, alias="AUDIT_SOURCE_TIMEOUT")
    kafka_bootstrap_servers: str = Field("localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_topic: str = Field("compliance-audit", alias="KAFKA_TOPIC")   # inbound (subscribed)

    # ── Compliance parameters (System Design §7) ───────────────────────────
    precheck_latency_budget_ms: int = Field(80, alias="PRECHECK_LATENCY_BUDGET_MS")
    fairness_ratio_min: float = Field(0.80, alias="FAIRNESS_RATIO_MIN")   # 4/5ths floor
    fairness_ratio_max: float = Field(1.25, alias="FAIRNESS_RATIO_MAX")
    disparity_min_sample: int = Field(100, alias="DISPARITY_MIN_SAMPLE")
    adverse_action_days: int = Field(30, alias="ADVERSE_ACTION_DAYS")     # ECOA disclosure window
    disclosure_warn_offsets: CsvList = Field(                            # days before the deadline
        default_factory=lambda: ["7", "2"], alias="DISCLOSURE_WARN_OFFSETS")
    audit_retention_years: int = Field(7, alias="AUDIT_RETENTION_YEARS")
    # Quiet hours for the TCPA pre-send check (local contact time).
    quiet_hours_start: str = Field("21:00", alias="QUIET_HOURS_START")
    quiet_hours_end: str = Field("08:00", alias="QUIET_HOURS_END")
    # Roles allowed to export the audit trail (least-privilege).
    export_roles: CsvList = Field(
        default_factory=lambda: ["compliance_officer", "auditor", "admin"], alias="EXPORT_ROLES")
    # Engine identity for Engine-8 signals + the case remediation SLA.
    engine_id: str = Field("compliance-monitoring", alias="ENGINE_ID")
    case_sla_hours: int = Field(72, alias="CASE_SLA_HOURS")
    # Alert noise control (FR-019): suppress a repeat (rule, entity, severity) inside the window.
    alert_dedup_window_min: int = Field(60, alias="ALERT_DEDUP_WINDOW_MIN")
    # Audit-chain integrity sweep cadence (FR-018); 0 disables the background task.
    chain_verify_interval_min: int = Field(30, alias="CHAIN_VERIFY_INTERVAL_MIN")
    # Data-privacy: lead-data retention window + pre-deletion warning (Data Privacy domain).
    data_retention_years: int = Field(7, alias="DATA_RETENTION_YEARS")   # exact-year retention
    data_retention_days: int = Field(0, alias="DATA_RETENTION_DAYS")     # override in days
    retention_warn_days: int = Field(7, alias="RETENTION_WARN_DAYS")
    # PII scanning of comm content + activity payloads (FR-007).
    pii_scan_enabled: bool = Field(True, alias="PII_SCAN_ENABLED")

    # ── WORM audit store + Merkle anchoring (tamper-evidence, design §7/8) ──
    # none (off) | local (write-once files) | s3 (object-lock COMPLIANCE mode).
    worm_gateway: str = Field("none", alias="WORM_GATEWAY")
    worm_local_dir: str = Field("./worm_store", alias="WORM_LOCAL_DIR")
    worm_s3_bucket: str = Field("", alias="WORM_S3_BUCKET")
    worm_s3_prefix: str = Field("audit/", alias="WORM_S3_PREFIX")
    worm_s3_retention_days: int = Field(2555, alias="WORM_S3_RETENTION_DAYS")   # object-lock ~7y
    merkle_anchor_interval_min: int = Field(0, alias="MERKLE_ANCHOR_INTERVAL_MIN")  # 0 disables

    # ── Audit lifecycle: cold-storage tiering + retention purge (CEAAPSS-566) ─
    audit_cold_age_days: int = Field(365, alias="AUDIT_COLD_AGE_DAYS")   # tier to cold after 1y
    audit_lifecycle_interval_min: int = Field(0, alias="AUDIT_LIFECYCLE_INTERVAL_MIN")  # 0 disables
    cold_store_gateway: str = Field("none", alias="COLD_STORE_GATEWAY")  # none | local | s3
    cold_store_dir: str = Field("./cold_store", alias="COLD_STORE_DIR")
    cold_store_s3_bucket: str = Field("", alias="COLD_STORE_S3_BUCKET")
    cold_store_s3_prefix: str = Field("cold/", alias="COLD_STORE_S3_PREFIX")

    # ── Redis hot cache: consent TTL for the fast pre-send path ─────────────
    consent_cache_ttl_s: int = Field(30, alias="CONSENT_CACHE_TTL_S")

    # ── Kafka producer (emit produced events onto the bus) ──────────────────
    kafka_produce: bool = Field(False, alias="KAFKA_PRODUCE")
    kafka_ingest: bool = Field(False, alias="KAFKA_INGEST")   # run the consumer loop (legacy alias)
    kafka_consumer_enabled: bool = Field(False, alias="KAFKA_CONSUMER_ENABLED")
    kafka_consumer_group: str = Field("compliance-monitoring", alias="KAFKA_CONSUMER_GROUP")
    kafka_topics: CsvList = Field(
        default_factory=lambda: ["compliance-audit", "crm.*"], alias="KAFKA_TOPICS"
    )
    kafka_group_id: str = Field("compliance-monitoring", alias="KAFKA_GROUP_ID")

    # ── Coverage-gap / dropped-event monitoring (BR-3, NFR coverage=100%) ───
    coverage_heartbeat_max_min: int = Field(60, alias="COVERAGE_HEARTBEAT_MAX_MIN")
    coverage_sweep_interval_min: int = Field(10, alias="COVERAGE_SWEEP_INTERVAL_MIN")
    dead_letter_alert_threshold: int = Field(1, alias="DEAD_LETTER_ALERT_THRESHOLD")

    # ── HTTP-poll upstream (EVENT_SOURCE_GATEWAY=http) ──────────────────────
    http_poll_interval_s: int = Field(30, alias="HTTP_POLL_INTERVAL_S")

    @model_validator(mode="after")
    def _reject_default_key_in_prod(self) -> "Settings":
        env = os.environ.get("ENV", self.env).lower()
        allowed = {"local", "dev", "development", "test"}
        if self.api_key == "change-me-dev-key" and env not in allowed:
            raise ValueError("Default dev API key is not allowed in non-local environments")
        return self

    @model_validator(mode="after")
    def _reject_wildcard_cors_in_prod(self) -> "Settings":
        env = os.environ.get("ENV", self.env).lower()
        allowed = {"local", "dev", "development", "test"}
        if env not in allowed and any(o == "*" for o in self.cors_origins):
            raise ValueError("Wildcard CORS origin (*) is not allowed in non-local environments")
        return self

    @field_validator(
        "cors_origins", "disclosure_warn_offsets", "export_roles", "kafka_topics", mode="before"
    )
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
