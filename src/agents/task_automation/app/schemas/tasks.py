"""CRM task schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.enums import TaskPriority, TaskType


class CompleteTaskRequest(BaseModel):
    outcome_notes: str = Field(..., min_length=1, max_length=2000)
    outcome: str | None = Field(None, description="connected | no_answer | scheduled | sent | etc.")


class CreateManualTaskRequest(BaseModel):
    lead_id: str
    task_type: TaskType
    title: str | None = None
    assignee_id: str
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventIngestRequest(BaseModel):
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CRMTaskResponse(BaseModel):
    task_id: str
    lead_id: str
    task_type: str
    title: str
    assignee_id: str
    priority: str
    status: str
    due_at: str
    overdue: bool
    alert_level: str | None
    rule_id: str | None
    created_at: str
    completed_at: str | None
    outcome_notes: str | None
    calendar_event_id: str | None = None
