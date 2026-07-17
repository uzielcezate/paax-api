"""Album + artist repository unit tests (graph load, per-track credits, discography order)."""

import pytest

from repositories.catalog.album_repository import AlbumRepository
from repositories.catalog.artist_repository import ArtistRepository


async def test_album_with_tracks_preserves_per_track_credits(gateway):
    repo = AlbumRepository(gateway)
    album = await repo.get_album_with_tracks("alb-att")

    assert album is not None
    assert album.title == "att."
    assert album.album_type == "album"
    # Album header artist is Young Miko only...
    assert [a.name for a in album.artists] == ["Young Miko"]
    # ...but the "offline" track keeps its real collaborators (Miko + Feid).
    offline = next(t for t in album.tracks if t.deezer_id == 123)
    assert [a.name for a in offline.artists] == ["Young Miko", "Feid"]
    # Tracks are ordered by (disc, track_number): solo (#1) before offline (#6).
    assert [t.id for t in album.tracks] == ["trk-solo", "trk-offline"]
    # Album genres present.
    assert {g.name for g in album.genres} == {"Latin", "Reggaeton"}


async def test_album_by_deezer_id(gateway):
    repo = AlbumRepository(gateway)
    album = await repo.get_album_by_deezer_id(333)
    assert album is not None and album.id == "alb-att"


async def test_artist_releases_sorted_newest_first(gateway):
    repo = ArtistRepository(gateway)
    artist = await repo.get_artist_with_releases("art-miko")

    assert artist is not None
    assert artist.name == "Young Miko"
    # Miko credits: att.(2024), trap kitty(2022), classy 101 single(2023 featured).
    dates = [r.release_date for r in artist.releases]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == "2024-04-05"      # newest first
    assert artist.deezer_fans_count == 500000
    assert {g.name for g in artist.genres} == {"Latin"}


async def test_missing_artist_returns_none(gateway):
    repo = ArtistRepository(gateway)
    assert await repo.get_artist_by_id("nope") is None
