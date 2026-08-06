"""Immutable, hash-chained audit ledger (CEAAPSS-566 / design §2).

Append-only: there is no edit or delete path. Each entry stores ``prev_hash`` and
``entry_hash = sha256(prev_hash || canonical(core))``, forming a tamper-evident chain — any
later mutation of a record's content breaks the chain, and ``verify_chain`` reports the first
broken ``seq``. Storage is pluggable: in-memory by default, durable SQL when ``uses_sql``; each
entry is also mirrored to a WORM store (object-lock) when configured, and periodic Merkle roots
are anchored for compact tamper-evidence over ranges.
"""
import csv
import hashlib
import io
import json
from datetime import UTC, datetime

from app.core.config import get_settings
from app.domain.models import AuditEntry

_GENESIS = "0" * 64
_HASHED = ("seq", "event_id", "occurred_at", "actor_type", "actor_id", "event_type", "entity_type",
           "entity_id", "action", "reasoning", "outcome", "engine", "explain_ref", "ip_address",
           "session_id")

# In-memory backend (default). SQL backend lives in app.db.ledger.
_entries: list[AuditEntry] = []
_anchors: list[dict] = []
# Purge watermark: the seq purged through + the carry hash the chain resumes from after a purge.
_purge_seq: int = 0
_purge_carry: str = _GENESIS


def _use_sql() -> bool:
    return get_settings().uses_sql


def _ledger():
    from app.db import ledger
    return ledger


def _core(entry: AuditEntry) -> dict:
    """The immutable content hashed into the chain (everything but the hashes themselves)."""
    d = entry.model_dump(mode="json")
    return {k: d[k] for k in _HASHED}


def _hash(prev_hash: str, core: dict) -> str:
    return hashlib.sha256((prev_hash + json.dumps(core, sort_keys=True)).encode()).hexdigest()


def _mirror_worm(entry: AuditEntry) -> None:
    """Best-effort mirror to the WORM store; never blocks the ledger write."""
    try:
        from app import worm
        worm.mirror(entry)
    except Exception:  # noqa: BLE001 — WORM is defense-in-depth, not the source of truth
        pass


def _watermark() -> tuple[int, str]:
    """(purged_through_seq, carry_hash) — (0, genesis) when nothing has been purged."""
    if _use_sql():
        return _ledger().get_watermark()
    return _purge_seq, _purge_carry


def append(**fields) -> AuditEntry:
    """Append one record to the ledger and return it (the only way to add; never edited).

    ``seq`` continues from the highest existing seq (or the purge watermark if the ledger has been
    purged empty), and ``prev_hash`` chains from the last entry — or the carry hash left by a purge
    — so the chain stays continuous and verifiable even after aged ranges are purged."""
    last = _ledger().last() if _use_sql() else (_entries[-1] if _entries else None)
    if last is not None:
        prev_hash, seq = last.entry_hash, last.seq + 1
    else:
        purged_seq, carry = _watermark()
        prev_hash, seq = carry, purged_seq + 1
    entry = AuditEntry(seq=seq, prev_hash=prev_hash, entry_hash="",
                       occurred_at=fields.pop("occurred_at", None) or datetime.now(UTC), **fields)
    entry.entry_hash = _hash(prev_hash, _core(entry))
    if _use_sql():
        _ledger().add(entry)
    else:
        _entries.append(entry)
    _mirror_worm(entry)
    return entry


def get(seq: int) -> AuditEntry | None:
    if _use_sql():
        return _ledger().get(seq)
    return next((e for e in _entries if e.seq == seq), None)


def search(
    *, entity_id: str | None = None, actor_id: str | None = None, event_type: str | None = None,
    engine: str | None = None, event_id: str | None = None, frm: datetime | None = None,
    to: datetime | None = None, limit: int = 500,
) -> list[AuditEntry]:
    if _use_sql():
        return _ledger().search(entity_id=entity_id, actor_id=actor_id, event_type=event_type,
                                engine_name=engine, event_id=event_id, frm=frm, to=to, limit=limit)
    out = _entries
    if entity_id:
        out = [e for e in out if e.entity_id == entity_id]
    if actor_id:
        out = [e for e in out if e.actor_id == actor_id]
    if event_type:
        out = [e for e in out if e.event_type == event_type]
    if engine:
        out = [e for e in out if e.engine == engine]
    if event_id:
        out = [e for e in out if e.event_id == event_id]
    if frm:
        out = [e for e in out if e.occurred_at >= frm]
    if to:
        out = [e for e in out if e.occurred_at <= to]
    return out[-limit:]


