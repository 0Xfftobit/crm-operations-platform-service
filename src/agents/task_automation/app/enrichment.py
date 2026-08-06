"""Feature enrichment for rule conditions — fail-safe on missing features."""
from __future__ import annotations

from typing import Any

PROTECTED_CLASS_PROXIES = frozenset({
    "race", "ethnicity", "gender", "sex", "religion", "national_origin",
    "marital_status", "age", "disability", "veteran_status", "zip_income_proxy",
})


def enrich_payload(*, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge event payload with enrichment features (feature store stub)."""
    enriched = dict(payload)
    features = payload.get("features") or {}

    if "product" not in enriched and payload.get("loan_product"):
        enriched["product"] = payload["loan_product"]
    if "stage" not in enriched:
        enriched["stage"] = payload.get("to_stage") or payload.get("new_stage") or payload.get("stage")

    enriched["_enriched"] = True
    enriched["_features_available"] = list(features.keys()) if features else []
    for k, v in features.items():
        if k not in PROTECTED_CLASS_PROXIES:
            enriched[k] = v
    return enriched


def condition_references_protected_proxy(rule: dict[str, Any]) -> bool:
    """BR-5: block conditions referencing protected-class proxies."""
    for c in rule.get("conditions") or []:
        if c.get("field", "").lower() in PROTECTED_CLASS_PROXIES:
            return True
    logic = rule.get("jsonlogic")
    if logic and _logic_refs_protected(logic):
        return True
    return False


def _logic_refs_protected(node: Any) -> bool:
    if isinstance(node, dict):
        for op, args in node.items():
            if op == "var":
                key = args if isinstance(args, str) else (args[0] if args else "")
                if str(key).lower() in PROTECTED_CLASS_PROXIES:
                    return True
            elif isinstance(args, list):
                if any(_logic_refs_protected(a) for a in args):
                    return True
            elif _logic_refs_protected(args):
                return True
    return False
