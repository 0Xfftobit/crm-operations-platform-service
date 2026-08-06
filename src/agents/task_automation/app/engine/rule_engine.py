"""Workflow rule condition evaluation — delegates to JSONLogic module."""
from __future__ import annotations

from typing import Any

from app.engine.jsonlogic import conditions_match, rule_conditions_match

__all__ = ["conditions_match", "rule_conditions_match", "_eval_condition"]


def _eval_condition(payload: dict[str, Any], condition: dict[str, Any]) -> bool:
    return conditions_match(payload, [condition])
