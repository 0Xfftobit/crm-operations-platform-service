"""Sequence branching (FR: branching; System Design §2/§4) — pick the next step from engagement.

A step's ``branches`` choose what happens after it, evaluated in order against the engagement seen
for that step. Conditions:

- ``opened`` / ``clicked`` / ``replied`` — immediate: satisfied once that signal arrives.
- ``no_open_after:PT48H`` / ``no_reply_after:PT48H`` / ``no_click_after:PT24H`` — timed negative:
  satisfied only once the duration has elapsed AND the signal never came.

``branch_wait`` is how long to wait before evaluating (the longest timed condition), so timed
negatives get their full window.
"""
from __future__ import annotations

from datetime import timedelta

from app.domain.models import Step
from app.duration import condition_duration

_KIND_OF = {"opened": "open", "clicked": "click", "replied": "reply"}


def branch_wait(step: Step) -> timedelta:
    """The longest timed-condition window on the step (0 if only immediate conditions)."""
    waits = [condition_duration(b.cond) for b in step.branches if "_after:" in b.cond]
    return max(waits, default=timedelta(0))


def _satisfied(cond: str, *, engaged: set[str], elapsed: timedelta) -> bool:
    if cond in _KIND_OF:                          # immediate positive
        return _KIND_OF[cond] in engaged
    if cond.startswith("no_") and "_after:" in cond:   # timed negative
        signal = cond[3:].split("_after:", 1)[0]       # no_open_after → open
        return signal not in engaged and elapsed >= condition_duration(cond)
    return False


def next_step_from_branches(step: Step, *, engaged: set[str], elapsed: timedelta) -> str | None:
    """First branch whose condition is satisfied → its ``goto``; else None (fall through linear)."""
    for b in step.branches:
        if _satisfied(b.cond, engaged=engaged, elapsed=elapsed):
            return b.goto
    return None


def remaining_wait(step: Step, *, engaged: set[str], elapsed: timedelta) -> timedelta | None:
    """Time until a still-pending timed-negative branch could fire (its signal still absent), else
    None. Lets an immediate ``opened`` branch fire while ``no_open_after:PT48H`` keeps waiting."""
    remaining = []
    for b in step.branches:
        if b.cond.startswith("no_") and "_after:" in b.cond:
            signal = b.cond[3:].split("_after:", 1)[0]
            dur = condition_duration(b.cond)
            if signal not in engaged and elapsed < dur:
                remaining.append(dur - elapsed)
    return min(remaining) if remaining else None
