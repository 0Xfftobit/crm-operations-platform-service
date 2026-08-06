"""Temporal activity definitions — connector calls (§1 Action Executor)."""
from __future__ import annotations

from typing import Any

from temporalio import activity

from app.integrations.calendar import create_calendar_event
from app.integrations.crm import apply_record_updates, assign_owner, post_note
from app.integrations.documents import request_documents


@activity.defn(name="assign_owner")
async def assign_owner_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = assign_owner(
        lead_id=payload["lead_id"],
        owner_id=payload["owner_id"],
        reason=payload.get("reason"),
    )
    return {**result, "activity": "assign_owner"}


@activity.defn(name="update_record")
async def update_record_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = apply_record_updates(
        lead_id=payload["lead_id"],
        updates=payload["updates"],
        event_payload=payload.get("event_payload") or {},
    )
    return {**result, "activity": "update_record"}


@activity.defn(name="post_note")
async def post_note_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = post_note(
        lead_id=payload["lead_id"],
        body=payload["body"],
        author=payload.get("author", "system"),
        visibility=payload.get("visibility", "internal"),
    )
    return {**result, "activity": "post_note"}


@activity.defn(name="request_document")
async def request_document_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = request_documents(
        lead_id=payload["lead_id"],
        document_types=payload["document_types"],
        assignee_id=payload["assignee_id"],
        rule_id=payload.get("rule_id"),
        payload=payload.get("payload"),
    )
    return {**result, "activity": "request_document"}


@activity.defn(name="create_calendar_event")
async def create_calendar_event_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = create_calendar_event(
        lead_id=payload["lead_id"],
        assignee_id=payload["assignee_id"],
        due_at=payload["due_at"],
        title=payload["title"],
        payload=payload.get("payload"),
    )
    return {**result, "activity": "create_calendar_event"}


CONNECTOR_ACTIVITIES = [
    assign_owner_activity,
    update_record_activity,
    post_note_activity,
    request_document_activity,
    create_calendar_event_activity,
]

ACTIVITY_NAMES = {
    "assign_owner": assign_owner_activity,
    "update_record": update_record_activity,
    "post_note": post_note_activity,
    "request_document": request_document_activity,
    "create_calendar_event": create_calendar_event_activity,
}
