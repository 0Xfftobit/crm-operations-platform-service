"""Security configuration validators — default dev key and CORS wildcard rejection."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.main import create_app


def test_default_dev_api_key_rejected_in_non_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-018 / NFR Security: default dev key must be rejected outside local/test."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="Default dev API key is not allowed in non-local environments"):
        get_settings()


def test_wildcard_cors_rejected_in_non_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR Security: wildcard CORS must be rejected in non-local environments."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "prod-key-123")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="Wildcard CORS origin \\(\\*\\) is not allowed in non-local environments"):
        get_settings()


def test_local_env_allows_default_key_and_wildcard_cors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local/test env may still use the default key and wildcard CORS for dev UX."""
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.api_key == "change-me-dev-key"
    assert "*" in settings.cors_origins


def test_cors_middleware_allows_configured_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """CORS middleware echoes allowed origins and rejects unknown ones."""
    monkeypatch.setenv("CORS_ORIGINS", "http://allowed.local,http://app.local")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        resp = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://allowed.local",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://allowed.local"

        blocked = client.get("/api/v1/health", headers={"Origin": "http://evil.local"})
        assert "access-control-allow-origin" not in blocked.headers
