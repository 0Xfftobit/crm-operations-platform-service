"""Coverage-gap / dropped-event monitoring (BR-3 — 'gaps are alerts'; NFR coverage = 100%).

Two complementary signals guarantee no monitoring blind spots:

- **Sequence gaps**: producers stamp a per-source monotonic ``source_seq``. If the monitor sees seq
  jump (…5, 7) the missing numbers are *dropped events* → a gap alert names each missing seq.
- **Heartbeat staleness**: each source's last-seen time is tracked; a source silent beyond the
  configured window is flagged (it may have stopped emitting — a silent blind spot).

Plus a **dead-letter counter**: messages the ingest path could not parse are counted so a growing
DLQ raises an alert. State is in-process (per replica); the service layer turns findings into
audit-backed alerts in the ``audit`` domain.
"""
from datetime import datetime

# per-source state: highest seq seen, set of gap seqs still outstanding, last-seen time.
_high: dict[str, int] = {}
_gaps: dict[str, set[int]] = {}
_last_seen: dict[str, datetime] = {}
_dead_letters: int = 0


def observe(source: str, source_seq: int | None, *, now: datetime) -> list[int]:
    """Record one event from ``source``; return any newly-detected missing seqs (dropped events)."""
    if source:
        _last_seen[source] = now
    if not source or source_seq is None:
        return []
    prev = _high.get(source)
    new_gaps: list[int] = []
    if prev is None or source_seq <= prev:
        # First sight, or a fill-in of a previously-missing seq.
        _gaps.setdefault(source, set()).discard(source_seq)
    else:
        missing = list(range(prev + 1, source_seq))
        if missing:
            _gaps.setdefault(source, set()).update(missing)
            new_gaps = missing
    if prev is None or source_seq > prev:
        _high[source] = source_seq
    return new_gaps


def stale_sources(*, now: datetime, max_min: int) -> list[dict]:
    """Sources silent longer than ``max_min`` minutes (a coverage blind spot)."""
    out: list[dict] = []
    for source, ts in _last_seen.items():
        silent_min = (now - ts).total_seconds() / 60
        if silent_min > max_min:
            out.append({"source": source, "silent_min": round(silent_min, 1)})
    return out


def record_dead_letter() -> int:
    global _dead_letters
    _dead_letters += 1
    return _dead_letters


def report() -> dict:
    return {
        "sources": {
            s: {"high_seq": _high.get(s), "outstanding_gaps": sorted(_gaps.get(s, set())),
                "last_seen": _last_seen[s].isoformat() if s in _last_seen else None}
            for s in set(_high) | set(_last_seen)
        },
        "dead_letters": _dead_letters,
    }


def dead_letters() -> int:
    return _dead_letters


def reset() -> None:
    global _dead_letters
    _high.clear()
    _gaps.clear()
    _last_seen.clear()
    _dead_letters = 0
