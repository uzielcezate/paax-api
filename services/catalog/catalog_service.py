"""
CatalogService — cache-first artist/album/track reads with stale-while-revalidate.

Read order (per docs/CACHE_STRATEGY.md):
  1. Redis response cache -> HIT.
  2. Negative cache -> controlled not-found.
  3. Supabase persistent catalog:
       fresh -> cache + return;
       stale -> return stale immediately + schedule background refresh.
  4. Missing -> distributed lock -> Deezer ingest -> cache + return.

Supabase is the source of truth; Redis is transient. Deezer is only touched on a
miss or a stale background refresh.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional, Tuple

from cache.cache_keys import CacheKeys
from cache.cache_policy import TTL, is_stale
from cache.distributed_lock import make_lock
from cache.response_cache import CacheStatus, ResponseCache
from config import settings
from mappers import response_mapper as resp
from services.deezer.errors import DeezerError, DeezerNotFound

logger = logging.getLogger("paax.catalog")

# (data_or_None, cache_status). status in {hit, miss, stale, processing}.
Result = Tuple[Optional[dict], str]


def _dump(model) -> dict:
    """Serialize a response model to a camelCase dict (never leak raw rows/models)."""
    return model.model_dump(by_alias=True)


def _to_epoch(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


class CatalogService:
    def __init__(self, *, redis, response_cache: ResponseCache, jobs,
                 ingestion, artist_repo, album_repo, track_repo):
        self.redis = redis
        self.cache = response_cache
        self.jobs = jobs
        self.ingestion = ingestion
        self.artists = artist_repo
        self.albums = album_repo
        self.tracks = track_repo

    # ── public API (Deezer-id entry points) ────────────────────────────────

    async def get_artist(self, deezer_id: int) -> Result:
        return await self._get_by_deezer(
            entity_type="artist",
            deezer_id=deezer_id,
            cache_key=CacheKeys.artist_by_deezer(deezer_id),
            ttl=TTL.artist(),
            load=lambda: self.artists.get_artist_by_deezer_id(deezer_id),
            build=resp.artist_response,
            ingest=lambda: self.ingestion.ingest_artist_profile(deezer_id),
        )

    async def get_album(self, deezer_id: int) -> Result:
        return await self._get_by_deezer(
            entity_type="album",
            deezer_id=deezer_id,
            cache_key=CacheKeys.album_by_deezer(deezer_id),
            ttl=TTL.album(),
            load=lambda: self.albums.get_album_by_deezer_id(deezer_id),
            build=resp.album_response,
            ingest=lambda: self.ingestion.ingest_album(deezer_id, complete=True),
        )

    async def get_track(self, deezer_id: int) -> Result:
        return await self._get_by_deezer(
            entity_type="track",
            deezer_id=deezer_id,
            cache_key=CacheKeys.track_by_deezer(deezer_id),
            ttl=TTL.track(),
            load=lambda: self.tracks.get_track_by_deezer_id(deezer_id),
            build=resp.track_response,
            ingest=lambda: self.ingestion.ingest_track(deezer_id),
        )

    # ── generic cache-first + SWR + lock ───────────────────────────────────

    async def _get_by_deezer(self, *, entity_type: str, deezer_id: int, cache_key: str,
                             ttl: int, load: Callable[[], Awaitable], build, ingest) -> Result:
        neg_key = CacheKeys.negative(entity_type, deezer_id)

        # 1. response cache
        env = await self.cache.get(cache_key)
        if env is not None:
            return env["data"], env["status"]

        # 2. negative cache
        if await self.cache.get_negative(neg_key):
            return None, CacheStatus.MISS

        # 3. supabase
        graph = await load()
        if graph is not None:
            return await self._serve_graph(
                graph, entity_type, cache_key, ttl, build, ingest, deezer_id)

        # 4. missing -> lock -> ingest
        lock_key = CacheKeys.lock_ingest(entity_type, deezer_id)
        lock = make_lock(self.redis, lock_key, settings.ttl.ingest_lock,
                         wait_seconds=settings.limits.lock_wait_seconds)
        await lock.acquire()
        try:
            graph = await load()  # another request may have just ingested it
            if graph is None:
                may_ingest = (self.redis is None) or lock.owned
                if not may_ingest:
                    # Another request holds the ingest lock and it's still not in
                    # the DB after our bounded wait -> controlled processing state.
                    return None, "processing"
                try:
                    await ingest()
                except DeezerNotFound:
                    await self.cache.set_negative(neg_key, TTL.negative(), "not_found")
                    logger.info("[catalog] %s deezer_id=%s not found (negative-cached)",
                                entity_type, deezer_id)
                    return None, CacheStatus.MISS
                graph = await load()
            if graph is None:
                return None, CacheStatus.MISS
            data = _dump(build(graph))
            await self.cache.set(cache_key, data, ttl, CacheStatus.HIT)
            return data, CacheStatus.MISS
        finally:
            await lock.release()

    async def _serve_graph(self, graph, entity_type, cache_key, ttl, build, ingest,
                           deezer_id) -> Result:
        fresh = not is_stale(_to_epoch(getattr(graph, "metadata_updated_at", None)),
                             entity_type, time.time())
        if fresh:
            data = _dump(build(graph))
            await self.cache.set(cache_key, data, ttl, CacheStatus.HIT)
            return data, CacheStatus.HIT

        # stale -> serve now, refresh in background
        data = _dump(build(graph, metadata_override="stale"))
        await self.cache.set(cache_key, data, ttl, CacheStatus.STALE)
        self.jobs.schedule(
            lambda: self._refresh(entity_type, deezer_id, cache_key, ingest),
            label=f"refresh:{entity_type}:{deezer_id}",
        )
        return data, CacheStatus.STALE

    async def _refresh(self, entity_type: str, deezer_id: int, cache_key: str, ingest) -> None:
        """Background re-ingest, guarded by a refresh lock (skip if already running)."""
        lock = make_lock(self.redis, CacheKeys.lock_refresh(entity_type, str(deezer_id)),
                         settings.ttl.refresh_lock, wait_seconds=0.0)
        if not await lock.acquire():
            return
        try:
            await ingest()
            await self.cache.invalidate(cache_key)
            logger.info("[catalog] refreshed %s deezer_id=%s", entity_type, deezer_id)
        except DeezerError as exc:
            logger.warning("[catalog] refresh %s deezer_id=%s failed: %s",
                           entity_type, deezer_id, exc)
        finally:
            await lock.release()

    # ── cache invalidation (called after ingest / match updates) ───────────

    async def invalidate_track(self, deezer_id: Optional[int] = None,
                               internal_id: Optional[str] = None) -> None:
        keys = []
        if deezer_id is not None:
            keys.append(CacheKeys.track_by_deezer(deezer_id))
        if internal_id is not None:
            keys.append(CacheKeys.track(internal_id))
        await self.cache.invalidate(*keys)

    async def invalidate_album(self, deezer_id: Optional[int] = None,
                               internal_id: Optional[str] = None) -> None:
        keys = []
        if deezer_id is not None:
            keys.append(CacheKeys.album_by_deezer(deezer_id))
        if internal_id is not None:
            keys.append(CacheKeys.album(internal_id))
        await self.cache.invalidate(*keys)

    async def invalidate_artist(self, deezer_id: Optional[int] = None,
                                internal_id: Optional[str] = None) -> None:
        keys = []
        if deezer_id is not None:
            keys.append(CacheKeys.artist_by_deezer(deezer_id))
        if internal_id is not None:
            keys.append(CacheKeys.artist(internal_id))
            keys.append(CacheKeys.artist_discography(internal_id))
            keys.append(CacheKeys.artist_top(internal_id))
        await self.cache.invalidate(*keys)
