"""
In-memory + Redis cache for YouTube match results.

Key format: ``yt_match:<artist_lower>:<title_lower>:<duration>``
TTL: 7 days (YouTube video IDs are stable).
"""

import json
import hashlib
from typing import Optional, Dict, Any

_TTL_MATCH = 604800  # 7 days

# ── In-memory fallback (when Redis is unavailable) ────────────────────────────
_memory_cache: Dict[str, Dict[str, Any]] = {}

# Redis client reference — set by init_cache()
_redis = None


def init_cache(redis_client):
    """Set the Redis client for match caching."""
    global _redis
    _redis = redis_client


def _make_key(artist: str, title: str, duration: int) -> str:
    """Deterministic cache key from track metadata."""
    raw = f"{artist.lower().strip()}:{title.lower().strip()}:{duration}"
    h = hashlib.md5(raw.encode()).hexdigest()[:16]
    return f"yt_match:{h}"


async def get_cached_match(
    artist: str, title: str, duration: int
) -> Optional[Dict[str, Any]]:
    """Return cached match result or None."""
    key = _make_key(artist, title, duration)

    # Memory cache
    if key in _memory_cache:
        return _memory_cache[key]

    # Redis cache
    if _redis:
        try:
            raw = await _redis.get(key)
            if raw:
                result = json.loads(raw)
                _memory_cache[key] = result
                return result
        except Exception:
            pass

    return None


async def set_cached_match(
    artist: str, title: str, duration: int, result: Dict[str, Any]
):
    """Store match result in both memory and Redis."""
    key = _make_key(artist, title, duration)
    _memory_cache[key] = result

    if _redis:
        try:
            await _redis.set(key, json.dumps(result), ex=_TTL_MATCH)
        except Exception:
            pass


def get_cache_stats() -> Dict[str, Any]:
    """Return memory cache stats for diagnostics."""
    return {
        "memory_entries": len(_memory_cache),
        "redis_enabled": _redis is not None,
    }
