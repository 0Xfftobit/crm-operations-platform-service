"""Action executor — rules → tasks, record updates, doc requests, workflows."""
from __future__ import annotations

from typing import Any

from app.agents.llm_task_agent import draft_task_notes, llm_write_permitted
from app.assignment import next_round_robin_index, resolve_assignee
from app.business_hours import calculate_due_at
from app.conflicts import has_recent_duplicate
from app.core.config import get_settings
from app.engine.conflict_resolver import resolve_action_conflicts, sort_rules_for_evaluation
from app.engine.rule_engine import rule_conditions_match
from app.enrichment import condition_references_protected_proxy
from app.guards import (
    canary_eligible,
    check_action_budget,
    check_loop_guard,
    record_actions,
    record_canary_exposure,
)
from app.idempotency import check_action_idempotency, record_action_idempotency
from app.integrations.activity_runner import ConnectorActivityError, execute_connector_activity
from app.integrations.documents import resolve_document_types
from app.integrations.temporal import start_workflow
from app.schemas.enums import RuleActionType, TaskType


def _resolve_actions(rule: dict[str, Any]) -> list[str | dict[str, Any]]:
    design_actions = rule.get("actions")
    if design_actions:
        return design_actions
    action_type = rule.get("action_type") or RuleActionType.CREATE_TASK
    if action_type == RuleActionType.COMPOUND:
        return [
            RuleActionType.CREATE_TASK,
            RuleActionType.UPDATE_RECORD,
            RuleActionType.REQUEST_DOCUMENT,
        ]
    return [action_type]


