"""Gateway settings (shared platform config)."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = Field("change-me-dev-key", alias="API_KEY")
    gateway_port: int = Field(8080, alias="GATEWAY_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    gateway_timeout: float = Field(30.0, alias="GATEWAY_TIMEOUT")
    # When true (default), the default dev key is rejected in non-debug envs.
    reject_default_key: bool = Field(True, alias="GATEWAY_REJECT_DEFAULT_KEY")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS"
    )

    # Upstream agent URLs and per-agent timeouts.
    compliance_monitor_url: str = Field("", alias="COMPLIANCE_MONITOR_URL")
    task_automation_url: str = Field("", alias="TASK_AUTOMATION_URL")
    follow_up_sequences_url: str = Field("", alias="FOLLOW_UP_SEQUENCES_URL")
    feedback_loop_url: str = Field("", alias="FEEDBACK_LOOP_URL")
    performance_analytics_url: str = Field("", alias="PERFORMANCE_ANALYTICS_URL")

    compliance_monitor_timeout: float = Field(10.0, alias="COMPLIANCE_MONITOR_TIMEOUT")
    task_automation_timeout: float = Field(30.0, alias="TASK_AUTOMATION_TIMEOUT")
    follow_up_sequences_timeout: float = Field(15.0, alias="FOLLOW_UP_SEQUENCES_TIMEOUT")
    feedback_loop_timeout: float = Field(20.0, alias="FEEDBACK_LOOP_TIMEOUT")
    performance_analytics_timeout: float = Field(30.0, alias="PERFORMANCE_ANALYTICS_TIMEOUT")

    @model_validator(mode="after")
    def _reject_default_in_prod(self) -> Settings:
        if not self.api_key:
            raise ValueError("API_KEY must be set")
        if (
            self.reject_default_key
            and self.api_key == "change-me-dev-key"
            and os.environ.get("ENV", "local").lower() not in ("local", "dev", "development")
        ):
            raise ValueError("Default dev API key is not allowed in non-local environments")
        return self

    @model_validator(mode="after")
    def _reject_wildcard_cors_in_prod(self) -> Settings:
        if (
            os.environ.get("ENV", "local").lower() not in ("local", "dev", "development")
            and any(o == "*" for o in self.cors_origins)
        ):
            raise ValueError("Wildcard CORS origin (*) is not allowed in non-local environments")
        return self

    def agent_urls(self) -> dict[str, str]:
        return {
            "compliance_monitor": self.compliance_monitor_url,
            "task_automation": self.task_automation_url,
            "follow_up_sequences": self.follow_up_sequences_url,
            "feedback_loop": self.feedback_loop_url,
            "performance_analytics": self.performance_analytics_url,
        }

    def agent_timeout(self, agent: str) -> float:
        return getattr(
            self,
            f"{agent.lower().replace('-', '_')}_timeout",
            self.gateway_timeout,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
