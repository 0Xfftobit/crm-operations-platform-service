"""CEL-compatible condition subset — comparisons and && / ||."""
from __future__ import annotations

import re
from typing import Any

_CLAUSE = re.compile(
    r"^\s*(?P<left>[a-zA-Z_][a-zA-Z0-9_.]*)\s*"
    r"(?P<op>==|!=|>=|<=|>|<)\s*"
    r"(?P<right>'(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\"|-?\d+(?:\.\d+)?|true|false)\s*$"
)


def _resolve(payload: dict[str, Any], ref: str) -> Any:
    if ref == "stage" and payload.get("stage") is None:
        return payload.get("to_stage") or payload.get("new_stage")
    return payload.get(ref)


def _parse_literal(raw: str) -> Any:
    if raw.startswith("'") or raw.startswith('"'):
        return raw[1:-1]
    if raw == "true":
        return True
    if raw == "false":
        return False
    if "." in raw:
        return float(raw)
    return int(raw)


def _eval_clause(payload: dict[str, Any], clause: str) -> bool:
    match = _CLAUSE.match(clause.strip())
    if not match:
        raise ValueError(f"unsupported_cel_clause:{clause}")
    left = _resolve(payload, match.group("left"))
    right = _parse_literal(match.group("right"))
    op = match.group("op")
    if left is None:
        return False
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return False


def cel_match(payload: dict[str, Any], expression: str | None) -> bool:
    if not expression or not str(expression).strip():
        return True
    expr = str(expression).strip()
    if "||" in expr:
        return any(cel_match(payload, part) for part in expr.split("||"))
    if "&&" in expr:
        return all(cel_match(payload, part) for part in expr.split("&&"))
    return _eval_clause(payload, expr)
