"""Bug-audit regression tests for the Task Automation Engine."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import store
from app.messaging.trigger_ingestor import is_duplicate_event, normalize_trigger
from app.persistence import redis_store
from app.runs import create_run, list_runs
from app.workflows.doc_request_chain import complete_verification, start_doc_request_workflow


def test_normalize_trigger_coerces_trigger_id_to_str() -> None:
    envelope = normalize_trigger(topic="deal.stage_changed", payload={"trigger_id": 12345, "lead_id": "l1"})
    assert isinstance(envelope["trigger_id"], str)
    assert envelope["trigger_id"] == "12345"


def test_process_event_asserts_trigger_id_is_str(client: TestClient) -> None:
    with patch("app.service.run_pipeline_stages") as mock_run:
        mock_run.return_value = {
            "envelope": {"deal_id": "d1"},
            "enriched_payload": {"lead_id": "l1", "tenant_id": "default"},
            "lead_id": "l1",
            "trigger_id": 12345,
            "evaluation": {"planned": [], "skipped": []},
        }
        with pytest.raises(AssertionError):
            client.post("/api/v1/events/ingest", json={"topic": "deal.stage_changed", "payload": {"lead_id": "l1"}})


def test_is_duplicate_event_thread_safe() -> None:
    redis_store.reset_redis_store()
    results: list[bool] = []

    def worker() -> None:
        results.append(is_duplicate_event("evt-same-1"))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if not r) == 1
    assert all(isinstance(r, bool) for r in results)


def test_store_create_rule_thread_safe(client: TestClient) -> None:
    def worker(n: int) -> None:
        client.post(
            "/api/v1/rules",
            json={
                "name": f"Rule {n}",
                "trigger_event": "deal.stage_changed",
                "conditions": [{"field": "stage", "operator": "eq", "value": f"stage-{n}"}],
                "task_type": "call",
                "assignee_strategy": "lead_owner",
            },
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store.list_rules()) == 10


def test_runs_create_thread_safe() -> None:
    def worker(n: int) -> None:
        create_run(trigger_id=f"trg-{n}", topic="deal.stage_changed", lead_id="l1", source="test")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(list_runs()) == 10


def test_complete_verification_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_verify(_payload: dict[str, Any]) -> dict[str, Any]:
        return {}  # missing `verified` key

    monkeypatch.setattr("app.workflows.doc_request_chain.verify_documents", _fake_verify)

    wf = start_doc_request_workflow(deal_id="d1", lead_id="l1", document_types=["paystub"])
    wf_id = wf["workflow_id"]

    # Simulate document received so the workflow enters Verifying state.
    with patch("app.workflows.doc_request_chain._use_temporal", return_value=False):
        from app.workflows.doc_request_chain import signal_document_received

        signal_document_received(wf_id, document_id="doc-1", doc_type="paystub")

    completed = complete_verification(wf_id)
    assert completed is not None
    assert completed["verified"] is False
