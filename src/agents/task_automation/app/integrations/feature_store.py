"""Feature Store client — deal/borrower enrichment features (§7)."""
from __future__ import annotations

from typing import Any

# Stub feature catalog keyed by lead/deal
_feature_cache: dict[str, dict[str, Any]] = {}


def reset_feature_store() -> None:
    _feature_cache.clear()


def seed_features(deal_id: str, features: dict[str, Any]) -> None:
    _feature_cache[deal_id] = features


def fetch_features(
    *,
    deal_id: str,
    lead_id: str | None = None,
    product: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    """Resolve features from Feature Store (stub) + inline context."""
    key = deal_id or lead_id or "unknown"
    cached = dict(_feature_cache.get(key, {}))
    if product and "product" not in cached:
        cached["product"] = product
    if stage and "stage" not in cached:
        cached["stage"] = stage
    # Architecture §7 feature examples
    defaults = {
        "loan_amount": cached.get("loan_amount"),
        "stage_age_hours": cached.get("stage_age_hours", 0),
        "missing_doc_flags": cached.get("missing_doc_flags", []),
        "product_type": cached.get("product_type") or product,
    }
    return {**defaults, **cached}


def store_stats() -> dict[str, Any]:
    return {"cached_entities": len(_feature_cache), "backend": "in_process_feature_store"}
