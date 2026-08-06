"""Shared API dependencies."""
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> str:
    settings = get_settings()
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key")
    return x_api_key
