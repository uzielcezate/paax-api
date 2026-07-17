"""PlaybackMatchingService: persistence, audio-preference, revalidation."""

import pytest

from schemas.catalog.common import MatchType, YoutubeMatchStatus
from services.catalog.jobs import BackgroundJobs
from services.catalog.playback_service import PlaybackMatchingService
from services.youtube.track_matcher import MatchResult, Slot
from repositories.catalog.track_repository import TrackRepository
from tests.conftest import FakeGateway


def _track_tables(**overrides):
    row = {
        "id": "trk-1", "deezer_id": 123, "title": "offline", "album_id": None,
        "duration_seconds": 180, "explicit": True,
        "youtube_match_status": "pending", "youtube_failure_count": 0,
        "metadata_status": "full",
    }
    row.update(overrides)
    return {
        "tracks": [row],
        "track_artists": [{"track_id": "trk-1", "artist_id": "art-1", "role": "primary",
                           "position": 1, "display_name": None}],
        "artists": [{"id": "art-1", "deezer_id": 111, "name": "Young Miko"}],
    }


def _matcher(result):
    async def _fn(meta):
        return result
    return _fn


def _service(gateway, result):
    return PlaybackMatchingService(
        track_repo=TrackRepository(gateway), jobs=BackgroundJobs(), matcher=_matcher(result),
    )


async def test_resolve_persists_audio_preferred():
    gw = FakeGateway(_track_tables())
    result = MatchResult(
        audio=Slot("aud1", MatchType.AUDIO_ONLY, 0.9, "topic"),
        music_video=Slot("mv1", MatchType.OFFICIAL_MUSIC_VIDEO, 0.8, "omv"),
        preferred=Slot("aud1", MatchType.AUDIO_ONLY, 0.9, "topic"),
        status=YoutubeMatchStatus.MATCHED,
    )
    svc = _service(gw, result)
    pb = await svc.resolve_and_persist("trk-1")

    assert pb["videoId"] == "aud1" and pb["preferredType"] == MatchType.AUDIO_ONLY
    assert pb["audioVideoId"] == "aud1" and pb["musicVideoId"] == "mv1"
    row = await gw.fetch_one("tracks", [("id", "eq", "trk-1")])
    assert row["preferred_youtube_video_id"] == "aud1"
    assert row["youtube_music_video_id"] == "mv1"
    assert row["youtube_match_status"] == "matched"
    assert row["youtube_failure_count"] == 0


async def test_never_replace_valid_audio_with_music_video():
    gw = FakeGateway(_track_tables(
        youtube_audio_video_id="oldaud", youtube_audio_match_type="audio_only",
        preferred_youtube_video_id="oldaud", youtube_match_status="matched",
        youtube_match_confidence=0.9,
    ))
    # Forced re-resolve finds ONLY a music video (no audio).
    result = MatchResult(audio=None,
                         music_video=Slot("mv2", MatchType.OFFICIAL_MUSIC_VIDEO, 0.85, "omv"),
                         preferred=Slot("mv2", MatchType.OFFICIAL_MUSIC_VIDEO, 0.85, "omv"),
                         status=YoutubeMatchStatus.MATCHED)
    svc = _service(gw, result)
    pb = await svc.resolve_and_persist("trk-1", force=True)
    # Audio preferred is preserved; MV only stored in its own slot.
    assert pb["videoId"] == "oldaud"
    assert pb["musicVideoId"] == "mv2"


async def test_resolve_honors_existing_match_without_force():
    gw = FakeGateway(_track_tables(
        preferred_youtube_video_id="keep", youtube_audio_video_id="keep",
        youtube_match_status="matched", youtube_failure_count=0,
    ))
    called = {"n": 0}

    async def matcher(meta):
        called["n"] += 1
        return MatchResult(None, None, None, YoutubeMatchStatus.FAILED)

    svc = PlaybackMatchingService(track_repo=TrackRepository(gw), jobs=BackgroundJobs(),
                                  matcher=matcher)
    pb = await svc.resolve_and_persist("trk-1")
    assert pb["videoId"] == "keep"
    assert called["n"] == 0            # matcher not invoked for a valid match


async def test_report_failure_increments_then_reresolves():
    gw = FakeGateway(_track_tables())
    result = MatchResult(None, None, None, YoutubeMatchStatus.FAILED)  # no replacement found
    svc = _service(gw, result)
    await svc.report_failure("trk-1")
    row = await gw.fetch_one("tracks", [("id", "eq", "trk-1")])
    assert row["youtube_failure_count"] == 1
    assert row["youtube_match_status"] == "failed"     # re-resolve found nothing


async def test_report_failure_gives_up_at_threshold():
    gw = FakeGateway(_track_tables(youtube_failure_count=2))
    called = {"n": 0}

    async def matcher(meta):
        called["n"] += 1
        return MatchResult(None, None, None, YoutubeMatchStatus.FAILED)

    svc = PlaybackMatchingService(track_repo=TrackRepository(gw), jobs=BackgroundJobs(),
                                  matcher=matcher)
    await svc.report_failure("trk-1")
    row = await gw.fetch_one("tracks", [("id", "eq", "trk-1")])
    assert row["youtube_failure_count"] == 3
    assert row["youtube_match_status"] == "unavailable"
    assert called["n"] == 0            # no re-resolve after giving up


async def test_missing_match_does_not_break_metadata():
    gw = FakeGateway(_track_tables())
    result = MatchResult(None, None, None, YoutubeMatchStatus.FAILED)
    svc = _service(gw, result)
    pb = await svc.resolve_and_persist("trk-1")
    assert pb["videoId"] is None and pb["matchStatus"] == "failed"
    # The catalog track row is untouched apart from youtube_* fields.
    row = await gw.fetch_one("tracks", [("id", "eq", "trk-1")])
    assert row["title"] == "offline" and row["metadata_status"] == "full"
