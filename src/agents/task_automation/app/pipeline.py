"""Automation pipeline — ingest → enrich → rule → idempotency → execute → ledger (§4)."""
from __future__ import annotations

from typing import Any

from app import store
from app.engine.action_executor import evaluate_rules
from app.enrichment import enrich_payload
from app.integrations.feature_store import fetch_features
from app.messaging.kafka_bus import partition_key
from app.messaging.trigger_ingestor import normalize_trigger
from app.persistence.db import init_schema
from app.tracing import trace_span


def pipeline_stages() -> list[str]:
    return [
        "trigger_ingestor",
        "enrichment_resolver",
        "rule_engine",
        "idempotency_check",
        "action_executor",
        "workflow_orchestrator",
        "action_ledger",
        "dlq_manager",
    ]


def run_pipeline_stages(
    *,
    topic: str,
    payload: dict[str, Any],
    source: str = "api",
    dry_run: bool = False,
    include_draft: bool = False,
    tenant_id: str | None = None,
    feature_flags: set[str] | None = None,
    trigger_id: str | None = None,
) -> dict[str, Any]:
    """Execute architecture data-flow stages with tracing."""
    stage_timings: dict[str, float] = {}

    with trace_span("trigger_ingestor", topic=topic, source=source) as s1:
        envelope = normalize_trigger(topic=topic, payload=payload, source=source)
    stage_timings["trigger_ingestor"] = s1["duration_ms"]
    if envelope.get("duplicate"):
        return {
            "envelope": envelope,
            "enriched_payload": payload,
            "lead_id": str(payload.get("lead_id") or payload.get("deal_id") or "unknown"),
            "trigger_id": payload.get("trigger_id"),
            "evaluation": {"planned": [], "skipped": [{"reason": "duplicate_event_id"}]},
            "stage_timings_ms": stage_timings,
            "dry_run": dry_run,
        }

    trigger_id = trigger_id or envelope["trigger_id"]
    enriched_payload = envelope["payload"]

    with trace_span("enrichment_resolver", deal_id=envelope["deal_id"]) as s2:
        features = fetch_features(
            deal_id=envelope["deal_id"],
            lead_id=enriched_payload.get("lead_id"),
            product=enriched_payload.get("product"),
            stage=enriched_payload.get("stage") or enriched_payload.get("to_stage"),
        )
        enriched_payload = enrich_payload(
            topic=topic,
            payload={**enriched_payload, "features": features, **features},
        )
        lead_id = str(
            enriched_payload.get("lead_id")
            or enriched_payload.get("deal_id")
            or partition_key(enriched_payload)
        )
    stage_timings["enrichment_resolver"] = s2["duration_ms"]

    with trace_span("rule_engine", lead_id=lead_id) as s3:
        rules = store.rules_for_event(
            topic, tenant_id=tenant_id, feature_flags=feature_flags, include_draft=include_draft,
        )
        evaluation = evaluate_rules(
            topic=topic,
            payload=enriched_payload,
            rules=rules,
            lead_id=lead_id,
            existing_tasks=store.all_tasks_raw(),
            dry_run=dry_run,
            trigger_id=trigger_id,
            include_draft=include_draft,
        )
    stage_timings["rule_engine"] = s3["duration_ms"]

    return {
        "envelope": envelope,
        "enriched_payload": enriched_payload,
        "lead_id": lead_id,
        "trigger_id": trigger_id,
        "evaluation": evaluation,
        "stage_timings_ms": stage_timings,
        "dry_run": dry_run,
    }


def init_pipeline() -> dict[str, Any]:
    from app import store

    schema = init_schema()
    hydrated = store.hydrate_rules_from_db()
    return {
        "pipeline": "task_automation",
        "stages": pipeline_stages(),
        "schema": schema,
        "rules_hydrated": hydrated,
    }
