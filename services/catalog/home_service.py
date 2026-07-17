"""
HomeService — ONE bounded home/chart orchestration.

Replaces the old "fire many simultaneous genre searches on every Home open"
pattern (Pop/Rock/Latin/Indie/Hip-Hop ... in parallel). Instead:

  * a single Redis-cached normalized home payload (stale-while-revalidate);
  * one bounded Deezer /chart refresh guarded by a lock (no parallel fan-out);
  * on Deezer 403/429/errors: serve stale cache, log once, and open a short
    circuit-breaker cooldown instead of turning every section into a 500;
  * NO eager YouTube matching on the response path.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from cache.cache_keys import CacheKeys
from cache.cache_policy import TTL
from cache.distributed_lock import make_lock
from cache.response_cache import CacheStatus
from config import settings
from mappers import discovery_mapper as dm
from services.deezer.errors import DeezerError

logger = logging.getLogger("paax.home")

Result = Tuple[Optional[dict], str]

_COOLDOWN_SECONDS = 60


class HomeService:
    def __init__(self, *, redis, response_cache, jobs, deezer, ingestion):
        self.redis = redis
        self.cache = response_cache
        self.jobs = jobs
        self.deezer = deezer
        self.ingestion = ingestion

    async def get_home(self, country: str = "US", limit: int = 50) -> Result:
        key = CacheKeys.home_chart(country, limit)
        cooldown_key = f"home:cooldown:{country.lower()}"

        env = await self.cache.get(key)
        if env is not None and env["status"] != CacheStatus.STALE:
            return env["data"], CacheStatus.HIT

        # Circuit breaker: if we recently failed, keep serving stale.
        if await self.cache.get_negative(cooldown_key):
            if env is not None:
                return env["data"], CacheStatus.STALE
            return None, "degraded"

        lock = make_lock(self.redis, f"lock:refresh:home:{country.lower()}",
                         settings.ttl.refresh_lock, wait_seconds=settings.limits.lock_wait_seconds)
        got = await lock.acquire()
        if not got:
            # Someone else is refreshing; serve stale if we have it.
            if env is not None:
                return env["data"], CacheStatus.STALE
            return None, "processing"
        try:
            env2 = await self.cache.get(key)
            if env2 is not None and env2["status"] != CacheStatus.STALE:
                return env2["data"], CacheStatus.HIT

            try:
                chart = await self.deezer.get_chart()
            except DeezerError as exc:
                # Serve stale, cool down, log ONCE.
                await self.cache.set_negative(cooldown_key, _COOLDOWN_SECONDS, str(exc))
                logger.warning("[home] chart refresh failed (%s); serving stale, cooldown %ss",
                               exc, _COOLDOWN_SECONDS)
                if env is not None:
                    return env["data"], CacheStatus.STALE
                return None, "degraded"

            data = self._map_chart(chart, limit)
            await self.cache.set(key, data, TTL.home(), CacheStatus.HIT)
            # Persist chart entities partially in the background (no matching here).
            self.jobs.schedule(lambda: self._ingest_chart(chart), label="home-ingest")
            return data, CacheStatus.MISS
        finally:
            await lock.release()

    def _map_chart(self, chart: dict, limit: int) -> dict:
        tracks = ((chart.get("tracks") or {}).get("data")) or []
        albums = ((chart.get("albums") or {}).get("data")) or []
        artists = ((chart.get("artists") or {}).get("data")) or []
        return {
            "tracks": [dm.deezer_track_item(t) for t in tracks[:limit]],
            "albums": [dm.deezer_album_item(a) for a in albums[:limit]],
            "artists": [dm.deezer_artist_item(a) for a in artists[:limit]],
        }

    async def _ingest_chart(self, chart: dict) -> None:
        tracks = ((chart.get("tracks") or {}).get("data")) or []
        albums = ((chart.get("albums") or {}).get("data")) or []
        artists = ((chart.get("artists") or {}).get("data")) or []
        await self.ingestion.ingest_search_results("tracks", tracks)
        await self.ingestion.ingest_search_results("albums", albums)
        await self.ingestion.ingest_search_results("artists", artists)
