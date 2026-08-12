"""Gateway /metrics endpoint and middleware tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.metrics import generate_metrics


def test_metrics_returns_prometheus_text():
    client = TestClient(app)
    client.get("/health")  # generate at least one observed request
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    body = resp.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "http_requests_in_flight" in body
    assert "cross_agent_calls_total" in body
    assert 'method="GET"' in body
    assert 'status="200"' in body


def test_generate_metrics_returns_exposition_format():
    text = generate_metrics()
    assert "http_requests_total" in text
    assert text.endswith("\n")
