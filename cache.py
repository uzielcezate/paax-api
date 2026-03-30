"""
cache.py — Redis caching utilities for paax-api.

Usage:
    from cache import get_redis_client, make_cache_key, cache_get, cache_set
"""
import os
import json
import random
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


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
# Cache I/O
# ---------------------------------------------------------------------------

async def cache_get(redis, key: str) -> Optional[Any]:
    """Fetch value from Redis. Returns deserialized Python object or None on miss/error."""
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("[Cache] GET error for key=%s: %s", key, exc)
        return None


async def cache_set(redis, key: str, value: Any, ttl: int) -> None:
    """
    Store value in Redis as JSON with jitter-extended TTL (base + 0–60 s).
    Silently no-ops if Redis is unavailable.
    """
    if redis is None:
        return
    try:
        jitter       = random.randint(0, 60)
        effective_ttl = ttl + jitter
        serialized   = json.dumps(value, ensure_ascii=False)
        await redis.set(key, serialized, ex=effective_ttl)
    except Exception as exc:
        logger.warning("[Cache] SET error for key=%s: %s", key, exc)
