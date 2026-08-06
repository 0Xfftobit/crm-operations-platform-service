"""Temporal workflow — runs a single connector activity with retry policy."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.integrations.activities.connectors import (
        assign_owner_activity,
        create_calendar_event_activity,
        post_note_activity,
        request_document_activity,
        update_record_activity,
    )


_ACTIVITY_BY_NAME = {
    "assign_owner": assign_owner_activity,
    "update_record": update_record_activity,
    "post_note": post_note_activity,
    "request_document": request_document_activity,
    "create_calendar_event": create_calendar_event_activity,
}


@workflow.defn(name="ConnectorActivityWorkflow")
class ConnectorActivityWorkflow:
    """Execute one connector activity durably on a Temporal worker."""

    @workflow.run
    async def run(self, activity_name: str, payload: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        activity_fn = _ACTIVITY_BY_NAME.get(activity_name)
        if activity_fn is None:
            raise ValueError(f"unknown_activity:{activity_name}")

        policy = options.get("retry_policy") or {}
        timeout_s = float(options.get("activity_timeout_s") or 30)

        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=float(policy.get("initial_interval_seconds") or 30)),
            maximum_interval=timedelta(seconds=float(policy.get("maximum_interval_seconds") or 300)),
            backoff_coefficient=float(policy.get("backoff_coefficient") or 2.0),
            maximum_attempts=int(policy.get("maximum_attempts") or 5),
        )

        return await workflow.execute_activity(
            activity_fn,
            payload,
            start_to_close_timeout=timedelta(seconds=timeout_s),
            retry_policy=retry_policy,
        )
