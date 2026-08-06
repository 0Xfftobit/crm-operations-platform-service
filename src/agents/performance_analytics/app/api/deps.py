"""Shared FastAPI dependencies — API-key auth for write/ops endpoints."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key",
        )


def parse_role_headers(
    role: str | None = Header(default=None, alias="X-Role"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_team_id: str | None = Header(default=None, alias="X-Team-Id"),
    x_rep_id: str | None = Header(default=None, alias="X-Rep-Id"),
) -> dict[str, str | None]:
    """Capture role-based access context for RLS (BR-5)."""
    return {
        "role": role,
        "tenant_id": x_tenant_id,
        "team_id": x_team_id,
        "rep_id": x_rep_id,
    }
