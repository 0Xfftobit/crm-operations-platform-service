"""System architecture overview builder (§1–§10)."""
from __future__ import annotations

from typing import Any

from app.components.registry import component_details, component_status
from app.core.config import get_settings
from app.messaging.kafka_bus import (
    TOPIC_AUTOMATION_COMPLETED,
    TOPIC_AUTOMATION_FAILED,
    TOPIC_DOCUMENT_RECEIVED,
    TOPIC_DOCUMENT_REQUESTED,
    TOPIC_LEAD_SCORED,
    TOPIC_NBA_RECOMMENDED,
    TOPIC_RECORD_UPDATED,
    TOPIC_STAGE_CHANGED,
    TOPIC_TASK_COMPLETED,
    TOPIC_TASK_CREATED,
)
from app.pipeline import pipeline_stages


def build_architecture_overview() -> dict[str, Any]:
    s = get_settings()
    return {
        "name": "Task Automation Engine",
        "role": "Engine 2 — CRM AI Assistant execution arm",
        "principles": [
            "deterministic_first",
            "idempotent_effects",
            "durable_orchestration",
            "separation_of_decision_and_action",
            "auditable_by_design",
        ],
        "context": {
            "peers": ["Stage Tracking", "Next-Best-Action", "Follow-up Sequences"],
            "downstream": ["CRM Service API", "Engine 3 Generative Docs", "Engine 5 API/XML", "Engine 8 Compliance"],
            "platform": ["Kafka Event Bus", "Temporal", "Feature Store"],
        },
        "components": component_status(),
        "data_flow": pipeline_stages(),
        "technology_stack": {
            "language_runtime": "Python (FastAPI); Go executor planned for production",
            "messaging": "Apache Kafka (in-process bus in dev)",
            "orchestration": f"Temporal @ {s.temporal_host}",
            "rule_dsl": "JSONLogic-style (CEL-compatible subset)",
            "datastores": {
                "postgres": "ledger + rule_registry + workflow_runs (SQLite in dev)",
                "redis": "idempotency + round-robin counters + rule cache",
            },
            "llm": "Platform LLM provider abstraction",
            "api": "REST via API gateway; RBAC scopes",
            "deploy": "Kubernetes + HPA, Helm (local: Docker Compose)",
            "observability": "OpenTelemetry spans, Prometheus metrics, structured logs",
        },
        "messaging": {
            "consumed_topics": [
                TOPIC_STAGE_CHANGED,
                TOPIC_LEAD_SCORED,
                TOPIC_NBA_RECOMMENDED,
                TOPIC_TASK_COMPLETED,
                TOPIC_DOCUMENT_RECEIVED,
            ],
            "produced_topics": [
                TOPIC_TASK_CREATED,
                TOPIC_RECORD_UPDATED,
                TOPIC_DOCUMENT_REQUESTED,
                TOPIC_AUTOMATION_COMPLETED,
                TOPIC_AUTOMATION_FAILED,
            ],
            "partition_key": "deal_id",
            "consumer_group": s.kafka_consumer_group,
        },
        "security": {
            "crm_mutations": "CRM service API only — no direct DB",
            "pii": "IDs + necessary fields; TLS in transit",
            "rule_authoring": "RBAC + versioned audit",
            "ledger": "append-only, immutable, 7y retention, streamed to Engine 8",
            "ecoa": "protected-class proxies excluded from conditions",
        },
        "scalability": {
            "executor_scaling": "HPA on Kafka consumer lag + CPU",
            "temporal_workers": "independent scale",
            "per_deal_ordering": "Kafka partition by deal_id",
            "canary_rollout": f"{s.canary_default_percent}% traffic",
            "action_budget": s.max_actions_per_deal,
        },
    }


def build_observability_snapshot() -> dict[str, Any]:
    from app.dlq import list_dlq
    from app.idempotency import violation_count
    from app.metrics import golden_signals, snapshot
    from app.tracing import get_traces

    details = component_details()
    return {
        "golden_signals": golden_signals(),
        "metrics": snapshot(task_count=0),
        "traces": get_traces(),
        "components": details,
        "alerts": {
            "idempotency_violations": violation_count(),
            "dlq_depth": len(list_dlq(limit=500)),
            "consumer_lag": details["kafka_bus"].get("consumer_lag", 0),
        },
    }
