"""System design overview and testing strategy matrix (§1–§9)."""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings


def build_system_design() -> dict[str, Any]:
    s = get_settings()
    return {
        "document": "System Design",
        "components": {
            "trigger_ingestor": {
                "description": "Kafka consumer group + scheduler; canonical envelope with dedupe",
                "envelope_fields": ["trigger_id", "type", "deal_id", "payload", "occurred_at", "source"],
                "module": "app.messaging.trigger_ingestor",
            },
            "rule_engine": {
                "description": "Hot cache + condition DSL + conflict resolution → action plan",
                "conflict_resolution": ["priority desc", "specificity", "rule_id"],
                "module": "app.engine.conflict_resolver",
            },
            "action_executor": {
                "description": "Connector calls as Temporal activities with idempotency + ledger",
                "module": "app.integrations.activity_runner",
                "activities": "app.integrations.activities.connectors",
                "worker": "scripts/temporal_worker.py",
            },
            "llm_task_agent": {
                "description": "Stateless drafts + confidence; writes gated by threshold + approval",
                "module": "app.agents.llm_task_agent",
            },
            "workflow_orchestrator": {
                "description": "Multi-step chains with timers, signals, compensation",
                "module": "app.workflows.doc_request_temporal",
                "facade": "app.workflows.doc_request_chain",
            },
        },
        "workflow_states": [
            "Received", "Enriched", "Evaluated", "Idempotent", "Executing",
            "AwaitingDocs", "Verifying", "Escalated", "Deduped", "Completed",
        ],
        "data_models": {
            "rule_registry": "rule_id, name, version, event_types, conditions, actions, priority, status",
            "action_ledger": "ledger_id, trigger_id, rule_id, action_type, action_key, deal_id, inputs_hash, outcome",
            "workflow_runs": "run_id, deal_id, status, started_at",
            "task_index": "task_id, deal_id, status, sla_due_at",
        },
        "redis_keys": {
            "idempotency": "idem:{trigger_id}:{rule_id}:{action_key}",
            "loop_budget": "loopbudget:{deal_id}",
            "rule_cache": "rule:{rule_id}:v{version}",
        },
        "configuration": {
            "per_deal_action_budget": s.max_actions_per_deal,
            "llm_confidence_threshold": s.llm_confidence_threshold,
            "default_task_sla_hours": s.default_task_sla_hours,
            "canary_pct": s.canary_default_percent,
            "retry_max_attempts": s.retry_max_attempts,
            "retry_backoff_seconds": s.retry_backoff_seconds,
            "retry_backoff_max_seconds": s.retry_backoff_max_seconds,
            "activity_timeout_s": s.activity_timeout_s,
            "temporal_enabled": s.temporal_enabled,
            "temporal_task_queue": s.temporal_task_queue,
            "idempotency_ttl_days": s.idempotency_ttl_days,
            "blast_radius_warning_threshold": s.blast_radius_warning_threshold,
        },
        "testing_strategy": [
            {"layer": "unit", "coverage": "DSL evaluation, conflict resolution, idempotency keys"},
            {"layer": "contract", "coverage": "Event schema + CRM/Engine 3 API contracts (stub)"},
            {"layer": "replay", "coverage": "Re-emit same event N times → exactly-once effects"},
            {"layer": "workflow", "coverage": "Temporal timers, signals, compensation"},
            {"layer": "load", "coverage": "15k events/s burst target (not validated in dev)"},
            {"layer": "guardrail", "coverage": "LLM low-confidence → draft only"},
            {"layer": "compliance", "coverage": "Protected-class proxy exclusion"},
        ],
    }
