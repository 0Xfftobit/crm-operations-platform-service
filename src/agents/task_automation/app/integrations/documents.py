"""Document request orchestration — Engine 3 / Engine 5."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.schemas.enums import DOC_SETS


def resolve_document_types(
    *,
    product: str,
    stage: str,
    explicit: list[str] | None = None,
    auto_set: bool = True,
) -> list[str]:
    if explicit:
        return list(explicit)
    if not auto_set:
        return []
    product_sets = DOC_SETS.get(product) or DOC_SETS["default"]
    stage_key = stage.lower().replace(" ", "_").replace("-", "_")
    for key, docs in product_sets.items():
        if key in stage_key or stage_key in key:
            return list(docs)
    return list(product_sets.get("pre_approval") or [])


def request_documents(
    *,
    lead_id: str,
    document_types: list[str],
    assignee_id: str,
    rule_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": f"doc_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "document_types": document_types,
        "assignee_id": assignee_id,
        "rule_id": rule_id,
        "target_engines": ["engine_3_generative_docs", "engine_5"],
        "status": "requested",
        "requested_at": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }
