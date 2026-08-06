"""Project requirements status matrix API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.rbac import Principal, require_scope
from app.service import build_requirements_status

router = APIRouter(tags=["requirements"])
ReadAuth = Annotated[Principal, Depends(require_scope("vision.read"))]


@router.get("/requirements", summary="Project requirements acceptance matrix")
def requirements_status(_: ReadAuth) -> dict[str, Any]:
    return build_requirements_status()
