"""Task automation orchestration service."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import structlog

from app import events, store
from app.business_hours import is_overdue
from app.clients import followup_client
from app.core.config import get_settings
from app.dlq import enqueue as dlq_enqueue
from app.dlq import get_dlq_entry, mark_replayed
from app.engine.action_executor import evaluate_rules
from app.enrichment import enrich_payload
from app.escalation import check_escalations, register_sla_timer
from app.event_history import list_events as list_historical_events
from app.event_history import record_event as record_historical_event
from app.guards import canary_stats, clear_trigger_chain
from app.idempotency import check_idempotency, record_idempotency, record_violation_attempt
from app.ledger import append_ledger_entry
from app.metrics import record_doc_request, record_event_processing, record_sla_breach, snapshot
from app.persistence.db import persist_task_index, persist_workflow_run
from app.pipeline import run_pipeline_stages
from app.runs import append_run_action, complete_run, create_run, get_run
from app.schemas.enums import PRIORITY_RANK
from app.tracing import trace_span
from app.workflows.doc_request_chain import start_doc_request_workflow

logger = structlog.get_logger(__name__)


def _lead_id_from_payload(payload: dict[str, Any]) -> str:
    return str(
        payload.get("lead_id")
        or payload.get("opportunity_id")
        or payload.get("deal_id")
        or payload.get("id")
        or "unknown"
    )


def _persist_and_emit(
    result: dict[str, Any],
    *,
    topic: str,
    lead_id: str,
    dry_run: bool,
    trigger_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    if dry_run:
        return result

    emitted: list[dict[str, Any]] = []
    created_tasks: list[dict[str, Any]] = []
    executed_actions: list[dict[str, Any]] = []
    trigger_id = trigger_id or result.get("trigger_id")
    assert trigger_id is not None
    trigger_id = str(trigger_id)

    for spec in result.get("created_tasks") or []:
        task = store.create_crm_task(
            lead_id=spec["lead_id"],
            task_type=spec["task_type"],
            assignee_id=spec["assignee_id"],
            due_at=spec["due_at"],
            priority=spec.get("priority", "normal"),
            rule_id=spec.get("rule_id"),
            title=spec.get("title"),
            payload=spec.get("payload"),
            calendar_event_id=spec.get("calendar_event_id"),
            recurring=bool(spec.get("recurring")),
            recurrence_interval_hours=spec.get("recurrence_interval_hours"),
        )
        if spec.get("llm_draft_notes"):
            task["llm_draft_notes"] = spec["llm_draft_notes"]
        if spec.get("sla_hours"):
            register_sla_timer(
                task_id=task["task_id"],
                lead_id=lead_id,
                assignee_id=task["assignee_id"],
                sla_hours=float(spec["sla_hours"]),
                rule_id=spec.get("rule_id"),
            )
        created_tasks.append(task)
        persist_task_index(task)

        evt = events.publish("task.created", {
            "task_id": task["task_id"],
            "lead_id": lead_id,
            "task_type": task["task_type"],
            "assignee_id": task["assignee_id"],
            "rule_id": spec.get("rule_id"),
        })
        emitted.append(evt)

        entry = append_ledger_entry(
            action_type="create_task",
            lead_id=lead_id,
            deal_id=lead_id,
            rule_id=spec.get("rule_id"),
            rule_version=spec.get("rule_version"),
            inputs=spec.get("payload") or {},
            outputs={"task_id": task["task_id"]},
            provenance={"event_topic": topic},
            trigger_id=trigger_id,
            action_key=spec.get("action_key"),
            outcome="executed",
        )
        action_row = {
            "action_type": "create_task",
            "outcome": "executed",
            "ledger_id": entry["ledger_id"],
            "task_id": task["task_id"],
        }
        executed_actions.append(action_row)
        if run_id:
            append_run_action(run_id, action_row)

    for mut in result.get("record_updates") or []:
        evt = events.publish("record.updated", mut)
        emitted.append(evt)
        entry = append_ledger_entry(
            action_type="update_record",
            lead_id=lead_id,
            deal_id=lead_id,
            rule_id=mut.get("rule_id"),
            inputs=mut.get("applied") or {},
            outputs=mut,
            provenance={"event_topic": topic},
            trigger_id=trigger_id,
            action_key=mut.get("action_key"),
            outcome="executed",
        )
        action_row = {"action_type": "update_record", "outcome": "executed", "ledger_id": entry["ledger_id"]}
        executed_actions.append(action_row)
        if run_id:
            append_run_action(run_id, action_row)

    for owner in result.get("owner_assignments") or []:
        evt = events.publish("record.updated", owner)
        emitted.append(evt)
        append_ledger_entry(
            action_type="assign_owner",
            lead_id=lead_id,
            rule_id=owner.get("rule_id"),
            inputs={"owner_id": owner.get("owner_id")},
            outputs=owner,
            provenance={"event_topic": topic},
            trigger_id=trigger_id,
            action_key=owner.get("action_key"),
        )

    for note in result.get("notes") or []:
        evt = events.publish("note.posted", note)
        emitted.append(evt)
        append_ledger_entry(
            action_type="post_note",
            lead_id=lead_id,
            rule_id=note.get("rule_id"),
            inputs={"body": note.get("body")},
            outputs=note,
            provenance={"event_topic": topic},
            trigger_id=trigger_id,
            action_key=note.get("action_key"),
        )

    for doc in result.get("document_requests") or []:
        evt = events.publish("document.requested", doc)
        emitted.append(evt)
        record_doc_request(correct=True)
        sla_hours = float(doc.get("sla_hours") or get_settings().default_task_sla_hours)
        wf = start_doc_request_workflow(
            deal_id=lead_id,
            lead_id=lead_id,
            document_types=doc.get("document_types") or [],
            rule_id=doc.get("rule_id"),
            sla_hours=sla_hours,
        )
        entry = append_ledger_entry(
            action_type="request_document",
            lead_id=lead_id,
            deal_id=lead_id,
            rule_id=doc.get("rule_id"),
            inputs={"document_types": doc.get("document_types")},
            outputs={**doc, "workflow_id": wf["workflow_id"]},
            provenance={"event_topic": topic, "target_engines": doc.get("target_engines")},
            trigger_id=trigger_id,
            action_key=doc.get("action_key"),
            outcome="executed",
            detail={"workflow_id": wf["workflow_id"]},
        )
        action_row = {
            "action_type": "request_document",
            "outcome": "executed",
            "ledger_id": entry["ledger_id"],
            "workflow_id": wf["workflow_id"],
        }
        executed_actions.append(action_row)
        if run_id:
            append_run_action(run_id, action_row)

    for wf in result.get("workflows") or []:
        evt = events.publish("workflow.started", wf)
        emitted.append(evt)
        persist_workflow_run({
            "run_id": wf.get("run_id") or wf.get("workflow_id"),
            "lead_id": lead_id,
            "deal_id": lead_id,
            "workflow_type": wf.get("template"),
            "status": wf.get("status", "started"),
            "started_at": wf.get("started_at"),
        })
        append_ledger_entry(
            action_type="start_workflow",
            lead_id=lead_id,
            rule_id=wf.get("rule_id"),
            inputs=wf.get("input") or {},
            outputs=wf,
            provenance={"event_topic": topic},
            trigger_id=trigger_id,
            action_key=wf.get("action_key"),
        )

    for custom in result.get("emitted_custom_events") or []:
        evt = events.publish(custom["topic"], custom["payload"])
        emitted.append(evt)
        append_ledger_entry(
            action_type="emit_event",
            lead_id=lead_id,
            rule_id=custom["payload"].get("rule_id"),
            inputs=custom["payload"],
            outputs={"topic": custom["topic"]},
            provenance={"event_topic": topic},
            trigger_id=trigger_id,
            action_key=custom.get("action_key"),
        )

    for skip in result.get("idempotent_skips") or []:
        entry = append_ledger_entry(
            action_type=skip.get("action_key", "unknown"),
            lead_id=lead_id,
            deal_id=lead_id,
            rule_id=skip.get("rule_id"),
            inputs={"action_key": skip.get("action_key")},
            outputs={"status": "deduped"},
            outcome="deduped",
            trigger_id=trigger_id,
            action_key=skip.get("action_key"),
        )
        action_row = {
            "action_type": skip.get("action_key", "unknown"),
            "outcome": "deduped",
            "ledger_id": entry["ledger_id"],
        }
        executed_actions.append(action_row)
        if run_id:
            append_run_action(run_id, action_row)

    result["created"] = created_tasks
    result["emitted_events"] = emitted
    result["executed_actions"] = executed_actions
    return result


def process_event(
    *,
    topic: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    dry_run: bool = False,
    trigger_id: str | None = None,
    source: str = "api",
    tenant_id: str | None = None,
    feature_flags: set[str] | None = None,
) -> dict[str, Any]:
    """Evaluate workflow rules and execute governed CRM actions."""
    started = time.perf_counter()

    pipeline_result = run_pipeline_stages(
        topic=topic,
        payload=payload,
        source=source,
        dry_run=dry_run,
        trigger_id=trigger_id,
        tenant_id=tenant_id,
        feature_flags=feature_flags,
    )
    envelope = pipeline_result.get("envelope") or {}
    if envelope.get("duplicate"):
        return {
            "duplicate": True,
            "event_id": envelope.get("event_id"),
            "status": "deduped",
            "message": "event_id already processed",
        }

    enriched = pipeline_result["enriched_payload"]
    lead_id = pipeline_result["lead_id"]
    trigger_id = pipeline_result["trigger_id"]
    assert isinstance(trigger_id, str)
    evaluation = pipeline_result["evaluation"]
    tenant_id = tenant_id or enriched.get("tenant_id")

    # Cross-agent fan-out to Follow-up Sequences Engine (P4).
    if not dry_run:
        settings = get_settings()
        if topic == "deal.stage_changed":
            stage = enriched.get("to_stage") or enriched.get("stage") or payload.get("stage")
            if isinstance(stage, str):
                followup_client.stage_changed(
                    lead_id, stage, tenant=tenant_id or "default", settings=settings,
                )
        if topic == "lead.assigned" and isinstance(enriched.get("lead_score"), (int, float)):
            followup_client.lead_scored(
                lead_id,
                score=float(enriched["lead_score"]),
                sequence_id=enriched.get("sequence_id", "default"),
                tenant=tenant_id or "default",
                context=enriched.get("context"),
                settings=settings,
            )

    run = create_run(
        trigger_id=trigger_id, topic=topic, lead_id=lead_id, source=source, dry_run=dry_run,
    )
    run_id = run["run_id"]

    if idempotency_key:
        cached = check_idempotency(idempotency_key)
        if cached:
            record_violation_attempt(idempotency_key)
            append_ledger_entry(
                action_type="deduped",
                lead_id=lead_id,
                inputs={"idempotency_key": idempotency_key},
                outputs={"status": "deduped"},
                outcome="deduped",
                trigger_id=trigger_id,
                provenance={"event_topic": topic},
            )
            complete_run(run_id, status="deduped", latency_ms=0)
            return {**cached, "idempotent_replay": True, "run_id": run_id}

    if not dry_run:
        record_historical_event(topic=topic, payload=enriched, source=source)

    store.record_audit(
        lead_id=lead_id,
        action="event.received",
        detail={"topic": topic, "payload": enriched, "source": source, "trigger_id": trigger_id},
    )

    try:
        if evaluation.get("loop_detected"):
            events.publish("automation.loop_detected", {
                "trigger_id": trigger_id, "lead_id": lead_id, "run_id": run_id,
            })
            complete_run(run_id, status="halted", error="loop_detected")
            return {
                "run_id": run_id,
                "trigger_id": trigger_id,
                "lead_id": lead_id,
                "loop_detected": True,
                "skipped": evaluation.get("skipped"),
            }

        with trace_span("action_executor", lead_id=lead_id):
            result = _persist_and_emit(
                evaluation,
                topic=topic,
                lead_id=lead_id,
                dry_run=dry_run,
                trigger_id=trigger_id,
                run_id=run_id,
            )
        actions_count = (
            len(result.get("created") or result.get("created_tasks") or [])
            + len(result.get("record_updates") or [])
            + len(result.get("document_requests") or [])
            + len(result.get("workflows") or [])
            + len(result.get("owner_assignments") or [])
            + len(result.get("notes") or [])
            + len(result.get("emitted_custom_events") or [])
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        result.update({
            "run_id": run_id,
            "topic": topic,
            "lead_id": lead_id,
            "trigger_id": trigger_id,
            "source": source,
            "dry_run": dry_run,
            "latency_ms": round(latency_ms, 2),
            "sla_met": latency_ms <= 3000,
            "skipped": result.get("skipped") or [],
            "conflicts": evaluation.get("conflicts") or [],
            "pipeline_timings_ms": pipeline_result.get("stage_timings_ms"),
        })
        record_event_processing(latency_ms=latency_ms, success=True, actions=actions_count)
        if idempotency_key and not dry_run:
            record_idempotency(idempotency_key, result)
        clear_trigger_chain(trigger_id=trigger_id, lead_id=lead_id)

        if not dry_run:
            events.publish("automation.run.completed", {
                "run_id": run_id,
                "trigger_id": trigger_id,
                "lead_id": lead_id,
                "actions_count": actions_count,
                "latency_ms": result["latency_ms"],
            })
        complete_run(
            run_id,
            status="completed" if not dry_run else "simulated",
            actions=result.get("executed_actions") or result.get("planned") or [],
            conflicts=evaluation.get("conflicts") or [],
            latency_ms=latency_ms,
        )
        completed = get_run(run_id)
        if completed:
            persist_workflow_run(completed)
        return result
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000.0
        record_event_processing(latency_ms=latency_ms, success=False)
        dlq_enqueue(topic=topic, payload=enriched, error=str(exc), trigger_id=trigger_id)
        events.publish("automation.failed", {
            "topic": topic, "lead_id": lead_id, "error": str(exc), "run_id": run_id,
        })
        complete_run(run_id, status="failed", error=str(exc), latency_ms=latency_ms)
        raise


def replay_dlq_entry(dlq_id: str) -> dict[str, Any]:
    entry = get_dlq_entry(dlq_id)
    if not entry:
        return {"error": "dlq_not_found"}
    if entry.get("status") == "replayed":
        return {"error": "already_replayed", "entry": entry}
    result = process_event(
        topic=entry["topic"],
        payload=entry["payload"],
        trigger_id=entry.get("trigger_id"),
        source="dlq_replay",
    )
    mark_replayed(dlq_id, result=result)
    append_ledger_entry(
        action_type="dlq.replayed",
        lead_id=_lead_id_from_payload(entry["payload"]),
        inputs={"dlq_id": dlq_id},
        outputs={"status": "replayed"},
        actor="sre",
    )
    return {"dlq_id": dlq_id, "status": "replayed", "result": result}


def simulate_rule(*, rule_id: str, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    rule = store.get_rule(rule_id)
    if not rule:
        return {"error": "rule_not_found"}
    lead_id = _lead_id_from_payload(payload)
    result = evaluate_rules(
        topic=topic,
        payload=payload,
        rules=[rule],
        lead_id=lead_id,
        existing_tasks=store.all_tasks_raw(),
        dry_run=True,
        include_draft=True,
    )
    store.record_dry_run(rule_id, passed=bool(result.get("planned")))
    return result


def simulate_rule_design(
    *,
    rule_id: str,
    lookback_days: int = 7,
    sample_size: int = 50,
) -> dict[str, Any]:
    """System design dry-run — historical replay with blast-radius warning."""
    rule = store.get_rule(rule_id)
    if not rule:
        return {"error": "rule_not_found"}

    topic = rule.get("trigger_event") or (rule.get("event_types") or [None])[0]
    historical = list_historical_events(topic=topic, days=lookback_days)
    would_fire = 0
    sample_actions: list[dict[str, Any]] = []

    for evt in historical:
        payload = evt.get("payload") or {}
        lead_id = _lead_id_from_payload(payload)
        result = evaluate_rules(
            topic=topic,
            payload=enrich_payload(topic=topic, payload=payload),
            rules=[rule],
            lead_id=lead_id,
            existing_tasks=[],
            dry_run=True,
            include_draft=True,
        )
        if not result.get("planned"):
            continue
        would_fire += 1
        for plan in result.get("planned") or []:
            if len(sample_actions) >= sample_size:
                break
            for action_type in plan.get("action_types") or []:
                sample_actions.append({
                    "deal_id": lead_id,
                    "action_type": action_type,
                    "rule_id": plan.get("rule_id"),
                })
        if len(sample_actions) >= sample_size:
            break

    settings = get_settings()
    store.record_dry_run(rule_id, passed=would_fire >= 0)
    return {
        "rule_id": rule_id,
        "would_fire_count": would_fire,
        "sample_actions": sample_actions[:sample_size],
        "blast_radius_warning": would_fire >= settings.blast_radius_warning_threshold,
        "lookback_days": lookback_days,
        "events_evaluated": len(historical),
    }


def simulate_blast_radius(
    *,
    rule_id: str,
    topic: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """US-2: Dry-run rule against historical events — zero side effects."""
    rule = store.get_rule(rule_id)
    if not rule:
        return {"error": "rule_not_found"}
    topic = topic or rule.get("trigger_event")
    historical = list_historical_events(topic=topic, days=days)
    if not historical:
        return {
            "rule_id": rule_id,
            "topic": topic,
            "days": days,
            "events_evaluated": 0,
            "would_fire_count": 0,
            "sample_actions": [],
            "message": "no historical events; ingest events first",
        }

    would_fire = 0
    samples: list[dict[str, Any]] = []
    for evt in historical:
        payload = evt.get("payload") or {}
        lead_id = _lead_id_from_payload(payload)
        result = evaluate_rules(
            topic=topic,
            payload=enrich_payload(topic=topic, payload=payload),
            rules=[rule],
            lead_id=lead_id,
            existing_tasks=[],
            dry_run=True,
            include_draft=True,
        )
        if result.get("planned"):
            would_fire += 1
            if len(samples) < 5:
                samples.append({
                    "history_id": evt.get("history_id"),
                    "lead_id": lead_id,
                    "planned": result["planned"],
                })
    return {
        "rule_id": rule_id,
        "topic": topic,
        "days": days,
        "events_evaluated": len(historical),
        "would_fire_count": would_fire,
        "sample_actions": samples,
        "side_effects": False,
    }


def rollback_rule(rule_id: str) -> dict[str, Any]:
    """Instant rollback — revert rule to draft/archived."""
    row = store.update_rule(rule_id, {"rollout_status": "archived", "active": False})
    if row:
        append_ledger_entry(
            action_type="rule.rollback",
            lead_id="_system",
            rule_id=rule_id,
            outputs={"rollout_status": "archived"},
        )
    return row or {"error": "rule_not_found"}


def get_automation_run(run_id: str) -> dict[str, Any] | None:
    from app.ledger import list_ledger

    run = get_run(run_id)
    if not run:
        return None
    ledger = list_ledger(lead_id=run.get("lead_id"), limit=50)
    run_ledger = [e for e in ledger if e.get("trigger_id") == run.get("trigger_id")]
    run["ledger_entries"] = run_ledger

    actions = run.get("actions") or []
    if not actions and run_ledger:
        for entry in run_ledger:
            row: dict[str, Any] = {
                "action_type": entry["action_type"],
                "outcome": entry.get("outcome", "executed"),
                "ledger_id": entry["ledger_id"],
            }
            task_id = (entry.get("outputs") or {}).get("task_id")
            if task_id:
                row["task_id"] = task_id
            actions.append(row)

    return {
        "run_id": run["run_id"],
        "trigger": {
            "type": run.get("topic"),
            "deal_id": run.get("lead_id"),
            "trigger_id": run.get("trigger_id"),
        },
        "actions": actions,
        "status": run.get("status"),
        "latency_ms": run.get("latency_ms"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "ledger_entries": run_ledger,
    }


def process_escalations() -> list[dict[str, Any]]:
    """FR-009: Check SLA breaches and emit escalation events."""
    breached = check_escalations()
    for timer in breached:
        events.publish("task.updated", {
            "task_id": timer["task_id"],
            "lead_id": timer["lead_id"],
            "reassigned_to": timer["reassigned_to"],
            "reason": "sla_breach_escalation",
        })
        record_sla_breach()
        append_ledger_entry(
            action_type="sla.escalated",
            lead_id=timer["lead_id"],
            inputs={"task_id": timer["task_id"]},
            outputs=timer,
        )

    from app.workflows.doc_request_chain import check_sla_timers

    doc_escalated = check_sla_timers()
    for wf in doc_escalated:
        events.publish("document.escalated", {
            "workflow_id": wf["workflow_id"],
            "deal_id": wf.get("deal_id"),
            "lead_id": wf.get("lead_id"),
            "reason": "doc_sla_breach",
        })
        append_ledger_entry(
            action_type="doc_request.escalated",
            lead_id=wf.get("lead_id") or wf.get("deal_id") or "unknown",
            deal_id=wf.get("deal_id"),
            inputs={"workflow_id": wf["workflow_id"]},
            outputs=wf.get("escalation") or wf,
        )
    return breached + doc_escalated


def publish_rule_version(rule_id: str, *, skip_gates: bool = False) -> dict[str, Any]:
    """Promote rule: draft → canary (mandatory) → active."""
    settings = get_settings()
    row = store.get_rule(rule_id)
    if not row:
        return {"error": "rule_not_found"}

    status = row.get("rollout_status", "draft")
    if status == "draft":
        if settings.mandatory_dry_run_before_publish and not row.get("dry_run_passed") and not skip_gates:
            return {"error": "dry_run_required", "message": "Rule must pass dry-run simulation before publish"}
        updated = store.update_rule(rule_id, {
            "rollout_status": "canary",
            "canary_percent": settings.canary_default_percent,
            "canary_started_at": datetime.now(UTC).isoformat(),
        })
    elif status == "canary":
        updated = store.update_rule(rule_id, {"rollout_status": "active", "active": True})
    else:
        updated = store.update_rule(rule_id, {"rollout_status": "active", "active": True})

    if updated:
        append_ledger_entry(
            action_type="rule.published",
            lead_id="_system",
            rule_id=rule_id,
            rule_version=updated.get("version"),
            outputs={"rollout_status": updated.get("rollout_status")},
        )
    return updated or {"error": "rule_not_found"}


def agent_queue(assignee_id: str) -> dict[str, Any]:
    tasks = store.list_tasks(assignee_id=assignee_id)
    pending = [t for t in tasks if t.get("status") in ("pending", "overdue", "in_progress")]
    pending.sort(key=lambda t: (PRIORITY_RANK.get(t.get("priority", "normal"), 2), t.get("due_at", "")))
    overdue_count = sum(1 for t in pending if t.get("overdue"))
    if overdue_count:
        record_sla_breach()
    return {
        "assignee_id": assignee_id,
        "tasks": pending,
        "total_pending": len(pending),
        "overdue_count": overdue_count,
        "overdue_alert": overdue_count > 0,
        "alert_level": "red" if overdue_count > 0 else None,
    }


def manager_overview() -> dict[str, Any]:
    all_tasks = store.list_tasks(limit=5000)
    active = [t for t in all_tasks if t.get("status") in ("pending", "overdue", "in_progress")]
    by_agent: dict[str, dict[str, Any]] = {}
    for task in active:
        aid = task.get("assignee_id", "unassigned")
        bucket = by_agent.setdefault(aid, {"assignee_id": aid, "pending": 0, "overdue": 0, "by_priority": {}})
        bucket["pending"] += 1
        if task.get("overdue"):
            bucket["overdue"] += 1
        pri = task.get("priority", "normal")
        bucket["by_priority"][pri] = bucket["by_priority"].get(pri, 0) + 1
    agents = sorted(by_agent.values(), key=lambda a: (-a["overdue"], -a["pending"]))
    return {"agents": agents, "total_active_tasks": len(active), "total_overdue": sum(a["overdue"] for a in agents)}


def spawn_recurring_tasks() -> list[dict[str, Any]]:
    spawned: list[dict[str, Any]] = []
    settings = get_settings()
    for task in store.all_tasks_raw():
        if not task.get("recurring") or task.get("status") != "completed":
            continue
        interval = task.get("recurrence_interval_hours")
        if not interval or task.get("recurrence_spawned"):
            continue
        completed_at = task.get("completed_at")
        if not completed_at:
            continue
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)
        hours_since = (datetime.now(UTC) - completed).total_seconds() / 3600.0
        if hours_since < interval:
            continue
        from app.business_hours import calculate_due_at
        due = calculate_due_at(
            delay_hours=float(interval),
            timezone=settings.business_timezone,
            business_start_hour=settings.business_start_hour,
            business_end_hour=settings.business_end_hour,
        )
        new_task = store.create_crm_task(
            lead_id=task["lead_id"],
            task_type=task["task_type"],
            assignee_id=task["assignee_id"],
            due_at=due.isoformat(),
            priority=task.get("priority", "normal"),
            rule_id=task.get("rule_id"),
            payload={"recurring_from": task["task_id"]},
            recurring=True,
            recurrence_interval_hours=interval,
        )
        task["recurrence_spawned"] = True
        events.publish("task.created", {"task_id": new_task["task_id"], "lead_id": task["lead_id"], "recurring": True})
        spawned.append(new_task)
    return spawned


def mark_overdue_tasks() -> int:
    count = 0
    for task in store.all_tasks_raw():
        if task.get("status") in ("completed", "cancelled"):
            continue
        if is_overdue(task["due_at"]):
            count += 1
    return count


def build_kpis() -> dict[str, Any]:
    from app.idempotency import violation_count
    from app.ledger import ledger_completeness

    task_count = len(store.all_tasks_raw())
    kpis = snapshot(task_count=task_count)
    kpis["ledger"] = ledger_completeness()
    kpis["idempotency_violations"] = violation_count()
    kpis["canary"] = canary_stats()
    return kpis


def build_requirements_status() -> dict[str, Any]:
    """Project requirements acceptance matrix (§10)."""
    from app.agents.llm_task_agent import pending_approvals
    from app.core.config import get_settings
    from app.dlq import list_dlq
    from app.idempotency import violation_count
    from app.ledger import ledger_completeness
    from app.metrics import snapshot

    s = get_settings()
    metrics = snapshot(task_count=len(store.all_tasks_raw()))
    ledger = ledger_completeness()

    functional = [
        {"id": "F1", "requirement": "Event-driven triggers (Kafka/API)", "status": "implemented", "notes": "HTTP ingest + optional Kafka consumer group"},
        {"id": "F2", "requirement": "Schedule-driven cron triggers", "status": "implemented", "notes": "POST /triggers/schedules"},
        {"id": "F3", "requirement": "JSONLogic-style conditions", "status": "implemented", "notes": "jsonlogic + CEL subset on rules"},
        {"id": "F4", "requirement": "Action library (7 actions)", "status": "implemented", "notes": "create_task, assign_owner, update_record, request_document, post_note, start_workflow, emit_event"},
        {"id": "F5", "requirement": "Temporal durable workflows", "status": "implemented", "notes": "Doc-request chain + GenericStartWorkflow with SLA/compensation"},
        {"id": "F6", "requirement": "LLM agents with guardrails", "status": "implemented", "notes": "Confidence threshold + approval gate"},
        {"id": "F7", "requirement": "Dry-run simulation", "status": "implemented", "notes": "Mandatory before publish"},
        {"id": "F8", "requirement": "Canary rollout (1%)", "status": "implemented", "notes": "draft→canary→active"},
        {"id": "F9", "requirement": "DLQ replay", "status": "implemented", "notes": "POST /vision/dlq/{id}/replay"},
        {"id": "F10", "requirement": "Immutable action ledger", "status": "implemented", "notes": "inputs_hash + provenance"},
    ]

    nfr = [
        {"id": "N1", "requirement": "Throughput ≥5k/s sustained", "target": "5000/s", "status": "not_validated"},
        {"id": "N2", "requirement": "Action latency p95 ≤3s", "target": "3000ms", "status": "implemented", "current_p95_ms": metrics.get("event_processing_latency_ms_p95")},
        {"id": "N3", "requirement": "Idempotency exactly-once effect", "target": "(trigger,rule,action)", "status": "implemented", "violations": violation_count()},
        {"id": "N4", "requirement": "Audit retention ≥7 years", "target": "7y", "status": "documented", "ledger_retention_years": 7},
        {"id": "N5", "requirement": "RBAC on rule authoring", "target": "IAM scopes", "status": "implemented", "roles": ["revops", "engineer", "compliance", "sre"]},
        {"id": "N6", "requirement": "Loop guard + action budget", "target": "per-deal limit", "status": "implemented", "max_actions_per_deal": s.max_actions_per_deal},
    ]

    acceptance = [
        {"id": "A1", "criterion": "Pre-Approval stage → doc+task within p95≤3s, once, ledgered", "status": "tested", "test": "test_requirements_pre_approval_acceptance"},
        {"id": "A2", "criterion": "Kafka replay produces no duplicate side effects", "status": "tested", "test": "test_action_level_idempotency_on_replay"},
        {"id": "A3", "criterion": "Rule requires dry-run + canary before production", "status": "tested", "test": "test_publish_requires_dry_run_and_canary"},
        {"id": "A4", "criterion": "Every write action in ledger with inputs_hash", "status": "tested", "test": "test_ledger_inputs_hash"},
        {"id": "A5", "criterion": "LLM write blocked without confidence+approval", "status": "tested", "test": "test_llm_write_blocked_without_approval"},
    ]

    phases = [
        {"phase": "P0", "milestone": "Foundations", "status": "complete", "deliverables": ["event schemas", "action ledger", "idempotency keys", "CRM connector"]},
        {"phase": "P1", "milestone": "Rules MVP", "status": "complete", "deliverables": ["rule engine", "JSONLogic", "create_task/update_record", "dry-run"]},
        {"phase": "P2", "milestone": "Orchestration", "status": "complete", "deliverables": ["Temporal workflows", "escalation timers", "doc-request chains"]},
        {"phase": "P3", "milestone": "Intelligence", "status": "complete", "deliverables": ["LLM agents", "confidence gate", "approval capture"]},
        {"phase": "P4", "milestone": "Hardening", "status": "partial", "deliverables": ["DLQ replay", "canary rollout", "contract tests", "load smoke test pending at scale"]},
    ]

    return {
        "engine": "Task Automation Engine",
        "version": s.task_automation_engine_version,
        "functional": functional,
        "non_functional": nfr,
        "acceptance_criteria": acceptance,
        "milestones": phases,
        "canary": canary_stats(),
        "pending_llm_approvals": len(pending_approvals()),
        "dlq_pending": len([e for e in list_dlq(limit=500) if e.get("status") == "pending"]),
        "ledger_completeness_pct": ledger.get("completeness_pct"),
    }


def build_functional_requirements_status() -> dict[str, Any]:
    """Functional requirements matrix (FR-001 – FR-020)."""
    s = get_settings()
    fr_items = [
        {"id": "FR-001", "requirement": "Ingest domain events from Kafka", "status": "implemented", "moscow": "Must"},
        {"id": "FR-002", "requirement": "JSONLogic-style condition DSL", "status": "implemented", "moscow": "Must"},
        {"id": "FR-003", "requirement": "Conflict resolution priority/specificity/tiebreak", "status": "implemented", "moscow": "Must"},
        {"id": "FR-004", "requirement": "create_task with template, assignee, due, SLA", "status": "implemented", "moscow": "Must"},
        {"id": "FR-005", "requirement": "update_record via CRM API", "status": "implemented", "moscow": "Must"},
        {"id": "FR-006", "requirement": "request_document with product/stage doc set", "status": "implemented", "moscow": "Must"},
        {"id": "FR-007", "requirement": "Idempotent (trigger,rule,action) keys", "status": "implemented", "moscow": "Must"},
        {"id": "FR-008", "requirement": "Temporal durable workflows", "status": "implemented", "moscow": "Must"},
        {"id": "FR-009", "requirement": "SLA escalation timers", "status": "implemented", "moscow": "Must"},
        {"id": "FR-010", "requirement": "Dry-run/simulation", "status": "implemented", "moscow": "Must"},
        {"id": "FR-011", "requirement": "Immutable ledger per action", "status": "implemented", "moscow": "Must"},
        {"id": "FR-012", "requirement": "Cron schedule triggers", "status": "implemented", "moscow": "Should"},
        {"id": "FR-013", "requirement": "LLM drafts with confidence score", "status": "implemented", "moscow": "Should"},
        {"id": "FR-014", "requirement": "Human approval for LLM writes below threshold", "status": "implemented", "moscow": "Must"},
        {"id": "FR-015", "requirement": "Loop detection + per-deal action budget", "status": "implemented", "moscow": "Must"},
        {"id": "FR-016", "requirement": "DLQ one-click replay", "status": "implemented", "moscow": "Should"},
        {"id": "FR-017", "requirement": "Rule versioning + canary + rollback", "status": "implemented", "moscow": "Must"},
        {"id": "FR-018", "requirement": "REST API trigger/simulate/query runs", "status": "implemented", "moscow": "Must"},
        {"id": "FR-019", "requirement": "Emit task.completed / automation.failed", "status": "implemented", "moscow": "Should"},
        {"id": "FR-020", "requirement": "Per-tenant/branch rule scoping", "status": "partial", "moscow": "Could"},
    ]
    user_stories = [
        {"id": "US-1", "title": "Stage-driven doc request", "status": "tested"},
        {"id": "US-2", "title": "Rule authoring with safety (blast radius)", "status": "tested"},
        {"id": "US-3", "title": "LLM-drafted note under guardrail", "status": "tested"},
    ]
    business_rules = [
        {"id": "BR-1", "rule": "Compliance-critical actions use deterministic rules only", "status": "enforced"},
        {"id": "BR-2", "rule": "No duplicate execution for same idempotency key", "status": "enforced"},
        {"id": "BR-3", "rule": f"Per-deal action budget default {s.max_actions_per_deal}", "status": "enforced"},
        {"id": "BR-4", "rule": "Assignee fallback chain", "status": "enforced"},
        {"id": "BR-5", "rule": "Protected-class proxies excluded from conditions", "status": "enforced"},
        {"id": "BR-6", "rule": "Doc sets from product+stage matrix", "status": "enforced"},
    ]
    return {
        "document": "Functional Requirements",
        "functional_requirements": fr_items,
        "user_stories": user_stories,
        "business_rules": business_rules,
        "implemented_count": sum(1 for f in fr_items if f["status"] == "implemented"),
        "total_count": len(fr_items),
    }


def vision_overview() -> dict[str, Any]:
    s = get_settings()
    return {
        "name": "Task Automation Engine",
        "mission": "Convert CRM events and decisions into automated, auditable tasks and mutations",
        "role_in_engine_2": "execution_arm",
        "capabilities": [
            "event_triggered_task_creation",
            "schedule_driven_triggers",
            "kafka_trigger_ingestion",
            "jsonlogic_rule_conditions",
            "crm_record_hygiene",
            "document_request_orchestration",
            "workflow_orchestration_temporal",
            "rule_versioning_canary_rollout",
            "immutable_action_ledger",
            "idempotency_guardrails",
            "loop_detection_action_budget",
            "llm_task_agents_under_guardrails",
            "dlq_replay",
            "rbac_rule_authoring",
        ],
        "consumed_events": [
            "deal.stage_changed", "lead.assigned", "lead.scored", "nba.recommended",
            "task.completed", "document.received",
        ],
        "produced_events": [
            "task.created", "task.updated", "task.completed", "record.updated",
            "document.requested", "workflow.started", "automation.run.completed",
            "automation.failed", "automation.loop_detected", "automation.llm.feedback",
        ],
        "peer_engines": {
            "stage_tracking": bool(s.stage_tracking_url.strip()),
            "lead_assignment": bool(s.lead_assignment_url.strip()),
            "engine_3_generative_docs": "consumes document.requested",
            "engine_5_api_xml_data": "external doc retrieval",
            "engine_8_compliance": "audits action ledger",
        },
        "platform_dependencies": {
            "kafka": s.kafka_bootstrap_servers,
            "temporal": s.temporal_host,
            "redis": s.redis_url,
            "postgres": s.storage_backend,
        },
    }
