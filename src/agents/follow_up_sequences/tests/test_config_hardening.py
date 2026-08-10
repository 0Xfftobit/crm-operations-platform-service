"""Config hardening: CORS and API-key validators reject unsafe production configs."""
from __future__ import annotations

import pytest

from app.core.config import get_settings


def _reset_env(monkeypatch):
    """Clear the env vars that affect the validators so each test gets a clean sandbox."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    get_settings.cache_clear()


def test_default_api_key_rejected_in_non_local(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Default dev API key is not allowed"):
        get_settings()


def test_wildcard_cors_rejected_in_non_local(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("API_KEY", "real-staging-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Wildcard CORS origin"):
        get_settings()


def test_valid_prod_config_allowed(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "prod-key")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.crequity.ai")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.env == "production"
    assert settings.api_key == "prod-key"
    assert settings.cors_origins == ["https://app.crequity.ai"]


def test_wildcard_and_default_key_allowed_in_local(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.env == "local"
    assert settings.api_key == "change-me-dev-key"
    assert settings.cors_origins == ["*"]
