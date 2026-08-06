"""CRM task API — queue, completion, manual create."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app import events, store
from app.api.deps import require_api_key
from app.business_hours import calculate_due_at
from app.core.config import get_settings
from app.schemas.tasks import CompleteTaskRequest, CreateManualTaskRequest

router = APIRouter(tags=["crm-tasks"])
Auth = Annotated[str, Depends(require_api_key)]


@router.get("/tasks/system-info", summary="Engine metadata")
def system_info(_: Auth) -> dict[str, Any]:
    s = get_settings()
    return {
        "capability_id": "task_automation",
        "engine_version": s.task_automation_engine_version,
        "scheduler_enabled": s.scheduler_enabled,
        "business_hours": {
            "timezone": s.business_timezone,
            "start_hour": s.business_start_hour,
            "end_hour": s.business_end_hour,
        },
        "task_types": ["call", "whatsapp", "send_email", "send_document", "schedule_meeting"],
        "storage_backend": s.storage_backend,
        "integrations": {
            "stage_tracking": bool(s.stage_tracking_url.strip()),
            "lead_assignment": bool(s.lead_assignment_url.strip()),
        },
    }


@router.get("/tasks", summary="List CRM tasks (filterable)")
def list_tasks(
    _: Auth,
    assignee_id: str | None = None,
    lead_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return {"tasks": store.list_tasks(assignee_id=assignee_id, lead_id=lead_id, status=status, limit=limit)}


@router.post("/tasks", summary="Create manual CRM task", status_code=201)
def create_manual_task(body: CreateManualTaskRequest, _: Auth) -> dict[str, Any]:
    s = get_settings()
    due_at = body.due_at
    if not due_at:
        due = calculate_due_at(
            delay_hours=0,
            timezone=s.business_timezone,
            business_start_hour=s.business_start_hour,
            business_end_hour=s.business_end_hour,
        )
        due_at = due.isoformat()
    return store.create_crm_task(
        lead_id=body.lead_id,
        task_type=body.task_type,
        assignee_id=body.assignee_id,
        due_at=due_at,
        priority=body.priority,
        title=body.title,
        payload=body.payload,
    )


@router.get("/tasks/{task_id}", summary="Get CRM task")
def get_task(task_id: str, _: Auth) -> dict[str, Any]:
    row = store.get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    return row


@router.post("/tasks/{task_id}/complete", summary="Complete task with outcome notes")
def complete_task(task_id: str, body: CompleteTaskRequest, _: Auth) -> dict[str, Any]:
    row = store.complete_task(
        task_id,
        outcome_notes=body.outcome_notes,
        outcome=body.outcome,
    )
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    events.publish("task.completed", {
        "task_id": task_id,
        "lead_id": row["lead_id"],
        "assignee_id": row.get("assignee_id"),
        "outcome": body.outcome,
    })
    return row
