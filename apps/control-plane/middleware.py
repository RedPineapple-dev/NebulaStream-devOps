"""Token-bucket HTTP rate limiter as a FastAPI middleware.

Each client IP gets its own bucket. Buckets refill at ``refill_rate`` tokens
per second up to ``capacity``. Each request consumes one token.

Usage::

    from middleware import add_rate_limiter
    add_rate_limiter(app, capacity=100, refill_rate=10)
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response


class _TokenBucket:
    """Per-client token bucket (not thread-safe — asyncio single-threaded ok)."""

    __slots__ = ("capacity", "refill_rate", "_tokens", "_last_refill")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()

    def consume(self) -> bool:
        """Returns True if a token was consumed, False if the bucket is empty."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class TokenBucketMiddleware:
    """ASGI middleware wrapping FastAPI with per-IP token-bucket rate limiting."""

    def __init__(
        self,
        app,
        capacity: float = 100.0,
        refill_rate: float = 10.0,
        exempt_paths: tuple[str, ...] = ("/healthz", "/readyz", "/metrics", "/ws"),
    ) -> None:
        self.app = app
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.exempt_paths = exempt_paths
        self._buckets: dict[str, _TokenBucket] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path not in self.exempt_paths:
                client = (scope.get("client") or ("unknown", 0))[0]
                bucket = self._buckets.setdefault(
                    client, _TokenBucket(self.capacity, self.refill_rate)
                )
                if not bucket.consume():
                    response = Response(
                        content='{"detail":"Too Many Requests"}',
                        status_code=429,
                        media_type="application/json",
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def add_rate_limiter(
    app: FastAPI,
    capacity: float = 100.0,
    refill_rate: float = 10.0,
    exempt_paths: tuple[str, ...] = ("/healthz", "/readyz", "/metrics", "/ws"),
) -> None:
    """Attach the token-bucket rate limiter middleware to *app*."""
    app.add_middleware(
        TokenBucketMiddleware,  # type: ignore[arg-type]
        capacity=capacity,
        refill_rate=refill_rate,
        exempt_paths=exempt_paths,
    )
