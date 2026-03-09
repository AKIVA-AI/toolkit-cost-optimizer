"""Simple in-memory rate limiting middleware.

Uses a sliding-window counter per client IP. Configuration is pulled from
Settings.RATE_LIMIT_PER_MINUTE and Settings.RATE_LIMIT_BURST.
"""

from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter keyed by client IP."""

    def __init__(self, app, rate_per_minute: int = 100, burst: int = 200):
        super().__init__(app)
        self.rate_per_minute = rate_per_minute
        self.burst = burst
        self._tokens: dict[str, float] = defaultdict(lambda: float(burst))
        self._last_time: dict[str, float] = defaultdict(time.monotonic)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health/metrics endpoints
        if request.url.path in ("/health", "/status", "/metrics"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        # Refill tokens
        elapsed = now - self._last_time[client_ip]
        self._last_time[client_ip] = now
        refill = elapsed * (self.rate_per_minute / 60.0)
        self._tokens[client_ip] = min(
            self._tokens[client_ip] + refill,
            float(self.burst),
        )

        if self._tokens[client_ip] < 1.0:
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded", "retry_after_seconds": 60},
                headers={"Retry-After": "60"},
            )

        self._tokens[client_ip] -= 1.0
        return await call_next(request)
