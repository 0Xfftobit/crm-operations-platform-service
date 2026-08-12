"""Application settings (pydantic-settings + .env)."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field("crequity-feedback-loop", alias="APP_NAME")
    env: str = Field("local", alias="ENV")
    debug: bool = Field(True, alias="DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_json: bool = Field(False, alias="LOG_JSON")
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")
    api_key: str = Field("change-me-dev-key", alias="API_KEY")
    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000"],
        alias="CORS_ORIGINS",
    )

    # --- In-process / disk storage (datasets + model artifacts) ---
    storage_backend: str = Field("memory", alias="STORAGE_BACKEND")
    data_dir: str = Field("./data/feedback_loop", alias="DATA_DIR")
    model_snapshot_dir: str = Field("./data/feedback_loop/models", alias="MODEL_SNAPSHOT_DIR")

    # --- Durable SQL backend (Postgres/SQLite) ---
    # memory (default, in-memory repos) | postgres (durable, needs DATABASE_URL).
    storage_gateway: str = Field("memory", alias="STORAGE_GATEWAY")
    database_url: str = Field("", alias="DATABASE_URL")
    db_auto_create: bool = Field(False, alias="DB_AUTO_CREATE")

    redis_url: str | None = Field(None, alias="REDIS_URL")

    # Outcome observation windows (days)
    window_response_days: int = Field(7, alias="WINDOW_RESPONSE_DAYS")
    window_stage_days: int = Field(30, alias="WINDOW_STAGE_DAYS")
    window_conversion_days: int = Field(90, alias="WINDOW_CONVERSION_DAYS")
    funded_label_window_days: int = Field(60, alias="FUNDED_LABEL_WINDOW_DAYS")

    # Drift / performance thresholds
    psi_threshold: float = Field(0.2, alias="PSI_THRESHOLD")
    drift_persistence_evals: int = Field(2, alias="DRIFT_PERSISTENCE_EVALS")
    min_training_samples: int = Field(50, alias="MIN_TRAINING_SAMPLES")
    holdout_ratio: float = Field(0.2, alias="HOLDOUT_RATIO")
    accuracy_threshold: float = Field(0.70, alias="ACCURACY_THRESHOLD")
    accuracy_epsilon: float = Field(0.01, alias="ACCURACY_EPSILON")
    fairness_ratio_min: float = Field(0.80, alias="FAIRNESS_RATIO_MIN")
    fairness_ratio_max: float = Field(1.25, alias="FAIRNESS_RATIO_MAX")

    # Retraining
    auto_retrain_enabled: bool = Field(True, alias="AUTO_RETRAIN_ENABLED")
    auto_retrain_risk_class: str = Field("low", alias="AUTO_RETRAIN_RISK_CLASS")

    # Kafka (optional — bus degrades to in-process log when disabled/unavailable)
    kafka_bootstrap_servers: str = Field("localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_produce: bool = Field(False, alias="KAFKA_PRODUCE")
    kafka_consumer_enabled: bool = Field(False, alias="KAFKA_CONSUMER_ENABLED")
    kafka_consumer_group: str = Field("crequity-feedback-loop", alias="KAFKA_CONSUMER_GROUP")
    kafka_topics: CsvList = Field(
        default_factory=lambda: [
            "crm.outcome.funded",
            "crm.outcome.lost",
            "crm.outreach.converted",
        ],
        alias="KAFKA_TOPICS",
    )

    @field_validator("kafka_topics", mode="before")
    @classmethod
    def _split_topics(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [x.strip() for x in s.split(",") if x.strip()]
        return v

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_list(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [x.strip() for x in s.split(",") if x.strip()]
        return v

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def model_path(self) -> Path:
        return Path(self.model_snapshot_dir)

    @property
    def uses_sql(self) -> bool:
        """True when a durable SQL backend is configured."""
        return self.storage_gateway == "postgres" and bool(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
