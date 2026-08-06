"""Workflow rule schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.enums import AssigneeStrategy, RolloutStatus, RuleActionType, TaskPriority, TaskType


class RuleCondition(BaseModel):
    field: str = Field(..., description="Payload field to evaluate, e.g. stage, grade")
    operator: str = Field("eq", description="eq | ne | in | gt | lt | contains")
    value: Any = Field(...)


class RecordUpdateSpec(BaseModel):
    owner_id: str | None = None
    stage_field: str | None = None
    stage_value: str | None = None
    flags: list[str] = Field(default_factory=list, description="missing_data, sla_at_risk, etc.")


class WorkflowRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    trigger_event: str = Field(..., description="e.g. deal.stage_changed, lead.assigned")
    conditions: list[RuleCondition] = Field(default_factory=list)
    jsonlogic: dict[str, Any] | None = Field(None, description="JSONLogic expression (preferred over conditions)")
    action_type: RuleActionType = RuleActionType.CREATE_TASK
    task_type: TaskType = TaskType.CALL
    delay_hours: float = Field(0, ge=0, description="Hours after trigger before due")
    assignee_strategy: AssigneeStrategy = AssigneeStrategy.LEAD_OWNER
    assignee_id: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    recurring: bool = False
    recurrence_interval_hours: float | None = Field(None, ge=1)
    active: bool = True
    conflict_window_hours: float = Field(4, ge=0, description="Dedupe window for same lead+type")
    version: str = Field("1.0.0", description="Semantic rule version for audit/rollout")
    rollout_status: RolloutStatus = RolloutStatus.ACTIVE
    canary_percent: float | None = Field(None, ge=0, le=100, description="Traffic % during canary rollout")
    record_updates: RecordUpdateSpec | None = None
    document_types: list[str] = Field(default_factory=list, description="Explicit doc types or auto from product/stage")
    auto_document_set: bool = Field(True, description="Resolve doc set from product+stage when document_types empty")
    workflow_template: str | None = Field(None, description="Temporal workflow template id")
    use_llm_notes: bool = Field(False, description="LLM drafts task notes under guardrails")
    note_template: str | None = Field(None, description="Template for post_note action")
    emit_topic: str | None = Field(None, description="Topic for emit_event action")
    sla_hours: float | None = Field(None, ge=0, description="SLA window for escalation")
    task_title: str | None = Field(None, description="Override default task title template")
    tenant_id: str | None = Field(None, description="Per-tenant rule scope (FR-020)")
    branch_id: str | None = Field(None, description="Per-branch rule scope")
    feature_flag: str | None = Field(None, description="Feature-flagged enablement key")


class WorkflowRuleUpdate(BaseModel):
    name: str | None = None
    conditions: list[RuleCondition] | None = None
    jsonlogic: dict[str, Any] | None = None
    action_type: RuleActionType | None = None
    task_type: TaskType | None = None
    delay_hours: float | None = None
    assignee_strategy: AssigneeStrategy | None = None
    assignee_id: str | None = None
    priority: TaskPriority | None = None
    recurring: bool | None = None
    recurrence_interval_hours: float | None = None
    active: bool | None = None
    conflict_window_hours: float | None = None
    version: str | None = None
    rollout_status: RolloutStatus | None = None
    canary_percent: float | None = None
    record_updates: RecordUpdateSpec | None = None
    document_types: list[str] | None = None
    auto_document_set: bool | None = None
    workflow_template: str | None = None
    use_llm_notes: bool | None = None
    note_template: str | None = None
    emit_topic: str | None = None
    sla_hours: float | None = None
    task_title: str | None = None
    tenant_id: str | None = None
    branch_id: str | None = None
    feature_flag: str | None = None


class SimulateRuleRequest(BaseModel):
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
