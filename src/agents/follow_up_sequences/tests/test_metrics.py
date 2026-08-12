"""Prometheus /metrics endpoint and in-memory metric collection tests."""
from __future__ import annotations

from app.core.metrics import (
    CROSS_AGENT_CALLS_TOTAL,
    HTTP_REQUESTS_TOTAL,
    KAFKA_CONSUMED_TOTAL,
    KAFKA_PRODUCED_TOTAL,
    generate_metrics,
)


def test_metrics_endpoint_returns_prometheus_text(client):
    # Make a request so the middleware records at least one observation.
    r = client.get("/health")
    assert r.status_code == 200

    resp = client.get("/api/v1/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text

    assert "# HELP http_requests_total" in body
    assert 'http_requests_total{method="GET",status="200"}' in body
    assert "# HELP http_request_duration_seconds" in body
    assert "http_request_duration_seconds_bucket" in body
    assert "# HELP http_requests_in_flight" in body


def test_generate_metrics_includes_app_counters():
    HTTP_REQUESTS_TOTAL.labels(method="GET", status="200").inc()
    KAFKA_PRODUCED_TOTAL.labels(topic="crm.outreach.sent").inc()
    KAFKA_CONSUMED_TOTAL.labels(topic="crm.deal.stage_changed").inc()
    CROSS_AGENT_CALLS_TOTAL.labels(client="compliance", status="200").inc()

    text = generate_metrics()
    assert "http_requests_total" in text
    assert 'kafka_produced_total{topic="crm.outreach.sent"}' in text
    assert 'kafka_consumed_total{topic="crm.deal.stage_changed"}' in text
    assert 'cross_agent_calls_total{client="compliance",status="200"}' in text
