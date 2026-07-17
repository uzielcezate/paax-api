"""CatalogService cache-first + stale-while-revalidate tests."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from cache.cache_keys import CacheKeys
from cache.response_cache import CacheStatus, ResponseCache
from repositories.catalog.album_repository import AlbumRepository
from repositories.catalog.artist_repository import ArtistRepository
from repositories.catalog.track_repository import TrackRepository
from services.catalog.catalog_service import CatalogService
from services.catalog.jobs import BackgroundJobs
from services.deezer.errors import DeezerNotFound
from tests.conftest import FakeGateway, FakeRedis

FRESH = datetime.now(timezone.utc).isoformat()
STALE = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()


class FakeIngestion:
    """Simulates ingestion by inserting rows into the gateway's tables."""

    def __init__(self, gateway: FakeGateway, not_found=frozenset()):
        self.g = gateway
        self.calls = []
        self._not_found = set(not_found)

    async def ingest_track(self, deezer_id):
        self.calls.append(("track", deezer_id))
        if deezer_id in self._not_found:
            raise DeezerNotFound("no data")
        self.g._tables.setdefault("tracks", []).append({
            "id": f"trk-{deezer_id}", "deezer_id": deezer_id, "title": f"track {deezer_id}",
            "duration_seconds": 180, "youtube_match_status": "pending",
            "metadata_status": "full", "metadata_updated_at": FRESH,
        })
        return f"trk-{deezer_id}"

    async def ingest_album(self, deezer_id, complete=True):
        self.calls.append(("album", deezer_id))
        self.g._tables.setdefault("albums", []).append({
            "id": f"alb-{deezer_id}", "deezer_id": deezer_id, "title": f"album {deezer_id}",
            "album_type": "album", "metadata_status": "full", "metadata_updated_at": FRESH,
        })
        return f"alb-{deezer_id}"

    async def ingest_artist_profile(self, deezer_id):
        self.calls.append(("artist", deezer_id))
        self.g._tables.setdefault("artists", []).append({
            "id": f"art-{deezer_id}", "deezer_id": deezer_id, "name": f"artist {deezer_id}",
            "metadata_status": "full", "metadata_updated_at": FRESH,
        })
        return f"art-{deezer_id}"


def _service(gateway, redis=None, not_found=frozenset()):
    ing = FakeIngestion(gateway, not_found=not_found)
    svc = CatalogService(
        redis=redis, response_cache=ResponseCache(redis), jobs=BackgroundJobs(),
        ingestion=ing, artist_repo=ArtistRepository(gateway),
        album_repo=AlbumRepository(gateway), track_repo=TrackRepository(gateway),
    )
    return svc, ing


async def test_cache_hit_returns_without_db(fake_redis):
    gw = FakeGateway({})
    svc, ing = _service(gw, fake_redis)
    await svc.cache.set(CacheKeys.track_by_deezer(123), {"id": "cached"}, ttl=60,
                        status=CacheStatus.HIT)
    data, status = await svc.get_track(123)
    assert status == CacheStatus.HIT and data == {"id": "cached"}
    assert ing.calls == []


async def test_db_fresh_no_ingest(fake_redis):
    gw = FakeGateway({"tracks": [{
        "id": "trk-200", "deezer_id": 200, "title": "fresh", "duration_seconds": 100,
        "youtube_match_status": "pending", "metadata_status": "full", "metadata_updated_at": FRESH,
    }]})
    svc, ing = _service(gw, fake_redis)
    data, status = await svc.get_track(200)
    assert status == CacheStatus.HIT and data["deezerId"] == 200
    assert ing.calls == []


async def test_db_stale_serves_stale_and_refreshes(fake_redis):
    gw = FakeGateway({"tracks": [{
        "id": "trk-201", "deezer_id": 201, "title": "old", "duration_seconds": 100,
        "youtube_match_status": "pending", "metadata_status": "full", "metadata_updated_at": STALE,
    }]})
    svc, ing = _service(gw, fake_redis)
    data, status = await svc.get_track(201)
    assert status == CacheStatus.STALE
    assert data["metadataStatus"] == "stale"
    await asyncio.sleep(0.05)  # let the background refresh run
    assert ("track", 201) in ing.calls


async def test_missing_triggers_ingest_then_serves(fake_redis):
    gw = FakeGateway({})
    svc, ing = _service(gw, fake_redis)
    data, status = await svc.get_track(202)
    assert status == CacheStatus.MISS and data["deezerId"] == 202
    assert ing.calls == [("track", 202)]


async def test_not_found_negative_cached(fake_redis):
    gw = FakeGateway({})
    svc, ing = _service(gw, fake_redis, not_found={203})
    data, status = await svc.get_track(203)
    assert data is None and status == CacheStatus.MISS
    # Second call must hit the negative cache — no second ingest attempt.
    data2, _ = await svc.get_track(203)
    assert data2 is None
    assert ing.calls == [("track", 203)]


async def test_album_and_artist_missing_ingest(fake_redis):
    gw = FakeGateway({})
    svc, ing = _service(gw, fake_redis)
    a_data, a_status = await svc.get_album(333)
    ar_data, ar_status = await svc.get_artist(111)
    assert a_data["deezerId"] == 333 and a_status == CacheStatus.MISS
    assert ar_data["deezerId"] == 111 and ar_status == CacheStatus.MISS
    assert ("album", 333) in ing.calls and ("artist", 111) in ing.calls
