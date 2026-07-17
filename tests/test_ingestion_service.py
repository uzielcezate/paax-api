"""Ingestion service tests (per-track detail fetch, credit preservation, errors)."""

import pytest

from ingestion.catalog_ingestion_service import CatalogIngestionService
from repositories.catalog.album_repository import AlbumRepository
from repositories.catalog.artist_repository import ArtistRepository
from repositories.catalog.track_repository import TrackRepository
from services.deezer.errors import DeezerNotFound, DeezerUnavailable
from tests.conftest import FakeGateway


class FakeDeezer:
    def __init__(self, album=None, tracks=None, artist=None, track_errors=None):
        self._album = album
        self._tracks = tracks or {}
        self._artist = artist
        self._track_errors = track_errors or {}
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
