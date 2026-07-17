"""Track repository unit tests (fresh lookup, multi-artist, ISRC, YT match write)."""

import pytest

from repositories.catalog.track_repository import TrackRepository


async def test_get_track_by_id_multi_artist(gateway):
    repo = TrackRepository(gateway)
    g = await repo.get_track_by_id("trk-offline")

    assert g is not None
    assert g.deezer_id == 123
    # Its OWN collaborators — never derived from the album header.
    assert [a.name for a in g.artists] == ["Young Miko", "Feid"]
    assert [a.role for a in g.artists] == ["primary", "featured"]
    assert g.artist_display_name == "Young Miko, Feid"
    assert g.album is not None and g.album.title == "att."
    assert [gg.name for gg in g.genres] == ["Latin"]
    # Preferred playback defaults to the audio-only match.
    assert g.playback.preferred_youtube_video_id == "aud123"
    assert g.playback.preferred_type == "audio_only"


async def test_get_track_by_deezer_id(gateway):
    repo = TrackRepository(gateway)
    g = await repo.get_track_by_deezer_id(123)
    assert g is not None and g.id == "trk-offline"


async def test_get_track_by_isrc(gateway):
    repo = TrackRepository(gateway)
    g = await repo.get_track_by_isrc("USABC2400001")
    assert g is not None and g.id == "trk-offline"


async def test_missing_track_returns_none(gateway):
    repo = TrackRepository(gateway)
    assert await repo.get_track_by_id("does-not-exist") is None
    assert await repo.get_track_by_deezer_id(999999) is None


async def test_solo_track_single_artist(gateway):
    repo = TrackRepository(gateway)
    g = await repo.get_track_by_id("trk-solo")
    assert [a.name for a in g.artists] == ["Young Miko"]
    assert g.artist_display_name == "Young Miko"


async def test_update_youtube_match_whitelists_columns(gateway):
    repo = TrackRepository(gateway)
    await repo.update_youtube_match("trk-solo", {
        "youtube_audio_video_id": "newaud",
        "preferred_youtube_video_id": "newaud",
        "youtube_audio_match_type": "audio_only",
        "youtube_match_status": "matched",
        "youtube_match_confidence": 0.8,
        "title": "HACKED",          # must be ignored — not a youtube_* column
        "metadata_status": "failed",  # must be ignored
    })
    row = await gateway.fetch_one("tracks", [("id", "eq", "trk-solo")])
    assert row["youtube_audio_video_id"] == "newaud"
    assert row["youtube_match_status"] == "matched"
    assert row["title"] == "solo track"       # unchanged
    assert row["metadata_status"] == "full"    # unchanged
