"""Functional requirements status API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.rbac import Principal, require_scope
from app.service import build_functional_requirements_status

router = APIRouter(tags=["functional-requirements"])
ReadAuth = Annotated[Principal, Depends(require_scope("vision.read"))]


@router.get("/functional-requirements", summary="Functional requirements matrix FR-001–FR-020")
def functional_requirements(_: ReadAuth) -> dict[str, Any]:
    return build_functional_requirements_status()
