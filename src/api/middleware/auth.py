"""Gateway X-API-Key middleware.

The canonical auth gate is implemented as a FastAPI dependency in
``src/api/main.py:require_api_key``. The middleware below is a thin ASGI
wrapper available for environments that want to reject unauthenticated
requests before they reach the application layer.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from config.settings import get_settings


class GatewayAPIKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not carry the configured X-API-Key.

    Public paths (``/health``, ``/ready``) are exempt.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if request.url.path not in ("/health", "/ready", "/agents"):
            api_key = request.headers.get("X-API-Key")
            if not api_key or api_key != settings.api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "missing or invalid API key"},
                )
        return await call_next(request)