def _sort_rules_by_priority(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_rules_for_evaluation(rules)


def _action_slot(rule: dict[str, Any]) -> str:
    action_type = rule.get("action_type") or RuleActionType.CREATE_TASK
    task_type = rule.get("task_type") or ""
    return f"{action_type}:{task_type}"


def _filter_matching_rules(
    *,
    rules: list[dict[str, Any]],
    payload: dict[str, Any],
    lead_id: str,
    trigger_id: str,
    include_draft: bool,
    skipped: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matching: list[dict[str, Any]] = []
    for rule in sort_rules_for_evaluation(rules):
        rule_id = rule["rule_id"]
        rollout = rule.get("rollout_status", "active")

        if rollout == "draft" and not include_draft:
            skipped.append({"rule_id": rule_id, "reason": "rule_draft_not_active"})
            continue
        if rollout == "archived":
            skipped.append({"rule_id": rule_id, "reason": "rule_archived"})
            continue
        if not rule.get("active", True):
            skipped.append({"rule_id": rule_id, "reason": "rule_inactive"})
            continue
        if condition_references_protected_proxy(rule):
            skipped.append({"rule_id": rule_id, "reason": "protected_class_proxy_blocked"})
            continue
        if rollout in ("active", "canary"):
            canary_ok, canary_reason = canary_eligible(rule=rule, lead_id=lead_id)
            if not canary_ok:
                skipped.append({"rule_id": rule_id, "reason": canary_reason})
                continue
        loop_ok, loop_reason = check_loop_guard(trigger_id=trigger_id, rule_id=rule_id, lead_id=lead_id)
        if not loop_ok:
            skipped.append({"rule_id": rule_id, "reason": loop_reason})
            continue
        if not rule_conditions_match(payload, rule):
            skipped.append({"rule_id": rule_id, "reason": "conditions_not_met"})
            continue
        matching.append(rule)

    slots = {r["rule_id"]: _action_slot(r) for r in matching}
    winners, conflicts = resolve_action_conflicts(matching_rules=matching, action_slots=slots)
    for c in conflicts:
        skipped.append({"rule_id": c["loser_rule_id"], "reason": "conflict_lost", "winner": c["winner_rule_id"]})
    return winners, conflicts


def evaluate_rules(
    *,
    topic: str,
    payload: dict[str, Any],
    rules: list[dict[str, Any]],
    lead_id: str,
    existing_tasks: list[dict[str, Any]],
    dry_run: bool = False,
    include_draft: bool = False,
    trigger_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate matching rules and produce planned/executed actions."""
    settings = get_settings()
    trigger_id = trigger_id or payload.get("trigger_id") or f"trg_{lead_id}"
    created: list[dict[str, Any]] = []
    record_updates: list[dict[str, Any]] = []
    document_requests: list[dict[str, Any]] = []
    workflows: list[dict[str, Any]] = []
    owner_assignments: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    emitted_custom: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    idempotent_skips: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    ok_budget, budget_reason = check_action_budget(lead_id=lead_id, increment=0)
    if not ok_budget:
        return {
            "planned": [],
            "created_tasks": [],
            "record_updates": [],
            "document_requests": [],
            "workflows": [],
            "owner_assignments": [],
            "notes": [],
            "emitted_custom_events": [],
            "skipped": [{"reason": budget_reason, "lead_id": lead_id, "loop_detected": True}],
            "conflicts": [],
            "trigger_id": trigger_id,
            "loop_detected": True,
        }

    eligible_rules, conflicts = _filter_matching_rules(
        rules=rules,
        payload=payload,
        lead_id=lead_id,
        trigger_id=trigger_id,
        include_draft=include_draft,
        skipped=skipped,
    )

    for rule in eligible_rules:
        rule_id = rule["rule_id"]
        rollout = rule.get("rollout_status", "active")
        actions_to_run = _resolve_actions(rule)
        pool_key = rule.get("assignee_pool") or rule_id
        rr_index = next_round_robin_index(pool_key=pool_key)
        assignee = resolve_assignee(
            strategy=rule.get("assignee_strategy", "lead_owner"),
            event_payload=payload,
            specific_agent=rule.get("assignee_id"),
            round_robin_pool=settings.round_robin_agents,
            round_robin_index=rr_index,
        )
        due = calculate_due_at(
            delay_hours=float(rule.get("delay_hours") or 0),
            timezone=settings.business_timezone,
            business_start_hour=settings.business_start_hour,
            business_end_hour=settings.business_end_hour,
        )

        plan_entry = {
            "rule_id": rule_id,
            "rule_version": rule.get("version", "1.0.0"),
            "action_types": actions_to_run,
            "assignee_id": assignee,
            "due_at": due.isoformat(),
            "rollout_status": rollout,
        }
        planned.append(plan_entry)

        if dry_run:
            continue

        if rollout == "canary":
            record_canary_exposure(rule_id)

        action_count = 0
        for act in actions_to_run:
            rule_for_act = dict(rule)
            if isinstance(act, dict):
                act_type = act.get("type") or act.get("action_type")
                action_key = act_type or "action"
                if act_type == "create_task":
                    rule_for_act["action_type"] = RuleActionType.CREATE_TASK
                    rule_for_act["task_type"] = act.get("template") or act.get("task_type") or rule_for_act.get("task_type")
                    rule_for_act["task_title"] = act.get("template") or rule_for_act.get("task_title")
                    if act.get("assignee"):
                        rule_for_act["assignee_strategy"] = "specific"
                        rule_for_act["assignee_id"] = act["assignee"]
                    if act.get("sla_hours") is not None:
                        rule_for_act["sla_hours"] = act["sla_hours"]
                elif act_type == "request_document":
                    rule_for_act["action_type"] = RuleActionType.REQUEST_DOCUMENT
                    if act.get("doc_set"):
                        rule_for_act["auto_document_set"] = False
                        rule_for_act["document_types"] = [act["doc_set"]]
                else:
                    rule_for_act["action_type"] = act_type
                act = rule_for_act.get("action_type") or act_type
            else:
                action_key = act if isinstance(act, str) else str(act)
            cached = check_action_idempotency(trigger_id=trigger_id, rule_id=rule_id, action_key=action_key)
            if cached:
                idempotent_skips.append({"rule_id": rule_id, "action_key": action_key, "cached": True})
                continue

            if act in (RuleActionType.CREATE_TASK, RuleActionType.COMPOUND) and rule_for_act.get("task_type"):
                task_type = rule_for_act["task_type"]
                window = float(rule_for_act.get("conflict_window_hours") or settings.duplicate_task_window_hours)
                if has_recent_duplicate(existing_tasks, lead_id=lead_id, task_type=task_type, window_hours=window):
                    skipped.append({"rule_id": rule_id, "reason": "duplicate_within_window"})
                    continue

                llm_notes = None
                if rule_for_act.get("use_llm_notes"):
                    llm_notes = draft_task_notes(task_type=task_type, lead_id=lead_id, event_payload=payload)
                    if not llm_write_permitted(llm_result=llm_notes):
                        skipped.append({"rule_id": rule_id, "reason": "llm_write_blocked_pending_approval"})
                        continue

                calendar_event_id = None
                if task_type == TaskType.SCHEDULE_MEETING:
                    try:
                        cal = execute_connector_activity(
                            "create_calendar_event",
                            {
                                "lead_id": lead_id,
                                "assignee_id": assignee,
                                "due_at": due.isoformat(),
                                "title": f"Meeting — {lead_id}",
                                "payload": payload,
                            },
                        )
                        calendar_event_id = cal["calendar_event_id"]
                    except ConnectorActivityError as exc:
                        skipped.append({"rule_id": rule_id, "reason": "connector_failed", "activity": exc.activity_name})
                        continue

                spec = {
                    "action": "create_task",
                    "action_key": action_key,
                    "lead_id": lead_id,
                    "task_type": task_type,
                    "title": rule_for_act.get("task_title"),
                    "assignee_id": assignee,
                    "due_at": due.isoformat(),
                    "priority": rule_for_act.get("priority", "normal"),
                    "rule_id": rule_id,
                    "rule_version": rule_for_act.get("version"),
                    "payload": {"trigger": topic, "trigger_id": trigger_id, **payload},
                    "calendar_event_id": calendar_event_id,
                    "llm_draft_notes": llm_notes,
                    "recurring": bool(rule_for_act.get("recurring")),
                    "recurrence_interval_hours": rule_for_act.get("recurrence_interval_hours"),
                    "sla_hours": rule_for_act.get("sla_hours"),
                }
                created.append(spec)
                action_count += 1
                record_action_idempotency(
                    trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=spec,
                )

            if act == RuleActionType.ASSIGN_OWNER:
                owner = rule_for_act.get("assignee_id") or assignee
                try:
                    mut = execute_connector_activity(
                        "assign_owner",
                        {"lead_id": lead_id, "owner_id": owner, "reason": f"rule:{rule_id}"},
                    )
                except ConnectorActivityError as exc:
                    skipped.append({"rule_id": rule_id, "reason": "connector_failed", "activity": exc.activity_name})
                    continue
                owner_assignments.append({**mut, "rule_id": rule_id, "action_key": action_key})
                action_count += 1
                record_action_idempotency(
                    trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=mut,
                )

            if act in (RuleActionType.UPDATE_RECORD, RuleActionType.COMPOUND):
                updates = rule_for_act.get("record_updates") or {}
                if updates:
                    try:
                        mut = execute_connector_activity(
                            "update_record",
                            {"lead_id": lead_id, "updates": updates, "event_payload": payload},
                        )
                    except ConnectorActivityError as exc:
                        skipped.append({"rule_id": rule_id, "reason": "connector_failed", "activity": exc.activity_name})
                        continue
                    record_updates.append({**mut, "rule_id": rule_id, "action_key": action_key})
                    action_count += 1
                    record_action_idempotency(
                        trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=mut,
                    )

            if act in (RuleActionType.REQUEST_DOCUMENT, RuleActionType.COMPOUND):
                product = str(payload.get("product") or "default")
                stage = str(payload.get("stage") or payload.get("to_stage") or "pre_approval")
                doc_types = resolve_document_types(
                    product=product,
                    stage=stage,
                    explicit=rule_for_act.get("document_types"),
                    auto_set=rule_for_act.get("auto_document_set", True),
                )
                if doc_types:
                    try:
                        req = execute_connector_activity(
                            "request_document",
                            {
                                "lead_id": lead_id,
                                "document_types": doc_types,
                                "assignee_id": assignee,
                                "rule_id": rule_id,
                                "payload": payload,
                            },
                        )
                    except ConnectorActivityError as exc:
                        skipped.append({"rule_id": rule_id, "reason": "connector_failed", "activity": exc.activity_name})
                        continue
                    document_requests.append({**req, "action_key": action_key})
                    action_count += 1
                    record_action_idempotency(
                        trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=req,
                    )

            if act == RuleActionType.POST_NOTE:
                note_body = rule_for_act.get("note_template") or f"Automated note from rule {rule_id}"
                if rule_for_act.get("use_llm_notes"):
                    llm = draft_task_notes(
                        task_type="post_note", lead_id=lead_id, event_payload=payload,
                    )
                    if not llm_write_permitted(llm_result=llm):
                        skipped.append({"rule_id": rule_id, "reason": "llm_note_blocked"})
                        continue
                    note_body = llm.get("draft_notes", note_body)
                try:
                    note = execute_connector_activity(
                        "post_note",
                        {"lead_id": lead_id, "body": note_body, "author": assignee},
                    )
                except ConnectorActivityError as exc:
                    skipped.append({"rule_id": rule_id, "reason": "connector_failed", "activity": exc.activity_name})
                    continue
                notes.append({**note, "rule_id": rule_id, "action_key": action_key})
                action_count += 1
                record_action_idempotency(
                    trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=note,
                )

            if act == RuleActionType.START_WORKFLOW and rule_for_act.get("workflow_template"):
                wf = start_workflow(
                    template=rule_for_act["workflow_template"],
                    lead_id=lead_id,
                    input_payload={"trigger": topic, "trigger_id": trigger_id, **payload},
                    rule_id=rule_id,
                    timer_hours=rule_for_act.get("sla_hours"),
                )
                workflows.append({**wf, "action_key": action_key})
                action_count += 1
                record_action_idempotency(
                    trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=wf,
                )

            if act == RuleActionType.EMIT_EVENT:
                evt_spec = {
                    "topic": rule_for_act.get("emit_topic") or "automation.custom",
                    "payload": {"lead_id": lead_id, "rule_id": rule_id, **payload},
                    "action_key": action_key,
                }
                emitted_custom.append(evt_spec)
                action_count += 1
                record_action_idempotency(
                    trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, result=evt_spec,
                )

        budget_ok, budget_reason = check_action_budget(lead_id=lead_id, increment=action_count)
        if not budget_ok:
            skipped.append({"rule_id": rule_id, "reason": budget_reason})
        else:
            record_actions(lead_id=lead_id, count=action_count)

    return {
        "planned": planned,
        "created_tasks": created,
        "record_updates": record_updates,
        "document_requests": document_requests,
        "workflows": workflows,
        "owner_assignments": owner_assignments,
        "notes": notes,
        "emitted_custom_events": emitted_custom,
        "skipped": skipped,
        "idempotent_skips": idempotent_skips,
        "conflicts": conflicts,
        "trigger_id": trigger_id,
    }
