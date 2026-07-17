"""
High-level cache-first response cache with hit/miss/stale status envelopes,
negative caching and targeted invalidation. Backed by store (Redis + in-memory
fallback). Redis is transient — Supabase remains the source of truth.
"""

from __future__ import annotations

from typing import Any, List, Optional

from cache.store import cache_delete, cache_get, cache_set


class CacheStatus:
    HIT = "hit"        # served fresh from Redis
    MISS = "miss"      # not in Redis
    STALE = "stale"    # served from Redis but flagged stale (refresh scheduled)


class ResponseCache:
    """Envelope-based response cache. Envelopes: {data, status, v}."""

    _VERSION = 1

    def __init__(self, redis):
        self._redis = redis

    async def get(self, key: str) -> Optional[dict]:
        """Return the stored envelope {data, status} or None on miss."""
        raw = await cache_get(self._redis, key)
        if not isinstance(raw, dict) or raw.get("v") != self._VERSION:
            return None
        return raw

    async def set(self, key: str, data: Any, ttl: int, status: str = CacheStatus.HIT) -> None:
        await cache_set(self._redis, key, {"v": self._VERSION, "status": status, "data": data}, ttl)

    async def delete(self, key: str) -> None:
        await cache_delete(self._redis, key)

    async def invalidate(self, *keys: str) -> None:
        """Targeted invalidation only — never a global flush."""
        for key in keys:
            if key:
                await cache_delete(self._redis, key)

    # -- negative cache ------------------------------------------------------

    async def get_negative(self, key: str) -> Optional[dict]:
        raw = await cache_get(self._redis, key)
        if isinstance(raw, dict) and raw.get("negative"):
            return raw
        return None

    async def set_negative(self, key: str, ttl: int, reason: str = "") -> None:
        await cache_set(self._redis, key, {"negative": True, "reason": reason}, ttl)
