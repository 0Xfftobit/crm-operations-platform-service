"""RBAC for rule authoring and execution authorization."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import get_settings


class Role(StrEnum):
    REVOPS = "revops"
    ENGINEER = "engineer"
    LOAN_OFFICER = "loan_officer"
    COMPLIANCE = "compliance"
    SRE = "sre"
    ADMIN = "admin"


ROLE_SCOPES: dict[str, set[str]] = {
    Role.REVOPS: {"rules.read", "rules.write", "rules.simulate", "rules.publish", "events.ingest", "vision.read", "llm.approve"},
    Role.ENGINEER: {"rules.read", "rules.write", "rules.simulate", "events.ingest", "vision.read", "dlq.replay"},
    Role.LOAN_OFFICER: {"tasks.read", "tasks.complete", "dashboard.read"},
    Role.COMPLIANCE: {"vision.read", "audit.read", "ledger.read"},
    Role.SRE: {"vision.read", "dlq.read", "dlq.replay", "health.read"},
    Role.ADMIN: {"*"},
}


class Principal:
    def __init__(self, *, role: str, subject: str, api_key_valid: bool) -> None:
        self.role = role
        self.subject = subject
        self.api_key_valid = api_key_valid

    def has_scope(self, scope: str) -> bool:
        if not self.api_key_valid:
            return False
        scopes = ROLE_SCOPES.get(self.role, set())
        return "*" in scopes or scope in scopes


def resolve_principal(
    x_api_key: Annotated[str | None, Header()] = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
    x_subject: Annotated[str | None, Header(alias="X-Subject")] = None,
) -> Principal:
    settings = get_settings()
    valid = bool(x_api_key and x_api_key == settings.api_key)
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key")
    role = (x_role or Role.REVOPS).lower()
    if role not in ROLE_SCOPES and role != Role.ADMIN:
        role = Role.REVOPS
    return Principal(role=role, subject=x_subject or "api-user", api_key_valid=True)


PrincipalDep = Annotated[Principal, Depends(resolve_principal)]


def require_scope(scope: str):
    def _dep(principal: PrincipalDep) -> Principal:
        if not principal.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing scope: {scope} (role={principal.role})",
            )
        return principal

    return _dep