def verify_chain() -> dict:
    """Recompute the chain; a mismatch means a record was altered externally (tamper-evident).

    Verification resumes from the purge carry hash, so a ledger with aged ranges purged still
    verifies end-to-end (the purged ranges remain provable via their retained Merkle anchors)."""
    entries = all_entries()
    purged_seq, prev = _watermark()
    for e in entries:
        if e.prev_hash != prev or _hash(e.prev_hash, _core(e)) != e.entry_hash:
            return {"verified": False, "entries": len(entries), "broken_at": e.seq}
        prev = e.entry_hash
    return {"verified": True, "entries": len(entries), "broken_at": None,
            "purged_through_seq": purged_seq}


def export_csv(entries: list[AuditEntry]) -> str:
    buf = io.StringIO()
    cols = [*_HASHED, "prev_hash", "entry_hash"]
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for e in entries:
        writer.writerow({c: e.model_dump(mode="json")[c] for c in cols})
    return buf.getvalue()


def export_package(entries: list[AuditEntry]) -> dict:
    return {"count": len(entries), "chain_verified": verify_chain()["verified"],
            "generated_at": datetime.now(UTC).isoformat(),
            "entries": [e.model_dump(mode="json") for e in entries]}


def all_entries() -> list[AuditEntry]:
    if _use_sql():
        return _ledger().all_entries()
    return list(_entries)


# ── Merkle anchors ─────────────────────────────────────────────────────────
def record_anchor(*, seq_from: int, seq_to: int, merkle_root: str, anchored_at: datetime,
                  external_ref: str = "") -> None:
    if _use_sql():
        _ledger().add_anchor(seq_from=seq_from, seq_to=seq_to, merkle_root=merkle_root,
                             anchored_at=anchored_at, external_ref=external_ref)
    else:
        _anchors.append({"id": len(_anchors) + 1, "seq_from": seq_from, "seq_to": seq_to,
                         "merkle_root": merkle_root, "anchored_at": anchored_at.isoformat(),
                         "external_ref": external_ref})


def get_anchors() -> list[dict]:
    if _use_sql():
        return _ledger().anchors()
    return list(_anchors)


def max_anchored_seq() -> int:
    """Highest seq covered by any Merkle anchor (the purge safety boundary)."""
    return max((a["seq_to"] for a in get_anchors()), default=0)


def purge_watermark() -> dict:
    seq, carry = _watermark()
    return {"purged_through_seq": seq, "carry_hash": carry}


def purge_through(seq: int) -> dict:
    """Delete every entry with ``seq <= seq`` from the hot ledger and advance the purge watermark.

    Callers (the service) MUST first ensure the range is cold-archived (preserved) and
    Merkle-anchored (tamper-evidence retained). The carry hash is the purged head entry's hash, so
    the chain over the remaining entries still verifies."""
    global _purge_seq, _purge_carry
    head = get(seq)
    if head is None:
        return {"purged": 0, **purge_watermark()}
    carry = head.entry_hash
    if _use_sql():
        removed = _ledger().delete_through(seq)
        _ledger().set_watermark(seq, carry)
    else:
        before = len(_entries)
        _entries[:] = [e for e in _entries if e.seq > seq]
        removed = before - len(_entries)
        _purge_seq, _purge_carry = seq, carry
    return {"purged": removed, "purged_through_seq": seq, "carry_hash": carry}


def reset_ledger() -> None:
    global _purge_seq, _purge_carry
    _entries.clear()
    _anchors.clear()
    _purge_seq, _purge_carry = 0, _GENESIS
    if _use_sql():
        _ledger().clear()
