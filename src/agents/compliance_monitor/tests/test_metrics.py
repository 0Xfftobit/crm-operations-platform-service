"""Prometheus metrics endpoint tests."""
import re


def _extract_sample(metrics_text: str, metric_name: str, **labels) -> float | None:
    pattern = re.escape(metric_name)
    if labels:
        parts = [f'{k}="{v}"' for k, v in labels.items()]
        pattern += r"\{[^}]*" + r".*".join(re.escape(p) for p in parts) + r"[^}]*\}"
    pattern += r"\s+([\d.eE+-]+)"
    match = re.search(pattern, metrics_text)
    return float(match.group(1)) if match else None


def test_metrics_endpoint_returns_prometheus_text(client):
    r = client.get("/api/v1/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "http_requests_in_flight" in body


def test_metrics_counter_increments(client):
    before = client.get("/api/v1/metrics").text
    count_before = _extract_sample(before, "http_requests_total", method="GET", status="200")

    r = client.get("/api/v1/health")
    assert r.status_code == 200

    after = client.get("/api/v1/metrics").text
    count_after = _extract_sample(after, "http_requests_total", method="GET", status="200")

    assert count_after is not None
    assert count_before is None or count_after > count_before
