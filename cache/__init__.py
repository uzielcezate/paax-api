"""
Cache package (Phase 2.3).

Redis is a *temporary performance layer* — never the source of truth (Supabase
is). This package provides:

  * store         — low-level Redis+in-memory get/set (legacy API, unchanged).
  * cache_keys    — the centralized key registry (no raw key strings elsewhere).
  * cache_policy  — TTL lookups from config.
  * distributed_lock — Redis locks (cache-stampede / duplicate-ingest guard).
  * response_cache   — high-level cache-first read with hit/miss/stale status,
                       negative caching, and targeted invalidation.

The legacy helpers are re-exported so ``from cache import get_redis_client, ...``
keeps working exactly as before.
"""

from cache.store import (  # noqa: F401  (re-export for backward compatibility)
    MemoryCache,
    cache_delete,
    cache_get,
    cache_set,
    get_memory_cache_stats,
    get_redis_client,
    make_cache_key,
)

__all__ = [
    "MemoryCache",
    "cache_delete",
    "cache_get",
    "cache_set",
    "get_memory_cache_stats",
    "get_redis_client",
    "make_cache_key",
]
