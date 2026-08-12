"""Health and readiness endpoint tests for the Feedback Loop System."""

from __future__ import annotations

from fastapi.testclient import TestClient

API_KEY = "test-key"
BASE = "/api/v1"


class TestHealth:
    def test_liveness(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_root_health(self, client: TestClient) -> None:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_readiness_default(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/ready")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ready"
        assert "checks" in body
        for name, check in body["checks"].items():
            assert check["status"] in ("ok", "not_configured"), f"{name} should not be degraded by default"
        assert body["checks"]["data"]["status"] == "ok"
        assert body["checks"]["models"]["status"] == "ok"
        assert body["checks"]["postgres"]["status"] == "not_configured"
        assert body["checks"]["redis"]["status"] == "not_configured"
        assert body["checks"]["kafka"]["status"] == "not_configured"
