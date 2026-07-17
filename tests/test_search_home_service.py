"""SearchService (DB-first + Deezer merge) and HomeService (bounded + breaker)."""

import asyncio

import pytest

from cache.response_cache import CacheStatus, ResponseCache
from repositories.catalog.search_repository import SearchRepository
from services.catalog.home_service import HomeService
from services.catalog.jobs import BackgroundJobs
from services.catalog.search_service import SearchService
from services.deezer.errors import DeezerUnavailable
from tests.conftest import FakeGateway, FakeRedis


class FakeDeezer:
    def __init__(self, search=None, chart=None, chart_error=None):
        self._search = search or {}
        self._chart = chart
        self._chart_error = chart_error
        self.chart_calls = 0

    async def search_tracks(self, q, limit=25):
        return {"data": self._search.get("tracks", [])}

    async def search_albums(self, q, limit=25):
        return {"data": self._search.get("albums", [])}

    async def search_artists(self, q, limit=25):
        return {"data": self._search.get("artists", [])}

    async def get_chart(self):
        self.chart_calls += 1
        if self._chart_error:
            raise self._chart_error
        return self._chart


class RecordingIngestion:
    def __init__(self):
        self.search_calls = []

    async def ingest_search_results(self, entity_type, items):
        self.search_calls.append((entity_type, [i.get("id") for i in items]))
        return []


def _search_service(gateway, redis, deezer, ingestion):
    return SearchService(
        response_cache=ResponseCache(redis), jobs=BackgroundJobs(),
        search_repo=SearchRepository(gateway), ingestion=ingestion, deezer=deezer,
    )


async def test_search_local_sufficient_skips_deezer(fake_redis):
    # RPC returns 20 local rows -> no Deezer discovery.
    rows = [{"id": f"t{i}", "deezer_id": 1000 + i, "title": f"song {i}",
             "primary_artist_name": "X"} for i in range(20)]
    gw = FakeGateway({}, rpc_returns={"catalog_search": rows})
    deezer = FakeDeezer(search={"tracks": [{"id": 999, "title": "extra"}]})
    ing = RecordingIngestion()
    svc = _search_service(gw, fake_redis, deezer, ing)

    data, status = await svc.search("tracks", "song", limit=20)
    assert len(data["items"]) == 20
    assert all(i["source"] == "catalog" for i in data["items"])
    assert ing.search_calls == []  # no discovery ingest


async def test_search_insufficient_merges_and_dedupes_deezer(fake_redis):
    local = [{"id": "t1", "deezer_id": 111, "title": "offline", "primary_artist_name": "Young Miko"}]
    gw = FakeGateway({}, rpc_returns={"catalog_search": local})
    # Deezer returns 111 (dupe) + 222 (new).
    deezer = FakeDeezer(search={"tracks": [
        {"id": 111, "title": "offline", "artist": {"name": "Young Miko"}, "album": {}},
        {"id": 222, "title": "classy", "artist": {"name": "Feid"}, "album": {}},
    ]})
    ing = RecordingIngestion()
    svc = _search_service(gw, fake_redis, deezer, ing)

    data, _ = await svc.search("tracks", "song", limit=20)
    deezer_ids = [i["deezerId"] for i in data["items"]]
    assert deezer_ids == [111, 222]                     # 111 not duplicated
    assert data["items"][0]["source"] == "catalog"
    assert data["items"][1]["source"] == "deezer"
    await asyncio.sleep(0.01)
    assert ing.search_calls == [("tracks", [222])]      # only the new one ingested


async def test_search_cached_second_call(fake_redis):
    gw = FakeGateway({}, rpc_returns={"catalog_search": []})
    deezer = FakeDeezer(search={"tracks": []})
    svc = _search_service(gw, fake_redis, deezer, RecordingIngestion())
    await svc.search("tracks", "zzz", limit=10)
    data, status = await svc.search("tracks", "zzz", limit=10)
    assert status == CacheStatus.HIT


# ── home ─────────────────────────────────────────────────────────────────────

CHART = {
    "tracks": {"data": [{"id": 1, "title": "t", "artist": {"name": "A"}, "album": {}}]},
    "albums": {"data": [{"id": 2, "title": "al", "artist": {"name": "A"}}]},
    "artists": {"data": [{"id": 3, "name": "A"}]},
}


def _home_service(redis, deezer):
    return HomeService(redis=redis, response_cache=ResponseCache(redis),
                       jobs=BackgroundJobs(), deezer=deezer, ingestion=RecordingIngestion())


async def test_home_single_chart_call_then_cached(fake_redis):
    deezer = FakeDeezer(chart=CHART)
    svc = _home_service(fake_redis, deezer)
    data, status = await svc.get_home("US", limit=10)
    assert status == CacheStatus.MISS
    assert [t["deezerId"] for t in data["tracks"]] == [1]
    # second call served from cache — no extra Deezer fan-out
    data2, status2 = await svc.get_home("US", limit=10)
    assert status2 == CacheStatus.HIT
    assert deezer.chart_calls == 1


async def test_home_deezer_error_serves_stale_not_500(fake_redis):
    # Prime the cache with a good payload, then force Deezer to fail.
    good = _home_service(fake_redis, FakeDeezer(chart=CHART))
    await good.get_home("US", limit=10)

    failing = _home_service(fake_redis, FakeDeezer(chart_error=DeezerUnavailable("403")))
    # Expire freshness by marking the cached envelope stale.
    from cache.cache_keys import CacheKeys
    env = await failing.cache.get(CacheKeys.home_chart("US", 10))
    await failing.cache.set(CacheKeys.home_chart("US", 10), env["data"], ttl=60,
                            status=CacheStatus.STALE)
    data, status = await failing.get_home("US", limit=10)
    assert status == CacheStatus.STALE      # served stale, not a 500
    assert data is not None
