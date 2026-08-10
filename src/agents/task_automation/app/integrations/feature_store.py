"""Feature Store client — deal/borrower enrichment features (§7)."""
from __future__ import annotations

import logging
from threading import RLock
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Stub feature catalog keyed by lead/deal
_feature_cache: dict[str, dict[str, Any]] = {}

_lock = RLock()


def reset_feature_store() -> None:
    with _lock:
        _feature_cache.clear()


def seed_features(deal_id: str, features: dict[str, Any]) -> None:
    with _lock:
        _feature_cache[deal_id] = features


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.feature_store_url and settings.feature_store_api_key:
        headers["X-API-Key"] = settings.feature_store_api_key
    return headers


def fetch_features(
    *,
    deal_id: str,
    lead_id: str | None = None,
    product: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    """Resolve features from Feature Store HTTP service when configured, else stub + inline context."""
    settings = get_settings()
    key = deal_id or lead_id or "unknown"
    with _lock:
        cached = dict(_feature_cache.get(key, {}))

    if settings.feature_store_url:
        try:
            with httpx.Client(timeout=settings.integration_timeout_s) as client:
                resp = client.get(
                    f"{settings.feature_store_url}/features/{deal_id}",
                    headers=_headers(),
                    params={"lead_id": lead_id or "", "product": product or "", "stage": stage or ""},
                )
            if resp.is_success:
                data = resp.json()
                data.setdefault("_provider", "feature_store_http")
                return data
            logger.warning("feature_store_fetch_failed", extra={"status": resp.status_code, "body": resp.text})
        except Exception as exc:  # noqa: BLE001
            logger.warning("feature_store_fetch_error", extra={"error": str(exc)})

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
    return {**defaults, **cached, "_provider": "in_process_feature_store"}


def store_stats() -> dict[str, Any]:
    with _lock:
        return {"cached_entities": len(_feature_cache), "backend": "in_process_feature_store"}
