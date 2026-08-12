"""Prometheus /metrics and request-ID middleware tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

BASE = "/api/v1"


class TestMetrics:
    def test_metrics_endpoint_returns_prometheus_text(self, client: TestClient) -> None:
        # Prime a request so the counter has something to report.
        res = client.get(f"{BASE}/health")
        assert res.status_code == 200

        res = client.get(f"{BASE}/metrics")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/plain")
        body = res.text
        assert "http_requests_total" in body
        assert "http_request_duration_seconds" in body
        assert "http_requests_in_flight" in body

    def test_request_id_echoed_on_response(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/health", headers={"X-Request-ID": "req-123"})
        assert res.status_code == 200
        assert res.headers["x-request-id"] == "req-123"

    def test_request_id_generated_when_missing(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/health")
        assert res.status_code == 200
        assert res.headers["x-request-id"]
