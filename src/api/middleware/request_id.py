"""Request-ID middleware: generates/echoes X-Request-ID and binds it to structlog context."""
from __future__ import annotations

from uuid import uuid4

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestIDMiddleware:
    """ASGI middleware that ensures every request has a request_id.

    - Generates an ``X-Request-ID`` if the client did not provide one and injects
      it into the ASGI ``scope["headers"]`` so downstream handlers see it.
    - Binds ``request_id`` to ``structlog`` contextvars for the lifetime of the
      request and clears them afterwards.
    - Echoes ``X-Request-ID`` on the response unless the inner app already set it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope["headers"])
        request_id: str | None = None
        for _i, (name, value) in enumerate(headers):
            if name.lower() == b"x-request-id":
                request_id = value.decode("utf-8")
                break

        if request_id is None:
            request_id = uuid4().hex
            headers.append([b"x-request-id", request_id.encode("utf-8")])
            scope["headers"] = headers

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                if not any(h[0].lower() == b"x-request-id" for h in response_headers):
                    response_headers.append([b"x-request-id", request_id.encode("utf-8")])
                    message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.clear_contextvars()
