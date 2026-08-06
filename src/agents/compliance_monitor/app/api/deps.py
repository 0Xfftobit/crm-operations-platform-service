"""Shared FastAPI dependencies."""
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
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid API key"
        )


def require_export_role(
    settings: SettingsDep,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
) -> str:
    """Least-privilege gate for audit export — only the configured roles may pull the trail."""
    if not x_role or x_role not in settings.export_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role '{x_role}' not permitted to export the audit trail",
        )
    return x_role
