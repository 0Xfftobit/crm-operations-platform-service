"""Personalization variable substitution (FR: personalization; AC: variables substitute correctly).

Renders ``{{lead_name}}`` / ``{{property_name}}`` / ``{{agent_name}}`` (and any context variables)
from the contact + enrollment context into the message template. Unknown variables are left blank
rather than leaking the raw ``{{token}}`` into an outbound message.
"""
from __future__ import annotations

import re

from app.domain.models import Contact

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def variables(contact: Contact, context: dict | None = None) -> dict[str, str]:
    """The substitution map for a contact (+ enrollment context overrides)."""
    base = {
        "lead_name": contact.name,
        "property_name": contact.property_name,
        "agent_name": contact.agent_name,
        "contact_id": contact.contact_id,
    }
    if context:
        base.update({k: str(v) for k, v in context.items()})
    return base


def render(template: str, contact: Contact, context: dict | None = None) -> str:
    """Substitute ``{{var}}`` tokens; unknown tokens render as empty (never leaked)."""
    values = variables(contact, context)
    return _VAR.sub(lambda m: values.get(m.group(1), ""), template)
