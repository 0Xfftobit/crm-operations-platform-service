"""Gateway smoke and P4 integration tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings, get_settings
from src.api.main import app


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_health_unauthenticated():
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_agents_unauthenticated():
    body = TestClient(app).get("/agents").json()
    assert isinstance(body, dict) and body
    assert "compliance_monitor" in body
    assert "performance_analytics" in body
    assert "feedback_loop" in body


def test_proxy_requires_api_key():
    client = TestClient(app)
    # No API key → 401 even before we know the agent exists.
    assert client.get("/compliance-monitor/api/v1/health").status_code == 401


def test_proxy_unknown_agent_returns_404():
    client = TestClient(app)
    assert client.get(
        "/unknown-agent/test",
        headers={"X-API-Key": "change-me-dev-key"},
    ).status_code == 404


def test_proxy_unknown_upstream_returns_502():
    client = TestClient(app)
    # All agent URLs are unset in the test environment, so the slug is known but the
    # upstream is not configured.
    assert client.get(
        "/compliance-monitor/api/v1/health",
        headers={"X-API-Key": "change-me-dev-key"},
    ).status_code == 502


def test_ready_without_upstreams_is_degraded():
    client = TestClient(app)
    body = client.get("/ready").json()
    assert body["status"] == "degraded"
    assert "agents" in body
    for _a, v in body["agents"].items():
        assert v == "unset" or v.startswith("down") or v.startswith("http")


def test_default_dev_key_rejected_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        Settings()
