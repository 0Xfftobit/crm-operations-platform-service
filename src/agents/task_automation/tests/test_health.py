"""Readiness and dependency check tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def test_ready_returns_ready_or_degraded(client: TestClient) -> None:
    response = client.get(f"{BASE}/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ready", "degraded")
    assert "checks" in body
    for check in ("storage", "postgres", "redis", "kafka"):
        assert check in body["checks"]
        assert body["checks"][check]["status"] in ("ok", "degraded", "not_configured")
