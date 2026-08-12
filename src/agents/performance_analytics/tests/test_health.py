"""Liveness / health and readiness / ready endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

BASE = "/api/v1"


class TestHealth:
    def test_health_liveness(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_ready_returns_checks(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/ready")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] in ("ready", "degraded")
        assert "checks" in body
        checks = body["checks"]
        for name in ("storage", "postgres", "redis", "kafka"):
            assert name in checks
            assert checks[name]["status"] in ("ok", "degraded", "not_configured")

    def test_ready_default_is_ready(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/ready")
        assert res.status_code == 200
        assert res.json()["status"] == "ready"
