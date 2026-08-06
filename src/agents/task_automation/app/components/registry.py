"""Component registry — architecture §3 component status."""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.dlq import list_dlq
from app.integrations.activity_runner import activity_runner_status
from app.integrations.feature_store import store_stats as feature_store_stats
from app.messaging.kafka_bus import bus_stats
from app.persistence.db import store_stats as db_stats
from app.persistence.redis_store import store_stats as redis_stats


def component_status() -> dict[str, str]:
    s = get_settings()
    return {
        "trigger_ingestor": "active",
        "enrichment_resolver": "active",
        "rule_engine": "active",
        "action_executor": "active",
        "workflow_orchestrator": "active" if s.temporal_enabled else "local_doc_workflow",
        "llm_task_agent": "active",
        "idempotency_store": s.idempotency_backend,
        "action_ledger": s.storage_backend,
        "rule_registry": s.storage_backend,
        "dlq_manager": "active",
        "temporal_activities": "active",
    }


def component_details() -> dict[str, Any]:
    pending_dlq = len([e for e in list_dlq(limit=500) if e.get("status") == "pending"])
    return {
        "components": component_status(),
        "kafka_bus": bus_stats(),
        "feature_store": feature_store_stats(),
        "idempotency_store": redis_stats(),
        "data_stores": db_stats(),
        "temporal_activities": activity_runner_status(),
        "dlq_pending": pending_dlq,
    }
