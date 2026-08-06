"""System architecture acceptance tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def test_architecture_overview(client: TestClient) -> None:
    body = client.get(f"{BASE}/architecture").json()
    assert body["name"] == "Task Automation Engine"
    assert "deterministic_first" in body["principles"]
    assert body["components"]["trigger_ingestor"] == "active"
    assert body["components"]["rule_engine"] == "active"
    assert "crm.deal.stage_changed" in body["messaging"]["consumed_topics"]


def test_architecture_components(client: TestClient) -> None:
    body = client.get(f"{BASE}/architecture/components").json()
    assert "kafka_bus" in body
    assert "idempotency_store" in body
    assert "data_stores" in body


def test_architecture_observability(client: TestClient) -> None:
    body = client.get(f"{BASE}/architecture/observability").json()
    assert "golden_signals" in body
    assert "action_latency_p95_ms" in body["golden_signals"]
    assert "traces" in body
    assert "alerts" in body


def test_pipeline_dry_run(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Arch pipeline rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "task_type": "call",
        },
    )
    result = client.post(f"{BASE}/architecture/pipeline/dry-run").json()
    assert result["evaluation"]["planned"]
    assert "stage_timings_ms" in result


def test_kafka_fanout_on_task_create(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Kafka test",
            "trigger_event": "lead.assigned",
            "conditions": [],
            "task_type": "call",
        },
    )
    client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-kafka-arch", "agent_id": "lo:amy"}},
    )
    obs = client.get(f"{BASE}/architecture/observability").json()
    assert obs["golden_signals"]["kafka_messages_total"] >= 1
