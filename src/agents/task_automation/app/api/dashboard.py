"""Agent and manager dashboards."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import require_api_key
from app.service import agent_queue, manager_overview

router = APIRouter(tags=["dashboard"])
Auth = Annotated[str, Depends(require_api_key)]


@router.get("/dashboard/agent/{assignee_id}", summary="Agent task queue sorted by urgency")
def agent_dashboard(assignee_id: str, _: Auth) -> dict[str, Any]:
    return agent_queue(assignee_id)


@router.get("/dashboard/manager", summary="Manager view — task status across all agents")
def manager_dashboard(_: Auth) -> dict[str, Any]:
    return manager_overview()
