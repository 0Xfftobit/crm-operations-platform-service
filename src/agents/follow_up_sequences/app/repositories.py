"""Repositories behind a narrow interface (in-memory default; Postgres/Temporal/Redis via gateways).

Holds the operational state: versioned sequence definitions, contacts (consent/tz), per-contact
enrollments (the durable state machines), the append-only outreach ledger (idempotent by
(enrollment, step, occurrence)), engagement events, and the audit log. Contact/enrollment-scoped
stores are keyed by **(tenant_id, id)** so tenants that reuse an id never collide. `from __future__`
keeps a ``list`` method from shadowing ``list[...]`` annotations.
"""
from __future__ import annotations

from app.domain.models import (
    Contact,
    EngagementEvent,
    Enrollment,
    OutreachRecord,
    SequenceDef,
)


class SequenceRepository:
    """Versioned sequence definitions, keyed by (tenant, sequence_id, version)."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, int], SequenceDef] = {}

    def put(self, s: SequenceDef) -> None:
        self._items[(s.tenant_id, s.sequence_id, s.version)] = s

    def get(self, sequence_id: str, tenant: str = "default",
            version: int | None = None) -> SequenceDef | None:
        if version is not None:
            return self._items.get((tenant, sequence_id, version))
        versions = [s for (t, sid, _v), s in self._items.items()
                    if t == tenant and sid == sequence_id]
        active = [s for s in versions if s.status == "active"] or versions
        return max(active, key=lambda s: s.version, default=None)

    def list(self, tenant: str | None = None) -> list[SequenceDef]:
        return [s for s in self._items.values() if tenant is None or s.tenant_id == tenant]

    def clear(self) -> None:
        self._items.clear()


class ContactRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Contact] = {}

    def put(self, c: Contact) -> None:
        self._items[(c.tenant_id, c.contact_id)] = c

    def get(self, contact_id: str, tenant: str = "default") -> Contact | None:
        return self._items.get((tenant, contact_id))

    def list(self, tenant: str | None = None) -> list[Contact]:
        return [c for c in self._items.values() if tenant is None or c.tenant_id == tenant]

    def clear(self) -> None:
        self._items.clear()


class EnrollmentRepository:
    def __init__(self) -> None:
        self._items: dict[str, Enrollment] = {}

    def put(self, e: Enrollment) -> None:
        self._items[e.enrollment_id] = e

    def get(self, enrollment_id: str) -> Enrollment | None:
        return self._items.get(enrollment_id)

    def for_contact(self, contact_id: str, tenant: str) -> list[Enrollment]:
        return [e for e in self._items.values()
                if e.contact_id == contact_id and e.tenant_id == tenant]

    def active_for_contact(self, contact_id: str, tenant: str) -> list[Enrollment]:
        return [e for e in self.for_contact(contact_id, tenant) if e.state in ("active", "paused")]

    def due(self, now, tenant: str | None = None) -> list[Enrollment]:
        return [e for e in self._items.values() if e.state == "active"
                and e.next_action_at <= now and (tenant is None or e.tenant_id == tenant)]

    def list(self, tenant: str | None = None) -> list[Enrollment]:
        return [e for e in self._items.values() if tenant is None or e.tenant_id == tenant]

    def clear(self) -> None:
        self._items.clear()


class LedgerRepository:
    """Append-only outreach ledger (§8) — every attempted send with its consent basis + outcome."""

    def __init__(self) -> None:
        self._items: list[OutreachRecord] = []
        self._keys: set[tuple[str, str, int]] = set()   # (enrollment, step, occurrence) idempotency

    def add(self, r: OutreachRecord) -> None:
        self._items.append(r)
        if r.status == "sent":
            self._keys.add((r.enrollment_id, r.step_id, r.occurrence))

    def sent_exists(self, enrollment_id: str, step_id: str, occurrence: int) -> bool:
        return (enrollment_id, step_id, occurrence) in self._keys

    def for_enrollment(self, enrollment_id: str) -> list[OutreachRecord]:
        return [r for r in self._items if r.enrollment_id == enrollment_id]

    def for_sequence(self, sequence_id: str, tenant: str) -> list[OutreachRecord]:
        # enrollment→sequence is resolved by the caller; ledger filters by tenant here.
        return [r for r in self._items if r.tenant_id == tenant]

    def list(self, tenant: str | None = None) -> list[OutreachRecord]:
        return [r for r in self._items if tenant is None or r.tenant_id == tenant]

    def clear(self) -> None:
        self._items.clear()
        self._keys.clear()


class EngagementRepository:
    def __init__(self) -> None:
        self._items: list[EngagementEvent] = []

    def add(self, e: EngagementEvent) -> None:
        self._items.append(e)

    def kinds_for_step(self, enrollment_id: str, step_id: str) -> set[str]:
        return {e.kind for e in self._items
                if e.enrollment_id == enrollment_id and e.step_id == step_id}

    def list(self) -> list[EngagementEvent]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()


class AuditRepository:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def add(self, record: dict) -> None:
        self._items.append(record)

    def list(self) -> list[dict]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()


_sequences = SequenceRepository()
_contacts = ContactRepository()
_enrollments = EnrollmentRepository()
_ledger = LedgerRepository()
_engagement = EngagementRepository()
_audit = AuditRepository()


def get_sequence_repository() -> SequenceRepository: return _sequences
def get_contact_repository() -> ContactRepository: return _contacts
def get_enrollment_repository() -> EnrollmentRepository: return _enrollments
def get_ledger_repository() -> LedgerRepository: return _ledger
def get_engagement_repository() -> EngagementRepository: return _engagement
def get_audit_repository() -> AuditRepository: return _audit


def reset_repositories() -> None:
    for repo in (_sequences, _contacts, _enrollments, _ledger, _engagement, _audit):
        repo.clear()
