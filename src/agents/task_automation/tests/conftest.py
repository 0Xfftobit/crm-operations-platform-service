"""Test fixtures."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import store
from app.agents.llm_task_agent import reset_llm_agent
from app.core import metrics as core_metrics
from app.core import resilience
from app.core.config import get_settings
from app.cron_triggers import reset_cron
from app.dlq import reset_dlq
from app.escalation import reset_escalation
from app.event_history import reset_event_history
from app.events import reset_events
from app.guards import reset_guards
from app.idempotency import reset_idempotency
from app.integrations.activity_runner import reset_activity_runner
from app.integrations.feature_store import reset_feature_store
from app.kafka_consumer import reset_kafka
from app.ledger import reset_ledger
from app.main import create_app
from app.messaging.kafka_bus import reset_kafka_bus
from app.messaging.trigger_ingestor import reset_trigger_ingestor
from app.metrics import reset_metrics
from app.persistence.db import reset_db
from app.persistence.redis_store import reset_redis_store
from app.runs import reset_runs
from app.tracing import reset_traces
from app.workflows.doc_request_chain import reset_workflows

API_KEY = "test-key"
BASE = "/api/v1"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    store.reset_store()
    reset_ledger()
    reset_events()
    reset_idempotency()
    reset_metrics()
    core_metrics.reset_metrics()
    resilience.reset_resilience()
    reset_dlq()
    reset_guards()
    reset_kafka()
    reset_cron()
    reset_llm_agent()
    reset_runs()
    reset_event_history()
    reset_escalation()
    reset_traces()
    reset_kafka_bus()
    reset_db()
    reset_redis_store()
    reset_feature_store()
    reset_trigger_ingestor()
    reset_workflows()
    reset_activity_runner()
    yield
    store.reset_store()
    reset_ledger()
    reset_events()
    reset_idempotency()
    reset_metrics()
    core_metrics.reset_metrics()
    resilience.reset_resilience()
    reset_dlq()
    reset_guards()
    reset_kafka()
    reset_cron()
    reset_llm_agent()
    reset_runs()
    reset_event_history()
    reset_escalation()
    reset_traces()
    reset_kafka_bus()
    reset_db()
    reset_redis_store()
    reset_feature_store()
    reset_trigger_ingestor()
    reset_workflows()
    reset_activity_runner()
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), headers={"X-API-Key": API_KEY})


def create_stage_change_rule(client: TestClient) -> dict:
    return client.post(
        f"{BASE}/rules",
        json={
            "name": "Underwriting call",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "underwriting"}],
            "task_type": "call",
            "delay_hours": 2,
            "assignee_strategy": "lead_owner",
            "priority": "high",
        },
    ).json()
