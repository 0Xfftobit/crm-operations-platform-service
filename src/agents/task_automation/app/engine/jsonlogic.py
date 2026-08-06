"""JSONLogic-style condition evaluation for workflow rules."""
from __future__ import annotations

from typing import Any

from app.engine.cel_eval import cel_match


def _resolve_var(payload: dict[str, Any], ref: str) -> Any:
    if ref == "stage" and payload.get("stage") is None:
        return payload.get("to_stage") or payload.get("new_stage")
    return payload.get(ref)


def _eval_jsonlogic(node: Any, payload: dict[str, Any]) -> Any:
    if node is None or not isinstance(node, dict):
        return node
    if len(node) != 1:
        return False
    op, args = next(iter(node.items()))
    if op == "var":
        key = args if isinstance(args, str) else args[0]
        return _resolve_var(payload, key)
    if not isinstance(args, list):
        args = [args]
    if op == "and":
        return all(bool(_eval_jsonlogic(a, payload)) for a in args)
    if op == "or":
        return any(bool(_eval_jsonlogic(a, payload)) for a in args)
    if op == "!":
        return not bool(_eval_jsonlogic(args[0], payload))
    if op in ("==", "eq"):
        return _eval_jsonlogic(args[0], payload) == _eval_jsonlogic(args[1], payload)
    if op in ("!=", "ne"):
        return _eval_jsonlogic(args[0], payload) != _eval_jsonlogic(args[1], payload)
    if op == ">":
        a, b = _eval_jsonlogic(args[0], payload), _eval_jsonlogic(args[1], payload)
        return a is not None and b is not None and a > b
    if op == ">=":
        a, b = _eval_jsonlogic(args[0], payload), _eval_jsonlogic(args[1], payload)
        return a is not None and b is not None and a >= b
    if op == "<":
        a, b = _eval_jsonlogic(args[0], payload), _eval_jsonlogic(args[1], payload)
        return a is not None and b is not None and a < b
    if op == "<=":
        a, b = _eval_jsonlogic(args[0], payload), _eval_jsonlogic(args[1], payload)
        return a is not None and b is not None and a <= b
    if op == "in":
        needle = _eval_jsonlogic(args[0], payload)
        hay = _eval_jsonlogic(args[1], payload)
        return needle in hay if isinstance(hay, (list, tuple, str)) else False
    if op == "contains":
        hay = str(_eval_jsonlogic(args[0], payload) or "")
        needle = str(_eval_jsonlogic(args[1], payload) or "")
        return needle.lower() in hay.lower()
    return False


def jsonlogic_match(payload: dict[str, Any], logic: dict[str, Any] | None) -> bool:
    if not logic:
        return True
    return bool(_eval_jsonlogic(logic, payload))


def conditions_match(payload: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    """Legacy field/operator conditions (AND). Empty = match."""
    if not conditions:
        return True
    for c in conditions:
        field = c.get("field", "")
        op = c.get("operator", "eq")
        expected = c.get("value")
        actual = _resolve_var(payload, field)
        if op == "eq" and actual != expected:
            return False
        if op == "ne" and actual == expected:
            return False
        if op == "in" and actual not in (expected if isinstance(expected, list) else [expected]):
            return False
        if op == "gt" and not (actual is not None and expected is not None and actual > expected):
            return False
        if op == "lt" and not (actual is not None and expected is not None and actual < expected):
            return False
        if op == "contains" and not (expected is not None and str(expected).lower() in str(actual or "").lower()):
            return False
    return True


def rule_conditions_match(payload: dict[str, Any], rule: dict[str, Any]) -> bool:
    """Evaluate cel → jsonlogic → legacy conditions."""
    cel_expr = rule.get("cel")
    if cel_expr:
        return cel_match(payload, cel_expr)
    logic = rule.get("jsonlogic")
    if logic:
        return jsonlogic_match(payload, logic)
    return conditions_match(payload, rule.get("conditions") or [])
