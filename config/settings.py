"""Gateway settings (shared platform config)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = "change-me-dev-key"
    gateway_port: int = 8080
    # Upstream agent URLs (name -> base url), resolved from <AGENT>_URL env vars.
    agent_urls: dict[str, str] = {}


def load_agent_urls() -> dict[str, str]:
    import os
    names = ['compliance_monitor', 'task_automation', 'follow_up_sequences']
    return {n: os.environ.get(f"{n.upper()}_URL", "") for n in names}
