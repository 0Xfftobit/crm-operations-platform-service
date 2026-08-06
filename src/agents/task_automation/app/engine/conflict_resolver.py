"""Rule conflict resolution — priority, specificity, deterministic tiebreak."""
from __future__ import annotations

from typing import Any

from app.schemas.enums import PRIORITY_RANK


def rule_specificity(rule: dict[str, Any]) -> int:
    """More conditions = more specific."""
    logic = rule.get("jsonlogic")
    if logic:
        return _logic_depth(logic)
    return len(rule.get("conditions") or [])


def _logic_depth(node: Any) -> int:
    if not isinstance(node, dict):
        return 0
    op, args = next(iter(node.items()))
    if op in ("and", "or") and isinstance(args, list):
        return sum(_logic_depth(a) for a in args) + len(args)
    return 1


def sort_rules_for_evaluation(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FR-003: priority (urgent first), then specificity, then rule_id."""
    return sorted(
        rules,
        key=lambda r: (
            PRIORITY_RANK.get(r.get("priority", "normal"), 2),
            -rule_specificity(r),
            r.get("rule_id", ""),
        ),
    )


def resolve_action_conflicts(
    *,
    matching_rules: list[dict[str, Any]],
    action_slots: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    When multiple rules target the same action slot, highest-ranked wins.
    Returns (winners, conflict_log).
    """
    sorted_rules = sort_rules_for_evaluation(matching_rules)
    winners: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    claimed: dict[str, str] = {}

    for rule in sorted_rules:
        rule_id = rule["rule_id"]
        action_type = rule.get("action_type", "create_task")
        slot = action_slots.get(rule_id, action_type)
        if slot in claimed:
            conflicts.append({
                "slot": slot,
                "winner_rule_id": claimed[slot],
                "loser_rule_id": rule_id,
                "resolution": "priority_specificity_tiebreak",
            })
            continue
        claimed[slot] = rule_id
        winners.append(rule)

    return winners, conflicts
