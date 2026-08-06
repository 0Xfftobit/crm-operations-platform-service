"""PII exposure detection (FR-007) — pattern scan over comm content + activity payloads.

Flags common sensitive identifiers so they can raise a ``data_privacy`` alert. Intentionally
conservative (precision over recall) to keep alert noise down; deep classification is out of scope.
"""
import re

# Categories that constitute a real exposure in outbound content (an email/phone to the very
# contact being messaged is normal; an SSN or card number in the body is not).
HIGH_RISK = frozenset({"ssn", "credit_card", "bank_account"})

_PATTERNS: dict[str, re.Pattern] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"),
    "bank_account": re.compile(r"\b(?:acct|account)\s*#?\s*\d{6,17}\b", re.IGNORECASE),
}


def scan(text: str) -> list[str]:
    """Return the sorted list of PII categories found in ``text`` (empty = clean)."""
    if not text:
        return []
    return sorted(name for name, pat in _PATTERNS.items() if pat.search(text))


def scan_payload(payload: dict) -> list[str]:
    """Scan all string values in a (shallow) payload dict."""
    found: set[str] = set()
    for v in payload.values():
        if isinstance(v, str):
            found.update(scan(v))
    return sorted(found)
