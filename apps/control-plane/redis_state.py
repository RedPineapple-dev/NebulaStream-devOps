"""Redis-backed distributed state for NebulaStream.

Provides:
  - Leader election via ``SET NX EX`` (single-writer lease)
  - Shared weight store backed by Redis HASH
  - Graceful in-memory fallback when Redis is unavailable

Usage::

    state = RedisState(redis_url="redis://localhost:6379/0", instance_id="cp-1")
    await state.connect()

    if await state.try_acquire_leader():
        # This instance is the leader — proceed with control loop.
        ...

    weights = await state.load_weights(default={...})
    await state.save_weights(weights)
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("control_plane.redis_state")

_REDIS_AVAILABLE = False
try:
    import redis.asyncio as aioredis  # type: ignore[import]

    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore[assignment]

LEADER_KEY = "nebula_leader"
WEIGHTS_KEY = "nebula_weights"
LEASE_TTL = 5  # seconds


class RedisState:
    """Thin wrapper around ``redis.asyncio`` with in-memory fallback."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        instance_id: str = "cp-local",
        lease_ttl: int = LEASE_TTL,
    ) -> None:
        self._url = redis_url
        self._instance_id = instance_id
        self._lease_ttl = lease_ttl
        self._client: Any = None
        self._enabled = _REDIS_AVAILABLE
        self._in_memory_weights: dict[str, int] = {}
        self._in_memory_leader: str | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Attempt to connect to Redis.  Returns True on success."""
        if not self._enabled:
            log.warning("redis_unavailable", reason="redis package not installed")
            return False
        try:
            self._client = aioredis.from_url(self._url, decode_responses=True)
            await self._client.ping()
            log.info("redis_connected", url=self._url, instance=self._instance_id)
            return True
        except Exception as exc:
            log.warning(
                "redis_connect_failed",
                url=self._url,
                error=str(exc)[:120],
            )
            self._client = None
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    # ── Leader election ───────────────────────────────────────────────────

    async def try_acquire_leader(self) -> bool:
        """Try to acquire/renew the leader lease.

        Returns True if this instance is the current leader.
        Uses ``SET NX EX`` for atomic acquire; renews if already held.
        Falls back to in-memory (always returns True) when Redis is down.
        """
        if not self._client:
            return True  # Standalone / fallback mode

        try:
            # Try to set (acquire) or renew if already the leader.
            acquired = await self._client.set(
                LEADER_KEY,
                self._instance_id,
                nx=True,
                ex=self._lease_ttl,
            )
            if acquired:
                log.info("leader_acquired", instance=self._instance_id)
                return True

            current = await self._client.get(LEADER_KEY)
            if current == self._instance_id:
                # Renew our lease.
                await self._client.expire(LEADER_KEY, self._lease_ttl)
                return True

            return False
        except Exception as exc:
            log.warning("leader_check_failed", error=str(exc)[:120])
            return True  # Fail-open: prefer availability over correctness

    async def get_leader(self) -> str | None:
        """Return the instance ID of the current leader, or None."""
        if not self._client:
            return self._instance_id
        try:
            return await self._client.get(LEADER_KEY)
        except Exception:
            return None

    # ── Weight store ──────────────────────────────────────────────────────

    async def load_weights(self, default: dict[str, int]) -> dict[str, int]:
        """Load weights from Redis HASH; returns *default* on miss or error."""
        if not self._client:
            return self._in_memory_weights or dict(default)
        try:
            data = await self._client.hgetall(WEIGHTS_KEY)
            if not data:
                return dict(default)
            return {r: int(w) for r, w in data.items()}
        except Exception as exc:
            log.warning("load_weights_failed", error=str(exc)[:120])
            return dict(default)

    async def save_weights(self, weights: dict[str, int]) -> None:
        """Persist weights to Redis HASH (and in-memory fallback)."""
        self._in_memory_weights = dict(weights)
        if not self._client:
            return
        try:
            await self._client.hset(WEIGHTS_KEY, mapping={k: str(v) for k, v in weights.items()})
        except Exception as exc:
            log.warning("save_weights_failed", error=str(exc)[:120])

    # ── Cooldown timer ────────────────────────────────────────────────────

    async def set_cooldown(self, key: str, ttl_seconds: int) -> None:
        """Set a cooldown key with TTL (replaces in-memory time tracking)."""
        if not self._client:
            return
        try:
            await self._client.set(key, "1", ex=ttl_seconds)
        except Exception:
            pass

    async def is_cooldown_active(self, key: str) -> bool:
        """Return True if the cooldown key still exists in Redis."""
        if not self._client:
            return False
        try:
            return bool(await self._client.exists(key))
        except Exception:
            return False

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def connected(self) -> bool:
        return self._client is not None
