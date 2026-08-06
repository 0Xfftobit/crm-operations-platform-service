"""Application settings for the Performance Analytics System."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field("crequity-performance-analytics", alias="APP_NAME")
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

    storage_backend: str = Field("memory", alias="STORAGE_BACKEND")
    data_dir: str = Field("./data/performance_analytics", alias="DATA_DIR")

    # Attribution / windowing
    attribution_lookback_d: int = Field(90, alias="ATTRIBUTION_LOOKBACK_D")
    attribution_default_model: str = Field("time_decay", alias="ATTRIBUTION_DEFAULT_MODEL")
    time_decay_halflife_d: float = Field(14.0, alias="TIME_DECAY_HALFLIFE_D")
    recon_tolerance: float = Field(0.005, alias="RECON_TOLERANCE")
    cohort_min_n: int = Field(25, alias="COHORT_MIN_N")

    # Anomaly detection
    anomaly_z_threshold: float = Field(2.0, alias="ANOMALY_Z_THRESHOLD")
    anomaly_min_history: int = Field(3, alias="ANOMALY_MIN_HISTORY")

    # RLS
    default_tenant_id: str = Field("default", alias="DEFAULT_TENANT_ID")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
