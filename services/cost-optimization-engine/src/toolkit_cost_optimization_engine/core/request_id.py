"""
Request ID middleware for Toolkit Cost Optimization Engine.

Assigns a unique request ID to every inbound HTTP request. If the caller
supplies an ``X-Request-ID`` header, that value is reused; otherwise a new
UUID4 is generated. The ID is:

- stored in a ``contextvars.ContextVar`` so downstream code can read it
- injected into every log record via a logging filter
- returned in the ``X-Request-ID`` response header
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Context variable holding the current request ID
REQUEST_ID_CTX: ContextVar[str | None] = ContextVar("request_id", default=None)

HEADER_NAME = "X-Request-ID"
HEADER_NAME_LOWER = b"x-request-id"


def get_request_id() -> str | None:
    """Return the request ID for the current async context."""
    return REQUEST_ID_CTX.get()


class RequestIDFilter(logging.Filter):
    """Logging filter that injects ``request_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
        return True


class RequestIDMiddleware:
    """Pure ASGI middleware that manages request IDs.

    Using raw ASGI (not BaseHTTPMiddleware) to avoid the known
    Starlette issue with BackgroundTasks in BaseHTTPMiddleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract caller-supplied request ID from headers
        request_id: str | None = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == HEADER_NAME_LOWER:
                request_id = header_value.decode("latin-1")
                break

        if not request_id:
            request_id = uuid.uuid4().hex

        token = REQUEST_ID_CTX.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER_NAME_LOWER, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            REQUEST_ID_CTX.reset(token)
