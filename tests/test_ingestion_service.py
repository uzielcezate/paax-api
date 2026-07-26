"""Ingestion service tests (per-track detail fetch, credit preservation, errors)."""

import asyncio

import pytest

from ingestion.catalog_ingestion_service import CatalogIngestionService
from repositories.catalog.album_repository import AlbumRepository
from repositories.catalog.artist_repository import ArtistRepository
from repositories.catalog.track_repository import TrackRepository
from services.deezer.errors import DeezerNotFound, DeezerUnavailable
from tests.conftest import FakeGateway


class FakeDeezer:
    def __init__(self, album=None, tracks=None, artist=None, track_errors=None, artist_albums=None):
        self._album = album
        self._tracks = tracks or {}
        self._artist = artist
        self._track_errors = track_errors or {}
        self._artist_albums = artist_albums or {"data": []}
        self.track_calls = []

    async def get_album(self, album_id):
        return self._album

    async def get_track(self, track_id):
        self.track_calls.append(track_id)
        if track_id in self._track_errors:
            raise self._track_errors[track_id]
        return self._tracks[track_id]

    async def get_artist(self, artist_id):
        return self._artist

    async def get_artist_albums(self, artist_id, limit=100):
        return self._artist_albums


def _rpc_returns():
    return {
        "catalog_upsert_album_graph": "alb-uuid",
        "catalog_upsert_track_graph": "trk-uuid",
        "catalog_upsert_artist_graph": "art-uuid",
    }


DZ_ALBUM = {
    "id": 333, "title": "att.", "record_type": "album", "release_date": "2024-04-05",
    "nb_tracks": 2, "duration": 360, "explicit_lyrics": True, "cover_xl": "http://cover.jpg",
    "artist": {"id": 111, "name": "Young Miko"},
    "contributors": [{"id": 111, "name": "Young Miko", "role": "Main"}],
    "genres": {"data": [{"id": 197, "name": "Latin"}]},
    "tracks": {"data": [
        {"id": 124, "title": "solo", "track_position": 1, "duration": 180,
         "artist": {"id": 111, "name": "Young Miko"}},
        {"id": 123, "title": "offline", "track_position": 6, "duration": 180,
         "artist": {"id": 111, "name": "Young Miko"}},
    ]},
}
DZ_TRACKS = {
    124: {"id": 124, "title": "solo", "track_position": 1, "duration": 180,
          "artist": {"id": 111, "name": "Young Miko"},
          "contributors": [{"id": 111, "name": "Young Miko", "role": "Main"}]},
    123: {"id": 123, "title": "offline", "track_position": 6, "duration": 180, "isrc": "US1",
          "artist": {"id": 111, "name": "Young Miko"},
          "contributors": [{"id": 111, "name": "Young Miko", "role": "Main"},
                           {"id": 222, "name": "Feid", "role": "Featured"}]},
}


def _service(deezer):
    gw = FakeGateway({}, rpc_returns=_rpc_returns())
    svc = CatalogIngestionService(
        deezer, ArtistRepository(gw), AlbumRepository(gw), TrackRepository(gw),
    )
    return svc, gw


async def test_ingest_album_fetches_details_and_preserves_credits():
    deezer = FakeDeezer(album=DZ_ALBUM, tracks=DZ_TRACKS)
    svc, gw = _service(deezer)

    album_uuid = await svc.ingest_album(333)
    assert album_uuid == "alb-uuid"
    # Per-track detail fetched for both tracks (bounded, but all requested).
    assert sorted(deezer.track_calls) == [123, 124]

    # Find the album upsert payload that was sent to the RPC.
    album_call = next(p for name, p in gw.calls if name == "rpc:catalog_upsert_album_graph")
    payload = album_call["p"]
    assert payload["complete"] is True
    tracks_by_id = {t["deezer_id"]: t for t in payload["tracks"]}
    assert [a["name"] for a in tracks_by_id[123]["artists"]] == ["Young Miko", "Feid"]
    assert [a["name"] for a in tracks_by_id[124]["artists"]] == ["Young Miko"]

    # Artist-genre enrichment issued for the primary artist with the album genre.
    artist_calls = [p for name, p in gw.calls if name == "rpc:catalog_upsert_artist_graph"]
    assert any(g["slug"] == "latin"
               for c in artist_calls for g in c["p"]["artist"].get("genres", []))


