"""Activity runner — Temporal activities with retry/backoff from config."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.integrations.activities.connectors import ACTIVITY_NAMES

logger = logging.getLogger(__name__)

_execution_log: list[dict[str, Any]] = []


class ConnectorActivityError(Exception):
    def __init__(self, activity_name: str, attempts: int, cause: Exception) -> None:
        super().__init__(f"{activity_name} failed after {attempts} attempts: {cause}")
        self.activity_name = activity_name
        self.attempts = attempts
        self.cause = cause


def reset_activity_runner() -> None:
    _execution_log.clear()


def get_activity_executions(*, limit: int = 50) -> list[dict[str, Any]]:
    return list(reversed(_execution_log[-limit:]))


def build_retry_policy_dict() -> dict[str, Any]:
    settings = get_settings()
    return {
        "maximum_attempts": settings.retry_max_attempts,
        "initial_interval_seconds": settings.retry_backoff_seconds,
        "maximum_interval_seconds": settings.retry_backoff_max_seconds,
        "backoff_coefficient": settings.retry_backoff_coefficient,
    }


def temporal_retry_policy_kwargs() -> dict[str, Any]:
    """Kwargs for temporalio.common.RetryPolicy + activity timeout."""
    settings = get_settings()
    policy = build_retry_policy_dict()
    return {
        "initial_interval": policy["initial_interval_seconds"],
        "maximum_interval": policy["maximum_interval_seconds"],
        "backoff_coefficient": policy["backoff_coefficient"],
        "maximum_attempts": policy["maximum_attempts"],
        "start_to_close_timeout_seconds": settings.activity_timeout_s,
    }


def _backoff_seconds(attempt: int, policy: dict[str, Any]) -> float:
    initial = float(policy["initial_interval_seconds"])
    maximum = float(policy["maximum_interval_seconds"])
    coefficient = float(policy["backoff_coefficient"])
    base = min(initial * (coefficient ** (attempt - 1)), maximum)
    return base * (0.5 + random.random())


def _invoke_activity(
    activity_fn: Callable[[dict[str, Any]], dict[str, Any] | Coroutine[Any, Any, dict[str, Any]]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = activity_fn(payload)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def _execute_local_with_retry(
    activity_name: str,
    activity_fn: Callable[[dict[str, Any]], dict[str, Any]],
    payload: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or build_retry_policy_dict()
    max_attempts = int(policy["maximum_attempts"])
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            result = _invoke_activity(activity_fn, payload)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            row = {
                "activity_name": activity_name,
                "attempt": attempt,
                "attempts_total": attempt,
                "outcome": "success",
                "provider": "local_activity",
                "latency_ms": elapsed_ms,
                "retry_policy": policy,
            }
            _execution_log.append(row)
            return {**result, "_activity_meta": row}
        except Exception as exc:
            last_error = exc
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            _execution_log.append({
                "activity_name": activity_name,
                "attempt": attempt,
                "outcome": "failed",
                "error": str(exc),
                "provider": "local_activity",
                "latency_ms": elapsed_ms,
            })
            logger.warning(
                "activity_attempt_failed",
                extra={"activity": activity_name, "attempt": attempt, "error": str(exc)},
            )
            if attempt >= max_attempts:
                break
            time.sleep(_backoff_seconds(attempt, policy))

    raise ConnectorActivityError(activity_name, max_attempts, last_error or RuntimeError("unknown"))


async def _execute_via_temporal_async(activity_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    from temporalio.client import Client

    from app.integrations.temporal_workflows import ConnectorActivityWorkflow

    settings = get_settings()
    options = {
        "retry_policy": build_retry_policy_dict(),
        "activity_timeout_s": settings.activity_timeout_s,
    }
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    handle = await client.start_workflow(
        ConnectorActivityWorkflow.run,
        args=[activity_name, payload, options],
        id=f"connector-{activity_name}-{uuid4().hex[:12]}",
        task_queue=settings.temporal_task_queue,
    )
    result = await handle.result()
    _execution_log.append({
        "activity_name": activity_name,
        "outcome": "success",
        "provider": "temporal",
        "workflow_id": handle.id,
        "retry_policy": build_retry_policy_dict(),
    })
    return {**result, "_activity_meta": {"provider": "temporal", "workflow_id": handle.id}}


def _execute_via_temporal(activity_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_execute_via_temporal_async(activity_name, payload))


def execute_connector_activity(activity_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run connector call as Temporal activity (remote worker) or local retry executor."""
    if activity_name not in ACTIVITY_NAMES:
        raise ValueError(f"unknown_activity:{activity_name}")

    settings = get_settings()
    if settings.temporal_enabled:
        try:
            return _execute_via_temporal(activity_name, payload)
        except Exception as exc:
            logger.warning(
                "temporal_activity_fallback",
                extra={"activity": activity_name, "error": str(exc)},
            )

    return _execute_local_with_retry(activity_name, ACTIVITY_NAMES[activity_name], payload)


def activity_runner_status() -> dict[str, Any]:
    settings = get_settings()
    recent = get_activity_executions(limit=10)
    failures = sum(1 for r in recent if r.get("outcome") == "failed")
    return {
        "temporal_enabled": settings.temporal_enabled,
        "temporal_host": settings.temporal_host,
        "temporal_task_queue": settings.temporal_task_queue,
        "retry_policy": build_retry_policy_dict(),
        "activity_timeout_s": settings.activity_timeout_s,
        "registered_activities": list(ACTIVITY_NAMES.keys()),
        "recent_executions": recent,
        "recent_failures": failures,
    }
