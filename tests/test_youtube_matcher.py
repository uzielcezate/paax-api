"""YouTube candidate classifier + two-slot matcher tests."""

import pytest

from schemas.catalog.common import MatchType, YoutubeMatchStatus
from services.youtube.candidate_classifier import classify
from services.youtube.track_matcher import match_track


# ── classifier ───────────────────────────────────────────────────────────────

def test_classify_topic_is_audio_only():
    t, rejected, _ = classify("offline", "Young Miko - Topic", "offline")
    assert t == MatchType.AUDIO_ONLY and not rejected


def test_classify_official_audio_and_lyric_and_mv():
    assert classify("offline (Official Audio)", "ch", "offline")[0] == MatchType.AUDIO_ONLY
    assert classify("offline (Lyric Video)", "ch", "offline")[0] == MatchType.LYRIC_VIDEO
    assert classify("Young Miko - offline (Official Video)", "YoungMikoVEVO", "offline")[0] == MatchType.OFFICIAL_MUSIC_VIDEO


def test_classify_rejects_live_cover_remix_when_absent_from_title():
    assert classify("offline (Live)", "ch", "offline")[1] is True
    assert classify("offline (cover)", "ch", "offline")[1] is True
    assert classify("offline (Remix)", "ch", "offline")[1] is True


def test_classify_accepts_remix_when_in_deezer_title():
    t, rejected, _ = classify("offline (Remix)", "ch", "offline (Remix)")
    assert rejected is False


# ── matcher ──────────────────────────────────────────────────────────────────

def _search_fn(candidates):
    async def _fn(query, n):
        return candidates
    return _fn


META = {"title": "offline", "primary_artist": "Young Miko",
        "display_artists": "Young Miko, Feid", "duration": 180, "explicit": True}


async def test_matcher_prefers_audio_over_music_video():
    candidates = [
        {"videoId": "topic1", "title": "offline", "channel": "Young Miko - Topic", "duration": 180},
        {"videoId": "mv1", "title": "Young Miko - offline (Official Video)", "channel": "YoungMikoVEVO", "duration": 182},
        {"videoId": "live1", "title": "offline (Live)", "channel": "x", "duration": 181},
        {"videoId": "cover1", "title": "offline (cover)", "channel": "y", "duration": 180},
    ]
    result = await match_track(META, search_fn=_search_fn(candidates))
    assert result.audio.video_id == "topic1"
    assert result.audio.match_type == MatchType.AUDIO_ONLY
    assert result.music_video.video_id == "mv1"
    assert result.preferred.video_id == "topic1"          # audio-first
    assert result.status == YoutubeMatchStatus.MATCHED


async def test_matcher_falls_back_to_music_video_when_no_audio():
    candidates = [
        {"videoId": "mv1", "title": "Young Miko - offline (Official Music Video)", "channel": "YoungMikoVEVO", "duration": 181},
    ]
    result = await match_track(META, search_fn=_search_fn(candidates))
    assert result.audio is None
    assert result.preferred.video_id == "mv1"
    assert result.preferred.match_type == MatchType.OFFICIAL_MUSIC_VIDEO


async def test_matcher_rejects_on_duration_mismatch():
    candidates = [
        {"videoId": "long1", "title": "offline", "channel": "Young Miko - Topic", "duration": 320},
    ]
    result = await match_track(META, search_fn=_search_fn(candidates))
    assert result.preferred is None
    assert result.status == YoutubeMatchStatus.FAILED
