"""
Redis distributed locks — guard against cache stampedes and duplicate Deezer
ingestion. Ownership-safe (release only if we still hold the token), expiring,
bounded-wait, and gracefully degrading when Redis is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

logger = logging.getLogger("paax.lock")

# Release only if the stored token is ours (avoids releasing someone else's lock
# after our TTL expired and they re-acquired).
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


class DistributedLock:
    """One lock instance. Prefer using it as an async context manager."""

    def __init__(self, redis, key: str, ttl_seconds: int,
                 wait_seconds: float = 0.0, poll_interval: float = 0.1):
        self._redis = redis
        self._key = key
        self._ttl = max(1, int(ttl_seconds))
        self._wait = max(0.0, float(wait_seconds))
        self._poll = max(0.02, float(poll_interval))
        self._token = uuid.uuid4().hex
        self._held = False
        #: True only when *this* instance acquired the lock in Redis (vs. the
        #: no-Redis degraded path). Lets callers know if real mutual exclusion held.
        self.owned = False

    async def acquire(self) -> bool:
        """
        Try to acquire, waiting up to ``wait_seconds``. Returns True if we hold
        the lock. When Redis is unavailable, returns True (degraded: proceed
        without mutual exclusion) — callers must remain idempotent.
        """
        if self._redis is None:
            self._held = True
            return True

        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._wait
        while True:
            try:
                ok = await self._redis.set(self._key, self._token, nx=True, ex=self._ttl)
            except Exception as exc:  # noqa: BLE001 — Redis down => degrade open
                logger.warning("[lock] acquire error on %s: %s (degrading open)", self._key, exc)
                self._held = True
                return True
            if ok:
                self._held = True
                self.owned = True
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(self._poll)

    async def release(self) -> None:
        if not self._held:
            return
        self._held = False
        if self._redis is None or not self.owned:
            return
        try:
            await self._redis.eval(_RELEASE_LUA, 1, self._key, self._token)
        except Exception as exc:  # noqa: BLE001 — lock will expire via TTL anyway
            logger.warning("[lock] release error on %s: %s", self._key, exc)

    async def __aenter__(self) -> "DistributedLock":
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.release()


def make_lock(redis, key: str, ttl_seconds: int, wait_seconds: float = 0.0) -> DistributedLock:
    return DistributedLock(redis, key, ttl_seconds, wait_seconds=wait_seconds)
