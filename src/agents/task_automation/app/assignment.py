"""Auto-assignment — explicit owner → deal owner → branch round-robin → queue."""
from __future__ import annotations

from typing import Any

from app.schemas.enums import AssigneeStrategy


def resolve_assignee(
    *,
    strategy: str,
    event_payload: dict[str, Any],
    specific_agent: str | None = None,
    round_robin_pool: list[str] | None = None,
    round_robin_index: int = 0,
    branch_pools: dict[str, list[str]] | None = None,
) -> str:
    """BR-4: Resolve task assignee with fallback chain."""
    if strategy == AssigneeStrategy.SPECIFIC_AGENT and specific_agent:
        return specific_agent

    if strategy == AssigneeStrategy.PROCESSOR:
        processor = event_payload.get("processor_id") or event_payload.get("assigned_processor_id")
        if processor:
            return str(processor)

    owner = (
        event_payload.get("assignee_id")
        or event_payload.get("agent_id")
        or event_payload.get("assigned_lo_id")
        or event_payload.get("lo_id")
        or event_payload.get("owner_id")
    )
    if strategy == AssigneeStrategy.LEAD_OWNER and owner:
        return str(owner)

    if strategy == AssigneeStrategy.BRANCH_ROUND_ROBIN:
        branch = str(event_payload.get("branch_id") or "default")
        pool = (branch_pools or {}).get(branch) or round_robin_pool or []
        if pool:
            return pool[round_robin_index % len(pool)]

    if strategy == AssigneeStrategy.ROUND_ROBIN and round_robin_pool:
        return round_robin_pool[round_robin_index % len(round_robin_pool)]

    if strategy == AssigneeStrategy.QUEUE:
        return "ops:queue"

    return str(owner or specific_agent or "ops:queue")


def next_round_robin_index(*, pool_key: str = "default") -> int:
    from app.persistence import redis_store

    return redis_store.incr_round_robin(pool_key)
