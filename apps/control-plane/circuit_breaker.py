"""Async circuit breaker for external service calls (LLM, Redis, etc.).

States:
  CLOSED   — Normal operation. Failures are counted.
  OPEN     — After max_failures consecutive failures, calls are rejected immediately.
  HALF_OPEN— After reset_seconds, one probe call is allowed.  Success → CLOSED;
             failure → OPEN again.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    """Async circuit breaker.

    Usage::

        cb = CircuitBreaker(max_failures=3, reset_seconds=30, name="ollama")

        result = await cb.call(my_async_fn, arg1, kwarg=val)
    """

    def __init__(
        self,
        max_failures: int = 3,
        reset_seconds: float = 30.0,
        name: str = "circuit_breaker",
    ) -> None:
        self.max_failures = max_failures
        self.reset_seconds = reset_seconds
        self.name = name

        self._state = CBState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def state(self) -> CBState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def call(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute *fn* through the circuit breaker.

        Raises CircuitBreakerOpen if the circuit is OPEN and the reset
        window hasn't elapsed.  Otherwise, calls fn and tracks result.
        """
        async with self._lock:
            if self._state == CBState.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0)
                if elapsed < self.reset_seconds:
                    raise CircuitBreakerOpen(
                        f"[{self.name}] circuit is OPEN "
                        f"({self.reset_seconds - elapsed:.1f}s remaining)"
                    )
                # Allow one probe
                self._state = CBState.HALF_OPEN

        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            await self._on_failure()
            raise exc

        await self._on_success()
        return result

    # ── State transitions ──────────────────────────────────────────────────

    async def _on_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._opened_at = None
            self._state = CBState.CLOSED

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            if self._state == CBState.HALF_OPEN or self._failure_count >= self.max_failures:
                self._state = CBState.OPEN
                self._opened_at = time.monotonic()

    # ── Helpers ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Manually reset to CLOSED (useful in tests)."""
        self._state = CBState.CLOSED
        self._failure_count = 0
        self._opened_at = None

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"CircuitBreaker(name={self.name!r}, state={self._state.value}, "
            f"failures={self._failure_count}/{self.max_failures})"
        )
