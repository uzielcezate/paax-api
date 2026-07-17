"""Integration tests for the normalized /v2 router (TestClient, fake container)."""

import pytest
from fastapi.testclient import TestClient

import app_container
from main import app
from services.rate_limit import RateLimiter


# ── fakes ────────────────────────────────────────────────────────────────────

class _Slot:
    def __init__(self, vid): self.preferred_youtube_video_id = vid; self.youtube_match_status = "matched"


class _Track:
    def __init__(self, vid="v1"): self.id = "t-int"; self.playback = _Slot(vid)


class FakeTrackRepo:
    async def get_track_by_deezer_id(self, dz):
        return _Track()


class FakeCatalog:
    def __init__(self):
        self.tracks = FakeTrackRepo()

    async def get_artist(self, dz):
        return {"id": "a1", "deezerId": dz, "name": "Artist"}, "miss"

    async def get_artist_by_internal_id(self, aid):
        if aid == "missing":
            return None, "miss"
        return {"id": aid, "deezerId": 111, "name": "Artist",
                "discography": {"albums": [], "eps": [], "singles": [], "compilations": []},
                "latestRelease": None}, "hit"

    async def get_album(self, dz):
        return {"id": "al1", "deezerId": dz, "title": "Album",
                "tracks": [{"id": "t1", "playback": {"videoId": None}}]}, "miss"

    async def get_album_by_internal_id(self, aid):
        return {"id": aid, "deezerId": 333, "title": "Album", "tracks": []}, "hit"

    async def get_track(self, dz):
        return {"id": "t1", "deezerId": dz, "title": "Track"}, "miss"

    async def get_track_by_internal_id(self, tid):
        return {"id": tid, "deezerId": 123, "title": "Track"}, "hit"


class FakePlayback:
    def __init__(self): self.scheduled = []
    def schedule_ids(self, ids): self.scheduled.extend(ids)
    async def resolve_and_persist(self, tid):
        return {"provider": "youtube", "engine": "iframe", "videoId": "aud1",
                "preferredType": "audio_only", "matchStatus": "matched"}
    async def report_failure(self, tid):
        return {"provider": "youtube", "engine": "iframe", "videoId": None, "matchStatus": "stale"}


class FakeSearch:
    async def search(self, t, q, limit=20, offset=0):
        return {"type": t, "query": q, "items": [{"type": t, "deezerId": 1}]}, "miss"


class FakeHome:
    async def get_home(self, country, limit):
        return {"tracks": [], "albums": [], "artists": []}, "hit"


class FakeDeezer:
    async def get_artist_top(self, dz, limit=20):
        return {"data": [{"id": 9, "title": "Top", "artist": {"name": "Artist"}, "album": {}}]}


@pytest.fixture
def client():
    c = app_container.container
    snapshot = c.__dict__.copy()
    c.gateway = object()
    c.catalog = FakeCatalog()
    c.playback = FakePlayback()
    c.search = FakeSearch()
    c.home = FakeHome()
    c.deezer = FakeDeezer()
    c.rate_limiter = RateLimiter(None)
    c.matcher_available = True
    yield TestClient(app)          # no context manager -> lifespan not run
    c.__dict__.update(snapshot)


def test_artist_by_deezer(client):
    r = client.get("/v2/artists/deezer/111")
    assert r.status_code == 200
    assert r.json()["deezerId"] == 111
    assert "X-Request-ID" in r.headers        # correlation id echoed


def test_artist_by_internal_and_404(client):
    assert client.get("/v2/artists/a1").status_code == 200
    assert client.get("/v2/artists/missing").status_code == 404


def test_discography_and_top(client):
    r = client.get("/v2/artists/a1/discography")
    assert r.status_code == 200 and "discography" in r.json()
    r2 = client.get("/v2/artists/a1/top")
    assert r2.status_code == 200 and r2.json()["items"][0]["playback"]["videoId"] == "v1"


def test_album_and_track(client):
    assert client.get("/v2/albums/deezer/333").json()["deezerId"] == 333
    assert client.get("/v2/tracks/deezer/123").json()["deezerId"] == 123
    assert client.get("/v2/tracks/t-int").json()["id"] == "t-int"


def test_resolve_and_report(client):
    r = client.post("/v2/tracks/t1/resolve-playback")
    assert r.status_code == 200 and r.json()["playback"]["videoId"] == "aud1"
    r2 = client.post("/v2/tracks/t1/report-playback-failure")
    assert r2.status_code == 200 and r2.json()["playback"]["matchStatus"] == "stale"


def test_find_and_home(client):
    assert client.get("/v2/find?q=miko&type=tracks").json()["items"]
    assert "tracks" in client.get("/v2/home").json()


def test_503_when_supabase_not_ready(client):
    app_container.container.catalog = None      # simulate Supabase unconfigured
    assert client.get("/v2/artists/deezer/111").status_code == 503


def test_legacy_health_shape_present(client):
    r = client.get("/health")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert "dependencies" in body and "status" in body
