"""Enumerations for task automation."""
from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    CALL = "call"
    WHATSAPP = "whatsapp"
    SEND_EMAIL = "send_email"
    SEND_DOCUMENT = "send_document"
    SCHEDULE_MEETING = "schedule_meeting"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"


class AssigneeStrategy(StrEnum):
    LEAD_OWNER = "lead_owner"
    PROCESSOR = "processor"
    SPECIFIC_AGENT = "specific_agent"
    ROUND_ROBIN = "round_robin"
    BRANCH_ROUND_ROBIN = "branch_round_robin"
    QUEUE = "queue"


class RuleActionType(StrEnum):
    CREATE_TASK = "create_task"
    ASSIGN_OWNER = "assign_owner"
    UPDATE_RECORD = "update_record"
    REQUEST_DOCUMENT = "request_document"
    POST_NOTE = "post_note"
    START_WORKFLOW = "start_workflow"
    EMIT_EVENT = "emit_event"
    COMPOUND = "compound"


class RolloutStatus(StrEnum):
    DRAFT = "draft"
    CANARY = "canary"
    ACTIVE = "active"
    ARCHIVED = "archived"


PRIORITY_RANK: dict[str, int] = {
    "urgent": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}

TASK_TYPES = [t.value for t in TaskType]
TRIGGER_EVENTS = [
    "deal.stage_changed",
    "lead.assigned",
    "lead.created",
    "lead.scored",
    "nba.recommended",
    "task.completed",
    "document.received",
    "manual",
]

EMITTED_EVENTS = [
    "task.created",
    "task.updated",
    "task.completed",
    "record.updated",
    "document.requested",
    "workflow.started",
    "automation.run.completed",
    "automation.failed",
    "automation.loop_detected",
    "automation.llm.feedback",
    "note.posted",
]

CONSUMED_EVENTS = TRIGGER_EVENTS

# Pre-approval doc sets per product/stage (vision §5 doc-request accuracy)
DOC_SETS: dict[str, dict[str, list[str]]] = {
    "CONVENTIONAL_30YR": {
        "pre_approval": ["pay_stub", "w2", "bank_statement", "credit_auth"],
        "underwriting": ["appraisal_order", "title_commitment", "hoi_binder"],
        "processing": ["employment_verification", "asset_verification"],
    },
    "default": {
        "pre_approval": ["pay_stub", "credit_auth", "id_verification"],
        "underwriting": ["appraisal_order"],
    },
}
