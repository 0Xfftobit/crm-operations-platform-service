"""System design API — components, schemas, configuration (§3)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.design import build_system_design
from app.rbac import Principal, require_scope
from app.workflows.doc_request_chain import get_workflow, list_workflows

router = APIRouter(tags=["system-design"])
ReadAuth = Annotated[Principal, Depends(require_scope("vision.read"))]


@router.get("/system-design", summary="System design overview (§1–§9)")
def system_design(_: ReadAuth) -> dict[str, Any]:
    return build_system_design()


@router.get("/system-design/workflows", summary="Active doc-request workflow instances")
def workflow_instances(_: ReadAuth, deal_id: str | None = None) -> dict[str, Any]:
    return {"workflows": list_workflows(deal_id=deal_id)}


@router.get("/system-design/workflows/{workflow_id}", summary="Workflow state detail")
def workflow_detail(workflow_id: str, _: ReadAuth) -> dict[str, Any]:
    wf = get_workflow(workflow_id)
    if not wf:
        return {"error": "workflow_not_found"}
    return wf
