"""Prometheus metrics endpoint tests for the Feedback Loop System."""

from __future__ import annotations

from fastapi.testclient import TestClient

API_KEY = "test-key"
BASE = "/api/v1"


class TestPrometheusMetrics:
    def test_metrics_returns_prometheus_text(self, client: TestClient) -> None:
        # Hit an endpoint first so the middleware records at least one request.
        client.get(f"{BASE}/health")

        res = client.get(f"{BASE}/metrics", headers={"Accept": "text/plain"})
        assert res.status_code == 200
        assert "text/plain" in res.headers["content-type"]
        text = res.text
        assert "http_requests_total" in text
        assert "http_request_duration_seconds" in text
        assert "http_requests_in_flight" in text

    def test_metrics_content_type_includes_version(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/metrics", headers={"Accept": "text/plain"})
        assert res.status_code == 200
        assert "version=0.0.4" in res.headers.get("content-type", "")

    def test_metrics_defaults_to_json_for_unknown_accept(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/metrics")
        assert res.status_code == 200
        # Existing all-engine metrics endpoint should still return JSON.
        assert res.headers.get("content-type") == "application/json"
