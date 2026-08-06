"""ORM tables. The audit ledger is fully columnar (search + hash-chain determinism); operational
rows use a hybrid of key columns + a JSON ``data`` blob holding the full pydantic model.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditRow(Base):
    __tablename__ = "audit_ledger"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    event_id: Mapped[str] = mapped_column(String, default="")
    # Stored as the exact ISO-8601 string so the hash round-trips byte-for-byte across dialects
    # (SQLite would otherwise drop the tz on a DateTime column and break the chain).
    occurred_at: Mapped[str] = mapped_column(String)
    actor_type: Mapped[str] = mapped_column(String, default="system")
    actor_id: Mapped[str] = mapped_column(String, default="")
    event_type: Mapped[str] = mapped_column(String, index=True)
    entity_type: Mapped[str] = mapped_column(String, default="")
    entity_id: Mapped[str] = mapped_column(String, default="", index=True)
    action: Mapped[str] = mapped_column(String, default="")
    reasoning: Mapped[str] = mapped_column(String, default="")
    outcome: Mapped[str] = mapped_column(String, default="")
    engine: Mapped[str] = mapped_column(String, default="", index=True)
    explain_ref: Mapped[str] = mapped_column(String, default="")
    ip_address: Mapped[str] = mapped_column(String, default="")
    session_id: Mapped[str] = mapped_column(String, default="")
    prev_hash: Mapped[str] = mapped_column(String)
    entry_hash: Mapped[str] = mapped_column(String)


class MerkleAnchorRow(Base):
    __tablename__ = "merkle_anchor"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seq_from: Mapped[int] = mapped_column(Integer)
    seq_to: Mapped[int] = mapped_column(Integer)
    merkle_root: Mapped[str] = mapped_column(String)
    anchored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_ref: Mapped[str] = mapped_column(String, default="")


class LedgerCheckpointRow(Base):
    """Single-row purge watermark: seq purged through + the carry hash the chain resumes from."""
    __tablename__ = "ledger_checkpoint"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # always 1
    purged_through_seq: Mapped[int] = mapped_column(Integer, default=0)
    carry_hash: Mapped[str] = mapped_column(String)  # last purged entry's hash (chain resumes here)


class _KV(Base):
    """Abstract single-key hybrid row: string key + JSON model blob."""
    __abstract__ = True
    k: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class ConsentRow(Base):
    __tablename__ = "consent"
    lead_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class AlertRow(_KV):
    __tablename__ = "alert"


class CaseRow(_KV):
    __tablename__ = "compliance_case"


class DisclosureRow(Base):
    __tablename__ = "disclosure_timer"
    deal_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class ExplainRow(_KV):
    __tablename__ = "explainability"


class RuleRow(Base):
    __tablename__ = "compliance_rule"
    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    stage: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class RuleChangeRow(Base):
    __tablename__ = "rule_change"
    change_id: Mapped[str] = mapped_column(String, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class RegulationRow(_KV):
    __tablename__ = "regulation"


class RetentionRow(_KV):
    __tablename__ = "retention"


class PolicyRow(_KV):
    __tablename__ = "compliance_policy"   # single active row, key = policy_id
