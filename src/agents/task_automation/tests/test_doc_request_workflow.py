"""Doc-request Temporal workflow tests."""
from __future__ import annotations

import asyncio

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.integrations.activities.doc_workflow import DOC_WORKFLOW_ACTIVITIES
from app.workflows.doc_request_chain import (
    complete_verification,
    get_workflow,
    reset_workflows,
    signal_document_received,
    start_doc_request_workflow,
)
from app.workflows.doc_request_temporal import DocRequestChainWorkflow


async def _run_happy_path() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-doc-queue",
            workflows=[DocRequestChainWorkflow],
            activities=DOC_WORKFLOW_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                DocRequestChainWorkflow.run,
                args=[
                    {
                        "deal_id": "L-temporal",
                        "lead_id": "L-temporal",
                        "document_types": ["pay_stub", "w2"],
                        "sla_hours": 48,
                    },
                    {
                        "retry_policy": {
                            "maximum_attempts": 3,
                            "initial_interval_seconds": 1,
                            "maximum_interval_seconds": 5,
                            "backoff_coefficient": 2.0,
                        },
                        "activity_timeout_s": 10,
                    },
                ],
                id="doc-test-happy",
                task_queue="test-doc-queue",
            )
            await handle.signal("document_received", {"document_id": "d1", "doc_type": "pay_stub"})
            await handle.signal("document_received", {"document_id": "d2", "doc_type": "w2"})
            result = await handle.result()
            assert result["state"] == "Completed"
            assert result["verified"] is True
            assert len(result["signals_received"]) == 2


async def _run_sla_escalation() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-doc-queue-sla",
            workflows=[DocRequestChainWorkflow],
            activities=DOC_WORKFLOW_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                DocRequestChainWorkflow.run,
                args=[
                    {
                        "deal_id": "L-sla",
                        "lead_id": "L-sla",
                        "document_types": ["w2"],
                        "sla_hours": 1,
                    },
                    {"retry_policy": {"maximum_attempts": 2}, "activity_timeout_s": 10},
                ],
                id="doc-test-sla",
                task_queue="test-doc-queue-sla",
            )
            await env.sleep(3601)
            status = await handle.query("get_status")
            assert status["state"] == "Escalated"
            assert status["escalated"] is True
            await handle.signal("document_received", {"document_id": "d-late", "doc_type": "w2"})
            result = await handle.result()
            assert result["state"] == "Completed"


def test_doc_request_temporal_happy_path() -> None:
    asyncio.run(_run_happy_path())


def test_doc_request_temporal_sla_escalation() -> None:
    asyncio.run(_run_sla_escalation())


def test_local_doc_request_chain() -> None:
    reset_workflows()
    wf = start_doc_request_workflow(
        deal_id="L-local",
        lead_id="L-local",
        document_types=["w2"],
        sla_hours=48,
    )
    assert wf["state"] == "AwaitingDocs"
    assert wf["provider"] == "local"

    signaled = signal_document_received(wf["workflow_id"], document_id="doc-1", doc_type="w2")
    assert signaled["state"] == "Verifying"

    completed = complete_verification(wf["workflow_id"])
    assert completed["state"] == "Completed"
    assert completed["verified"] is True

    fetched = get_workflow(wf["workflow_id"])
    assert fetched["state"] == "Completed"
