"""Application settings (pydantic-settings + .env)."""
import json
import os
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    app_name: str = Field("crequity-task-automation", alias="APP_NAME")
    env: str = Field("local", alias="ENV")
    debug: bool = Field(True, alias="DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_json: bool = Field(False, alias="LOG_JSON")
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")
    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS"
    )

    api_key: str = Field("change-me-dev-key", alias="API_KEY")
    task_automation_engine_version: str = Field("rules-engine-1.0", alias="TASK_AUTOMATION_ENGINE_VERSION")

    storage_backend: str = Field("memory", alias="STORAGE_BACKEND")
    database_path: str = Field("./data/tasks.db", alias="TASK_AUTOMATION_DB_PATH")
    database_url: str = Field("", alias="DATABASE_URL")

    scheduler_enabled: bool = Field(True, alias="SCHEDULER_ENABLED")
    scheduler_interval_s: int = Field(60, alias="SCHEDULER_INTERVAL_S", ge=5)
    default_task_timeout_s: int = Field(300, alias="DEFAULT_TASK_TIMEOUT_S", ge=1)
    max_concurrent_tasks: int = Field(10, alias="MAX_CONCURRENT_TASKS", ge=1)
    duplicate_task_window_hours: float = Field(4.0, alias="DUPLICATE_TASK_WINDOW_HOURS", ge=0)

    business_timezone: str = Field("America/Chicago", alias="BUSINESS_TIMEZONE")
    business_start_hour: int = Field(9, alias="BUSINESS_START_HOUR", ge=0, le=23)
    business_end_hour: int = Field(17, alias="BUSINESS_END_HOUR", ge=1, le=24)

    round_robin_agents: CsvList = Field(
        default_factory=lambda: ["lo:amy", "lo:bob", "lo:carol"],
        alias="ROUND_ROBIN_AGENTS",
    )

    stage_tracking_url: str = Field("", alias="STAGE_TRACKING_URL")
    stage_tracking_api_key: str = Field("change-me-dev-key", alias="STAGE_TRACKING_API_KEY")
    lead_assignment_url: str = Field("", alias="LEAD_ASSIGNMENT_URL")
    lead_assignment_api_key: str = Field("change-me-dev-key", alias="LEAD_ASSIGNMENT_API_KEY")
    crm_service_url: str = Field("", alias="CRM_SERVICE_URL")
    crm_api_key: str = Field("", alias="CRM_API_KEY")
    calendar_service_url: str = Field("", alias="CALENDAR_SERVICE_URL")
    calendar_api_key: str = Field("", alias="CALENDAR_API_KEY")
    follow_up_sequences_url: str = Field("", alias="FOLLOW_UP_SEQUENCES_URL")
    follow_up_sequences_api_key: str = Field("", alias="FOLLOW_UP_SEQUENCES_API_KEY")
    fus_fanout_enabled: bool = Field(False, alias="FUS_FANOUT_ENABLED")
    integration_timeout_s: float = Field(5.0, alias="INTEGRATION_TIMEOUT_S", ge=0.5)

    kafka_bootstrap_servers: str = Field("localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_produce: bool = Field(False, alias="KAFKA_PRODUCE")
    kafka_consumer_group: str = Field("task-automation-engine", alias="KAFKA_CONSUMER_GROUP")
    kafka_consumer_enabled: bool = Field(False, alias="KAFKA_CONSUMER_ENABLED")
    kafka_topics: CsvList = Field(
        default_factory=lambda: [
            "crm.deal.stage_changed",
            "crm.lead.assigned",
            "crm.document.received",
        ],
        alias="KAFKA_TOPICS",
    )
    temporal_host: str = Field("localhost:7233", alias="TEMPORAL_HOST")
    temporal_namespace: str = Field("default", alias="TEMPORAL_NAMESPACE")
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    max_actions_per_deal: int = Field(25, alias="MAX_ACTIONS_PER_DEAL", ge=1)
    max_rule_chain_depth: int = Field(10, alias="MAX_RULE_CHAIN_DEPTH", ge=1)
    canary_default_percent: float = Field(1.0, alias="CANARY_DEFAULT_PERCENT", ge=0, le=100)
    llm_confidence_threshold: float = Field(0.85, alias="LLM_CONFIDENCE_THRESHOLD", ge=0, le=1)
    llm_provider_url: str = Field("", alias="LLM_PROVIDER_URL")
    llm_api_key: str = Field("", alias="LLM_API_KEY")
    llm_model: str = Field("gpt-4o-mini", alias="LLM_MODEL")
    llm_timeout_s: float = Field(10.0, alias="LLM_TIMEOUT_S", ge=1)
    mandatory_dry_run_before_publish: bool = Field(True, alias="MANDATORY_DRY_RUN_BEFORE_PUBLISH")

    idempotency_backend: str = Field("memory", alias="IDEMPOTENCY_BACKEND")
    idempotency_ttl_days: int = Field(7, alias="IDEMPOTENCY_TTL_DAYS", ge=1)
    default_task_sla_hours: float = Field(48.0, alias="DEFAULT_TASK_SLA_HOURS", ge=1)
    retry_max_attempts: int = Field(5, alias="RETRY_MAX_ATTEMPTS", ge=1)
    retry_backoff_seconds: float = Field(30.0, alias="RETRY_BACKOFF_SECONDS", ge=0.1)
    retry_backoff_max_seconds: float = Field(300.0, alias="RETRY_BACKOFF_MAX_SECONDS", ge=1)
    retry_backoff_coefficient: float = Field(2.0, alias="RETRY_BACKOFF_COEFFICIENT", ge=1)
    activity_timeout_s: float = Field(30.0, alias="ACTIVITY_TIMEOUT_S", ge=1)
    temporal_enabled: bool = Field(False, alias="TEMPORAL_ENABLED")
    temporal_task_queue: str = Field("task-automation", alias="TEMPORAL_TASK_QUEUE")
    loop_budget_ttl_hours: float = Field(1.0, alias="LOOP_BUDGET_TTL_HOURS", ge=0.1)
    blast_radius_warning_threshold: int = Field(100, alias="BLAST_RADIUS_WARNING_THRESHOLD", ge=1)
    feature_store_url: str = Field("", alias="FEATURE_STORE_URL")
    feature_store_api_key: str = Field("", alias="FEATURE_STORE_API_KEY")
    otel_enabled: bool = Field(False, alias="OTEL_ENABLED")

    export_local_dir: str = Field("./data/exports", alias="EXPORT_LOCAL_DIR")
    s3_export_bucket: str = Field("", alias="S3_EXPORT_BUCKET")
    s3_export_prefix: str = Field("task-automation/ledger", alias="S3_EXPORT_PREFIX")
    s3_export_region: str = Field("", alias="S3_EXPORT_REGION")

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

    @field_validator("cors_origins", "round_robin_agents", "kafka_topics", mode="before")
    @classmethod
    def _split_list(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [x.strip() for x in s.split(",") if x.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
