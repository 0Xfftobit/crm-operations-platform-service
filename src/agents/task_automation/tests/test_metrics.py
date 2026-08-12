"""Prometheus /metrics endpoint tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def test_metrics_returns_prometheus_text(client: TestClient) -> None:
    response = client.get(f"{BASE}/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "http_requests_total" in body


def test_metrics_includes_request_total_after_call(client: TestClient) -> None:
    client.get(f"{BASE}/health")
    response = client.get(f"{BASE}/metrics")
    body = response.text
    assert "http_requests_total" in body
    assert 'method="GET"' in body
    assert 'status="200"' in body


def test_metrics_includes_kafka_produced(client: TestClient) -> None:
    response = client.get(f"{BASE}/metrics")
    body = response.text
    assert "kafka_produced_total" in body
