"""Generic Temporal start_workflow — durable timer + compensation metadata."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.integrations.activities.doc_workflow import compensate_doc_request_activity


def _retry_policy(options: dict[str, Any]) -> RetryPolicy:
    policy = options.get("retry_policy") or {}
    return RetryPolicy(
        initial_interval=timedelta(seconds=float(policy.get("initial_interval_seconds") or 30)),
        maximum_interval=timedelta(seconds=float(policy.get("maximum_interval_seconds") or 300)),
        backoff_coefficient=float(policy.get("backoff_coefficient") or 2.0),
        maximum_attempts=int(policy.get("maximum_attempts") or 5),
    )


@workflow.defn(name="GenericStartWorkflow")
class GenericStartWorkflow:
    """Durable wrapper for rule-driven start_workflow actions."""

    def __init__(self) -> None:
        self._status = "started"
        self._compensation: dict[str, Any] | None = None
        self._template = ""
        self._lead_id = ""
        self._rule_id: str | None = None
        self._input_payload: dict[str, Any] = {}
        self._started_at = ""

    @workflow.signal
    def compensate(self, reason: str) -> None:
        self._status = "compensating"

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        info = workflow.info()
        return {
            "workflow_id": info.workflow_id,
            "run_id": info.run_id,
            "template": self._template,
            "lead_id": self._lead_id,
            "rule_id": self._rule_id,
            "status": self._status,
            "provider": "temporal",
            "input": self._input_payload,
            "compensation": self._compensation,
            "compensation_available": self._compensation is None,
            "started_at": self._started_at,
        }

    @workflow.run
    async def run(self, input_payload: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        template = str(input_payload["template"])
        lead_id = str(input_payload["lead_id"])
        rule_id = input_payload.get("rule_id")
        timer_hours = input_payload.get("timer_hours")
        payload = dict(input_payload.get("payload") or {})
        self._template = template
        self._lead_id = lead_id
        self._rule_id = rule_id
        self._input_payload = payload
        self._started_at = workflow.now().isoformat()

        timeout_s = float(options.get("activity_timeout_s") or 30)
        retry_policy = _retry_policy(options)
        activity_timeout = timedelta(seconds=timeout_s)

        if timer_hours:
            try:
                await workflow.wait_condition(
                    lambda: self._status == "compensating",
                    timeout=timedelta(hours=float(timer_hours)),
                )
            except TimeoutError:
                pass

        if self._status == "compensating":
            self._compensation = await workflow.execute_activity(
                compensate_doc_request_activity,
                {
                    "workflow_id": workflow.info().workflow_id,
                    "deal_id": lead_id,
                    "reason": "workflow_compensation",
                },
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            self._status = "compensated"
        else:
            self._status = "completed"

        status = self.get_status()
        status["completed_at"] = workflow.now().isoformat()
        status["durable"] = True
        return status
