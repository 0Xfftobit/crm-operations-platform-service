"""Security configuration tests for the Feedback Loop System.

Covers the business rule that default/dev keys and wildcard CORS origins are
rejected outside of local-like environments (BR-3/NFR-Security).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def _clear_and_set_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_default_api_key_rejected_in_non_local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_and_set_env(monkeypatch, ENV="staging", API_KEY="change-me-dev-key")
    with pytest.raises(ValidationError):
        Settings()


def test_wildcard_cors_rejected_in_non_local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_and_set_env(
        monkeypatch,
        ENV="production",
        API_KEY="prod-key",
        CORS_ORIGINS="*",
    )
    with pytest.raises(ValidationError):
        Settings()


def test_default_api_key_allowed_in_local_and_test(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("local", "dev", "development", "test"):
        _clear_and_set_env(monkeypatch, ENV=env, API_KEY="change-me-dev-key")
        settings = Settings()
        assert settings.api_key == "change-me-dev-key"


def test_wildcard_cors_allowed_in_local_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_and_set_env(monkeypatch, ENV="local", API_KEY="change-me-dev-key", CORS_ORIGINS="*")
    settings = Settings()
    assert settings.cors_origins == ["*"]
