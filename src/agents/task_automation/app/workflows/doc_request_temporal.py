"""Temporal DocRequestChainWorkflow — await docs → verify → complete (§4)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.integrations.activities.doc_workflow import (
        compensate_doc_request_activity,
        documents_complete,
        escalate_doc_request_activity,
        verify_documents_activity,
    )


def _retry_policy(options: dict[str, Any]) -> RetryPolicy:
    policy = options.get("retry_policy") or {}
    return RetryPolicy(
        initial_interval=timedelta(seconds=float(policy.get("initial_interval_seconds") or 30)),
        maximum_interval=timedelta(seconds=float(policy.get("maximum_interval_seconds") or 300)),
        backoff_coefficient=float(policy.get("backoff_coefficient") or 2.0),
        maximum_attempts=int(policy.get("maximum_attempts") or 5),
    )


@workflow.defn(name="DocRequestChainWorkflow")
class DocRequestChainWorkflow:
    """Multi-step doc request: AwaitingDocs → (Escalated) → Verifying → Completed."""

    def __init__(self) -> None:
        self._state = "AwaitingDocs"
        self._signals_received: list[dict[str, Any]] = []
        self._document_types: list[str] = []
        self._deal_id = ""
        self._lead_id = ""
        self._rule_id: str | None = None
        self._sla_hours = 48.0
        self._verified = False
        self._escalated = False
        self._escalation: dict[str, Any] | None = None
        self._verification: dict[str, Any] | None = None
        self._compensation: dict[str, Any] | None = None
        self._compensation_reason: str | None = None
        self._started_at = ""

    @workflow.signal
    def document_received(self, payload: dict[str, Any]) -> None:
        if self._state not in ("AwaitingDocs", "Escalated"):
            return
        self._signals_received.append({
            **payload,
            "at": workflow.now().isoformat(),
        })
        if documents_complete(
            document_types=self._document_types,
            signals_received=self._signals_received,
        ):
            self._state = "Verifying"

    @workflow.signal
    def compensate(self, reason: str) -> None:
        self._state = "Compensating"
        self._compensation_reason = reason

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        info = workflow.info()
        return {
            "workflow_id": info.workflow_id,
            "type": "doc_request_chain",
            "provider": "temporal",
            "deal_id": self._deal_id,
            "lead_id": self._lead_id,
            "document_types": self._document_types,
            "rule_id": self._rule_id,
            "state": self._state,
            "sla_hours": self._sla_hours,
            "signals_received": list(self._signals_received),
            "verified": self._verified,
            "escalated": self._escalated,
            "escalation": self._escalation,
            "verification": self._verification,
            "compensation": self._compensation,
            "compensation_reason": self._compensation_reason,
            "compensation_available": self._compensation is None,
            "started_at": self._started_at,
            "run_id": info.run_id,
        }

    @workflow.run
    async def run(self, input_payload: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        self._deal_id = input_payload["deal_id"]
        self._lead_id = input_payload["lead_id"]
        self._document_types = list(input_payload.get("document_types") or [])
        self._rule_id = input_payload.get("rule_id")
        self._sla_hours = float(input_payload.get("sla_hours") or 48)
        self._started_at = workflow.now().isoformat()

        timeout_s = float(options.get("activity_timeout_s") or 30)
        retry_policy = _retry_policy(options)
        activity_timeout = timedelta(seconds=timeout_s)
        sla_timeout = timedelta(hours=self._sla_hours)

        # AwaitingDocs — wait for document.received signal or SLA timer
        try:
            await workflow.wait_condition(
                lambda: self._state in ("Verifying", "Compensating"),
                timeout=sla_timeout,
            )
        except TimeoutError:
            if self._state == "Compensating":
                pass
            else:
                self._state = "Escalated"
                self._escalated = True
                self._escalation = await workflow.execute_activity(
                    escalate_doc_request_activity,
                    {
                        "deal_id": self._deal_id,
                        "lead_id": self._lead_id,
                        "rule_id": self._rule_id,
                        "sla_hours": self._sla_hours,
                    },
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )
                await workflow.wait_condition(
                    lambda: self._state in ("Verifying", "Compensating"),
                )

        if self._state == "Compensating":
            self._compensation = await workflow.execute_activity(
                compensate_doc_request_activity,
                {
                    "workflow_id": workflow.info().workflow_id,
                    "deal_id": self._deal_id,
                    "reason": self._compensation_reason or "manual_compensation",
                },
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            self._state = "Compensated"
            status = self.get_status()
            status["completed_at"] = workflow.now().isoformat()
            return status

        # Verifying
        self._verification = await workflow.execute_activity(
            verify_documents_activity,
            {
                "deal_id": self._deal_id,
                "lead_id": self._lead_id,
                "document_types": self._document_types,
                "signals_received": self._signals_received,
            },
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        self._verified = bool(self._verification.get("verified", True))
        self._state = "Completed"

        status = self.get_status()
        status["completed_at"] = workflow.now().isoformat()
        return status
