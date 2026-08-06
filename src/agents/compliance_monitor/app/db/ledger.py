"""SQL storage backend for the audit ledger + Merkle anchors.

Holds only persistence; the hash-chain construction/verification stays in ``app.audit``. ``seq`` is
assigned by the caller (count+1) rather than autoincrement so hashing is deterministic and identical
to the in-memory backend.
"""
from datetime import datetime

from app.db import engine
from app.db.orm import AuditRow, LedgerCheckpointRow, MerkleAnchorRow
from app.domain.models import AuditEntry

_GENESIS = "0" * 64

_COLS = ("seq", "event_id", "occurred_at", "actor_type", "actor_id", "event_type", "entity_type",
         "entity_id", "action", "reasoning", "outcome", "engine", "explain_ref", "ip_address",
         "session_id", "prev_hash", "entry_hash")


def _to_entry(row: AuditRow) -> AuditEntry:
    # occurred_at is stored as an ISO string; pydantic parses it back to a tz-aware datetime.
    return AuditEntry(**{c: getattr(row, c) for c in _COLS})


def _row_kwargs(entry: AuditEntry) -> dict:
    d = {c: getattr(entry, c) for c in _COLS}
    d["occurred_at"] = entry.occurred_at.isoformat()      # store the exact canonical string
    return d


def count() -> int:
    with engine.session() as s:
        return s.query(AuditRow).count()


def last() -> AuditEntry | None:
    with engine.session() as s:
        row = s.query(AuditRow).order_by(AuditRow.seq.desc()).first()
        return _to_entry(row) if row else None


def add(entry: AuditEntry) -> None:
    with engine.session() as s:
        s.add(AuditRow(**_row_kwargs(entry)))
        s.commit()


def get(seq: int) -> AuditEntry | None:
    with engine.session() as s:
        row = s.get(AuditRow, seq)
        return _to_entry(row) if row else None


def all_entries() -> list[AuditEntry]:
    with engine.session() as s:
        return [_to_entry(r) for r in s.query(AuditRow).order_by(AuditRow.seq).all()]


def search(
    *, entity_id: str | None = None, actor_id: str | None = None, event_type: str | None = None,
    engine_name: str | None = None, event_id: str | None = None, frm: datetime | None = None,
    to: datetime | None = None, limit: int = 500,
) -> list[AuditEntry]:
    with engine.session() as s:
        q = s.query(AuditRow)
        if entity_id:
            q = q.filter(AuditRow.entity_id == entity_id)
        if actor_id:
            q = q.filter(AuditRow.actor_id == actor_id)
        if event_type:
            q = q.filter(AuditRow.event_type == event_type)
        if engine_name:
            q = q.filter(AuditRow.engine == engine_name)
        if event_id:
            q = q.filter(AuditRow.event_id == event_id)
        if frm:
            q = q.filter(AuditRow.occurred_at >= frm.isoformat())
        if to:
            q = q.filter(AuditRow.occurred_at <= to.isoformat())
        rows = q.order_by(AuditRow.seq).all()
        return [_to_entry(r) for r in rows][-limit:]


def clear() -> None:
    with engine.session() as s:
        s.query(AuditRow).delete()
        s.query(LedgerCheckpointRow).delete()
        s.commit()


def delete_through(seq: int) -> int:
    with engine.session() as s:
        n = s.query(AuditRow).filter(AuditRow.seq <= seq).delete()
        s.commit()
        return n


def get_watermark() -> tuple[int, str]:
    """Return (purged_through_seq, carry_hash); (0, genesis) when nothing purged."""
    with engine.session() as s:
        row = s.get(LedgerCheckpointRow, 1)
        return (row.purged_through_seq, row.carry_hash) if row else (0, _GENESIS)


def set_watermark(seq: int, carry_hash: str) -> None:
    with engine.session() as s:
        s.merge(LedgerCheckpointRow(id=1, purged_through_seq=seq, carry_hash=carry_hash))
        s.commit()


# ── Merkle anchors ─────────────────────────────────────────────────────────
def add_anchor(*, seq_from: int, seq_to: int, merkle_root: str, anchored_at: datetime,
               external_ref: str = "") -> None:
    with engine.session() as s:
        s.add(MerkleAnchorRow(seq_from=seq_from, seq_to=seq_to, merkle_root=merkle_root,
                              anchored_at=anchored_at, external_ref=external_ref))
        s.commit()


def anchors() -> list[dict]:
    with engine.session() as s:
        rows = s.query(MerkleAnchorRow).order_by(MerkleAnchorRow.id).all()
        return [{"id": r.id, "seq_from": r.seq_from, "seq_to": r.seq_to,
                 "merkle_root": r.merkle_root, "anchored_at": r.anchored_at.isoformat(),
                 "external_ref": r.external_ref} for r in rows]
