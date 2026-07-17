"""
SearchService — database-first search with external (Deezer) discovery.

  1. Redis response cache.
  2. Supabase trigram search (catalog_search RPC).
  3. If local results are insufficient, query Deezer, schedule background
     ingestion of the new entities, and merge+dedupe into the response.
  4. Cache the final response briefly.

YouTube matching is NEVER on the search path (results must not wait for it).
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from cache.cache_keys import CacheKeys
from cache.cache_policy import TTL
from cache.response_cache import CacheStatus
from mappers import discovery_mapper as dm
from services.deezer.errors import DeezerError

logger = logging.getLogger("paax.search")

_TYPES = {"tracks", "albums", "artists"}
Result = Tuple[dict, str]


class SearchService:
    #: below this many local hits we reach out to Deezer for discovery
    LOCAL_SUFFICIENT_RATIO = 0.5

    def __init__(self, *, response_cache, jobs, search_repo, ingestion, deezer):
        self.cache = response_cache
        self.jobs = jobs
        self.search_repo = search_repo
        self.ingestion = ingestion
        self.deezer = deezer

    async def search(self, entity_type: str, query: str, limit: int = 20, offset: int = 0) -> Result:
        entity_type = entity_type if entity_type in _TYPES else "tracks"
        query = (query or "").strip()
        if not query:
            return {"type": entity_type, "query": query, "items": []}, CacheStatus.MISS

        key = CacheKeys.search(entity_type, query, limit, offset)
        env = await self.cache.get(key)
        if env is not None:
            return env["data"], env["status"]

        local_rows = await self.search_repo._search(entity_type, query, limit, offset)
        items = [dm.local_item(entity_type, r) for r in local_rows]
        have = {i["deezerId"] for i in items if i.get("deezerId")}

        # Discovery when local coverage is thin.
        if len(items) < max(1, int(limit * self.LOCAL_SUFFICIENT_RATIO)):
            dz_new = await self._discover(entity_type, query, limit, have)
            if dz_new:
                # Persist discovered entities in the background (partial).
                self.jobs.schedule(
                    lambda: self.ingestion.ingest_search_results(entity_type, dz_new),
                    label=f"search-ingest:{entity_type}",
                )
                items.extend(dm.deezer_item(entity_type, d) for d in dz_new)

        items = items[:limit]
        data = {"type": entity_type, "query": query, "items": items}
        await self.cache.set(key, data, TTL.search(), CacheStatus.HIT)
        return data, CacheStatus.MISS

    async def _discover(self, entity_type: str, query: str, limit: int, have: set) -> List[dict]:
        fn = {
            "tracks": self.deezer.search_tracks,
            "albums": self.deezer.search_albums,
            "artists": self.deezer.search_artists,
        }[entity_type]
        try:
            dz = await fn(query, limit=limit)
        except DeezerError as exc:
            logger.warning("[search] deezer discovery failed for %r: %s", query, exc)
            return []
        data = dz.get("data") or []
        return [d for d in data if d.get("id") not in have]
