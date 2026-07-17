"""
Redis fixed-window rate limiter for expensive endpoints.

Degrades OPEN when Redis is unavailable (never blocks traffic on a cache outage).
Returns (allowed, remaining, reset_seconds).
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger("paax.ratelimit")


class RateLimiter:
    def __init__(self, redis):
        self._redis = redis

    async def check(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int, int]:
        if self._redis is None:
            return True, limit, window_seconds  # degrade open
        bucket = f"ratelimit:{key}"
        try:
            count = await self._redis.incr(bucket)
            if count == 1:
                await self._redis.expire(bucket, window_seconds)
            ttl = await self._redis.ttl(bucket)
            remaining = max(0, limit - int(count))
            reset = ttl if isinstance(ttl, int) and ttl > 0 else window_seconds
            return int(count) <= limit, remaining, reset
        except Exception as exc:  # noqa: BLE001 — never block on limiter failure
            logger.warning("[ratelimit] error on %s: %s (degrading open)", key, exc)
            return True, limit, window_seconds
