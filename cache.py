"""
cache.py — Redis caching utilities for paax-api.

Usage:
    from cache import get_redis_client, make_cache_key, cache_get, cache_set
"""
import os
import json
import random
import re
import time
import logging
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory fallback cache (used when Redis is unavailable)
# ---------------------------------------------------------------------------

class MemoryCache:
    """
    Simple in-memory TTL cache for when Redis is unavailable.
    Thread-safe enough for single-worker uvicorn. Uses OrderedDict for LRU
    eviction and stores (value, expires_at) tuples.
    """

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            # Expired — remove and return miss
            del self._cache[key]
            self._misses += 1
            return None
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        jitter = random.randint(0, 60)
        expires_at = time.time() + ttl + jitter
        # Remove existing entry if present (to update position)
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = (value, expires_at)
        # Evict oldest entries if over limit
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
        }


# Global in-memory cache instance (always available)
_memory_cache = MemoryCache(max_size=500)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def get_redis_client():
    """
    Return a redis.asyncio client if REDIS_URL is set, else None.
    Credentials are intentionally NOT logged.
    """
    url = os.environ.get("REDIS_URL")
    if not url:
        logger.info("[Cache] REDIS_URL not set — caching disabled.")
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore
        client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        logger.info("[Cache] Redis client created.")
        return client
    except Exception as exc:
        logger.warning("[Cache] Failed to create Redis client: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _normalize(value: str) -> str:
    """Trim, lowercase, collapse whitespace, replace spaces with underscore."""
    value = value.strip().lower()
    value = re.sub(r"\s+", "_", value)
    return value


def make_cache_key(endpoint: str, params: dict) -> str:
    """
    Build a deterministic cache key.

    Example:
        make_cache_key("search", {"q": "Justin Bieber", "filter": "songs", "limit": "20"})
        → "search:filter=songs|limit=20|q=justin_bieber"
    """
    normalized = {
        _normalize(k): _normalize(str(v))
        for k, v in params.items()
        if v is not None and str(v).strip() != ""
    }
    parts = "|".join(f"{k}={v}" for k, v in sorted(normalized.items()))
    return f"{_normalize(endpoint)}:{parts}"


# ---------------------------------------------------------------------------
# Cache I/O (Redis → MemoryCache fallback)
# ---------------------------------------------------------------------------

async def cache_get(redis, key: str) -> Optional[Any]:
    """
    Fetch value from cache. Tries Redis first, falls back to in-memory cache.
    Returns deserialized Python object or None on miss/error.
    """
    # Try Redis first
    if redis is not None:
        try:
            raw = await redis.get(key)
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("[Cache] Redis GET error for key=%s: %s", key, exc)

    # Fall back to in-memory cache
    return _memory_cache.get(key)


async def cache_set(redis, key: str, value: Any, ttl: int) -> None:
    """
    Store value in cache. Writes to Redis (if available) AND in-memory cache.
    In-memory ensures cache survives brief Redis outages.
    """
    # Always write to in-memory cache
    _memory_cache.set(key, value, ttl)

    # Also write to Redis if available
    if redis is not None:
        try:
            jitter       = random.randint(0, 60)
            effective_ttl = ttl + jitter
            serialized   = json.dumps(value, ensure_ascii=False)
            await redis.set(key, serialized, ex=effective_ttl)
        except Exception as exc:
            logger.warning("[Cache] Redis SET error for key=%s: %s", key, exc)


def get_memory_cache_stats() -> dict:
    """Return stats for the in-memory fallback cache."""
    return _memory_cache.stats
