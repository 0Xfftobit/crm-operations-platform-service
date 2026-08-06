"""Cadence vocabulary — channels, states, consent bases, stop reasons, guardrail outcomes.

Single source of truth for the categorical values the scheduler, guardrails, channel orchestrator,
and ledger agree on.
"""
from __future__ import annotations

# Channels the engine sends over (provider abstraction, §5).
CHANNELS: tuple[str, ...] = ("sms", "email", "whatsapp", "voicemail", "call_task")

# Enrollment lifecycle states (System Design §2).
ENROLLMENT_STATES: tuple[str, ...] = ("active", "paused", "stopped", "completed")

# TCPA/CAN-SPAM consent bases recorded on every send (outreach ledger, §2). Unknown ⇒ fail-closed.
CONSENT_BASES: frozenset[str] = frozenset({
    "express_written", "express", "prior_business", "unknown"})
VALID_CONSENT: frozenset[str] = frozenset({"express_written", "express", "prior_business"})

# Reasons a sequence stops/pauses (ledgered).
STOP_REASONS: tuple[str, ...] = (
    "replied", "converted", "opt_out", "dnc", "max_steps", "completed", "manual")

# Outreach ledger send outcomes.
LEDGER_STATUS: tuple[str, ...] = ("sent", "failed", "held", "blocked")

# Guardrail block reasons (why a send was not allowed).
GUARDRAIL_BLOCKS: tuple[str, ...] = (
    "consent_unknown", "dnc", "opted_out", "suppressed", "quiet_hours", "frequency_cap",
    "manual_cooldown")

# Enrollment trigger types (FR: enrollment triggers).
ENROLL_TRIGGERS: tuple[str, ...] = (
    "manual", "stage_change", "lead_score", "tag_applied")

# Terminal deal stages that count as "converted/applied" for stop rules.
CONVERTED_STAGES: frozenset[str] = frozenset({
    "application", "applied", "approved", "closed_won", "funded"})