async def test_ingest_album_tolerates_track_detail_failure():
    deezer = FakeDeezer(album=DZ_ALBUM, tracks={124: DZ_TRACKS[124]},
                        track_errors={123: DeezerUnavailable("boom")})
    svc, gw = _service(deezer)
    album_uuid = await svc.ingest_album(333)
    assert album_uuid == "alb-uuid"
    # Track 123 falls back to the tracklist entry (main artist only) — no crash.
    album_call = next(p for name, p in gw.calls if name == "rpc:catalog_upsert_album_graph")
    tracks_by_id = {t["deezer_id"]: t for t in album_call["p"]["tracks"]}
    assert [a["name"] for a in tracks_by_id[123]["artists"]] == ["Young Miko"]


async def test_ingest_track_full():
    deezer = FakeDeezer(tracks=DZ_TRACKS)
    svc, gw = _service(deezer)
    uuid = await svc.ingest_track(123)
    assert uuid == "trk-uuid"
    call = next(p for name, p in gw.calls if name == "rpc:catalog_upsert_track_graph")
    assert call["p"]["track"]["metadata_status"] == "full"
    assert len(call["p"]["track"]["artists"]) == 2


async def test_ingest_track_propagates_deezer_not_found():
    deezer = FakeDeezer(tracks={}, track_errors={999: DeezerNotFound("no data")})
    svc, _ = _service(deezer)
    with pytest.raises(DeezerNotFound):
        await svc.ingest_track(999)


# ── Phase 2.6: artist-profile discography attribution ────────────────────────

# Deezer /artist/27/albums entries OMIT the nested 'artist' field (the bug).
_DAFT_ARTIST = {"id": 27, "name": "Daft Punk", "nb_fan": 5000000}
_DAFT_ALBUMS = {"data": [
    {"id": 302, "title": "Discovery", "record_type": "album", "nb_tracks": 14},
    {"id": 303, "title": "Homework", "record_type": "album", "nb_tracks": 16},
]}


def _album_payloads(gw):
    return [p["p"] for name, p in gw.calls if name == "rpc:catalog_upsert_album_graph"]


async def test_ingest_artist_profile_attributes_albums_to_parent():
    deezer = FakeDeezer(artist=_DAFT_ARTIST, artist_albums=_DAFT_ALBUMS)
    svc, gw = _service(deezer)

    await svc.ingest_artist_profile(27)

    payloads = _album_payloads(gw)
    assert len(payloads) == 2
    for p in payloads:
        aa = p["album_artists"]
        # Every album attributed to Daft Punk; NO null-deezer "Unknown Artist".
        assert [a["deezer_id"] for a in aa] == [27]
        assert all(a["name"] == "Daft Punk" for a in aa)
        assert not any(a.get("deezer_id") is None for a in aa)


async def test_ingest_artist_profile_is_idempotent_on_attribution():
    deezer = FakeDeezer(artist=_DAFT_ARTIST, artist_albums=_DAFT_ALBUMS)
    svc, gw = _service(deezer)
    await svc.ingest_artist_profile(27)
    await svc.ingest_artist_profile(27)   # second run
    # Both runs emit identical attribution (no divergence / no placeholder).
    for p in _album_payloads(gw):
        assert [a["deezer_id"] for a in p["album_artists"]] == [27]


# ── Phase 3.3 §3: bounded-concurrency discography fan-out ─────────────────────

class _ConcurrencyProbeGateway(FakeGateway):
    """Records the peak number of concurrent album-graph upserts in flight."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_flight = 0
        self.peak = 0

    async def rpc(self, fn, params):
        if fn == "catalog_upsert_album_graph":
            self._in_flight += 1
            self.peak = max(self.peak, self._in_flight)
            await asyncio.sleep(0.01)  # hold the slot so overlap is observable
            try:
                return await super().rpc(fn, params)
            finally:
                self._in_flight -= 1
        return await super().rpc(fn, params)


async def test_ingest_artist_profile_bounds_discography_concurrency():
    many = {"data": [
        {"id": 1000 + i, "title": f"Album {i}", "record_type": "album", "nb_tracks": 10}
        for i in range(30)
    ]}
    deezer = FakeDeezer(artist=_DAFT_ARTIST, artist_albums=many)
    gw = _ConcurrencyProbeGateway({}, rpc_returns=_rpc_returns())
    svc = CatalogIngestionService(
        deezer, ArtistRepository(gw), AlbumRepository(gw), TrackRepository(gw),
        discography_concurrency=4,
    )

    await svc.ingest_artist_profile(27)

    # All 30 albums ingested…
    assert len(_album_payloads(gw)) == 30
    # …but never more than the configured bound concurrently, and it did fan out
    # beyond a serial loop (peak > 1).
    assert gw.peak <= 4
    assert gw.peak > 1
