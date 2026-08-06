"""KPI metrics — vision §5 success metrics."""
from __future__ import annotations

from typing import Any

_latencies_ms: list[float] = []
_successes = 0
_failures = 0
_actions_total = 0
_doc_requests = 0
_doc_requests_correct = 0
_sla_breaches = 0
_llm_overrides = 0
_llm_actions = 0


def record_event_processing(*, latency_ms: float, success: bool, actions: int = 0) -> None:
    global _successes, _failures, _actions_total
    _latencies_ms.append(latency_ms)
    if len(_latencies_ms) > 5000:
        _latencies_ms.pop(0)
    _actions_total += actions
    if success:
        _successes += 1
    else:
        _failures += 1


def record_doc_request(*, correct: bool = True) -> None:
    global _doc_requests, _doc_requests_correct
    _doc_requests += 1
    if correct:
        _doc_requests_correct += 1


def record_sla_breach() -> None:
    global _sla_breaches
    _sla_breaches += 1


def record_llm_action(*, overridden: bool = False) -> None:
    global _llm_actions, _llm_overrides
    _llm_actions += 1
    if overridden:
        _llm_overrides += 1


def golden_signals() -> dict[str, Any]:
    from app.dlq import list_dlq
    from app.idempotency import violation_count
    from app.messaging.kafka_bus import bus_stats, consumer_lag

    sorted_lat = sorted(_latencies_ms)
    p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0.0
    p99 = sorted_lat[int(len(sorted_lat) * 0.99)] if sorted_lat else 0.0
    total_runs = _successes + _failures
    dlq_depth = len([e for e in list_dlq(limit=500) if e.get("status") == "pending"])
    return {
        "kafka_consumer_lag": consumer_lag(),
        "action_latency_p95_ms": round(p95, 2),
        "action_latency_p99_ms": round(p99, 2),
        "action_success_rate_pct": round(100.0 * _successes / total_runs, 2) if total_runs else 100.0,
        "dlq_depth": dlq_depth,
        "idempotency_violations": violation_count(),
        "kafka_messages_total": bus_stats().get("messages_total", 0),
    }


def snapshot(*, task_count: int = 0, manual_task_estimate: int = 100) -> dict[str, Any]:
    from app.idempotency import violation_count

    sorted_lat = sorted(_latencies_ms)
    p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0.0
    total_runs = _successes + _failures
    automated_pct = round(100.0 * task_count / max(manual_task_estimate, 1), 1) if task_count else 0.0
    return {
        "event_processing_latency_ms_p95": round(p95, 2),
        "automation_success_rate_pct": round(100.0 * _successes / total_runs, 2) if total_runs else 100.0,
        "manual_task_creation_reduction_pct": automated_pct,
        "doc_request_accuracy_pct": round(100.0 * _doc_requests_correct / _doc_requests, 2) if _doc_requests else 100.0,
        "sla_breach_rate_pct": round(100.0 * _sla_breaches / max(_actions_total, 1), 2),
        "idempotency_violations": violation_count(),
        "audit_ledger_actions": _actions_total,
        "llm_human_override_rate_pct": round(100.0 * _llm_overrides / _llm_actions, 2) if _llm_actions else 0.0,
        "targets": {
            "latency_p95_ms": 3000,
            "doc_request_accuracy_pct": 99.0,
            "sla_breach_rate_pct": 2.0,
            "automation_success_rate_pct": 97.0,
            "idempotency_violations": 0,
            "ledger_completeness_pct": 100.0,
            "llm_override_rate_pct": 5.0,
        },
    }


def reset_metrics() -> None:
    global _successes, _failures, _actions_total, _doc_requests, _doc_requests_correct
    global _sla_breaches, _llm_overrides, _llm_actions
    _latencies_ms.clear()
    _successes = _failures = _actions_total = 0
    _doc_requests = _doc_requests_correct = 0
    _sla_breaches = _llm_overrides = _llm_actions = 0
