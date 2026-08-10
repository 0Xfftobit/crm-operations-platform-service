"""Production-hardening config validation: default dev key and wildcard CORS rejection."""
import os

import pytest

from app.core.config import Settings, get_settings
from app.main import create_app


def _clear_and_setenv(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


# ── Default dev API key rejection in non-local environments ────────────────
def test_default_dev_key_rejected_in_prod(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        API_KEY="change-me-dev-key",
    )
    with pytest.raises(ValueError, match="Default dev API key"):
        Settings(_env_file=None)


def test_default_dev_key_rejected_in_staging(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="staging",
        API_KEY="change-me-dev-key",
    )
    with pytest.raises(ValueError, match="Default dev API key"):
        Settings(_env_file=None)


def test_custom_key_accepted_in_prod(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        API_KEY="prod-api-key",
    )
    s = Settings(_env_file=None)
    assert s.api_key == "prod-api-key"


# ── Wildcard CORS origin rejection in non-local environments ───────────────
def test_wildcard_cors_rejected_in_prod(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        CORS_ORIGINS="*",
    )
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(_env_file=None)


def test_wildcard_cors_rejected_in_prod_json_form(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        CORS_ORIGINS='["*"]',
    )
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(_env_file=None)


def test_specific_origin_accepted_in_prod(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        CORS_ORIGINS="https://app.crequity.ai,https://ops.crequity.ai",
    )
    s = Settings(_env_file=None)
    assert "https://app.crequity.ai" in s.cors_origins
    assert "*" not in s.cors_origins


# ── Local/dev still permits the default key and wildcard CORS ──────────────
def test_local_allows_default_key_and_wildcard_cors(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="local",
        API_KEY="change-me-dev-key",
        CORS_ORIGINS="*",
    )
    s = Settings(_env_file=None)
    assert s.api_key == "change-me-dev-key"
    assert s.cors_origins == ["*"]


def test_dev_allows_default_key_and_wildcard_cors(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="dev",
        API_KEY="change-me-dev-key",
        CORS_ORIGINS='["*"]',
    )
    s = Settings(_env_file=None)
    assert s.api_key == "change-me-dev-key"
    assert "*" in s.cors_origins


# ── FastAPI app creation enforces the same validators ─────────────────────
def test_create_app_rejects_default_key_in_prod(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        API_KEY="change-me-dev-key",
    )
    with pytest.raises(ValueError, match="Default dev API key"):
        create_app()


def test_create_app_rejects_wildcard_cors_in_prod(monkeypatch):
    _clear_and_setenv(
        monkeypatch,
        ENV="production",
        API_KEY="prod-api-key",
        CORS_ORIGINS="*",
    )
    with pytest.raises(ValueError, match="Wildcard CORS"):
        create_app()


# ── Verify the conftest hermetic defaults keep local/test safe ─────────────
def test_conftest_default_settings_are_local_test_safe(monkeypatch):
    # The default API_KEY from conftest must not be the literal dev default
    # so that the test suite can instantiate an app without hitting the prod guard.
    monkeypatch.setenv("ENV", "test")
    get_settings.cache_clear()
    assert os.environ["API_KEY"] != "change-me-dev-key"
    s = Settings(_env_file=None)
    assert s.env == "test"
    assert s.api_key != "change-me-dev-key"
