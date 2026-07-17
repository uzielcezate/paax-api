"""
Two-slot YouTube matcher.

From canonical catalog metadata it resolves, in one pass:
  * the best AUDIO slot  (Topic / official-audio / album-art / lyric fallback),
  * the best MUSIC-VIDEO slot (official MV / VEVO),
and computes the preferred playback source as audio-first (the mandatory rule).

Pure w.r.t. persistence: it only reads YouTube search results (via an injectable
search function) and returns a structured result. Persistence lives in
services/catalog/playback_service.py.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from schemas.catalog.common import MatchType, YoutubeMatchStatus
from services.youtube.candidate_classifier import AUDIO_TYPES, classify
from services.youtube.youtube_search import search_youtube

SearchFn = Callable[[str, int], Awaitable[List[Dict[str, Any]]]]

_MATCH_THRESHOLD = 0.5
_REVIEW_THRESHOLD = 0.3

_NOISE_RE = re.compile(
    r"\((official\s*)?(audio|video|music\s*video|lyric[s]?\s*video|visualizer)\)|"
    r"\[(official\s*)?(audio|video|music\s*video|lyric[s]?\s*video)\]|"
    r"\bofficial\s*(audio|video|music\s*video)\b|\(audio\)|\(lyrics?\)",
    re.IGNORECASE,
)

# Per-type additive bonus so, at equal base score, audio_only > lyric > mv > other.
_TYPE_BONUS = {
    MatchType.AUDIO_ONLY: 12,
    MatchType.LYRIC_VIDEO: 6,
    MatchType.OFFICIAL_MUSIC_VIDEO: 8,
    MatchType.OTHER: 0,
}


@dataclass
class Slot:
    video_id: str
    match_type: str
    confidence: float
    reason: str


@dataclass
class MatchResult:
    audio: Optional[Slot]
    music_video: Optional[Slot]
    preferred: Optional[Slot]
    status: str

    @property
    def preferred_video_id(self) -> Optional[str]:
        return self.preferred.video_id if self.preferred else None


def _clean(title: str) -> str:
    return re.sub(r"\s+", " ", _NOISE_RE.sub("", title or "")).strip().lower()


def _build_queries(primary_artist: str, title: str, display_artists: str) -> List[str]:
    q = [
        f"{primary_artist} - {title} official audio",
        f"{primary_artist} {title} topic",
        f"{display_artists} {title}",
    ]
    # de-dupe preserving order
    seen, out = set(), []
    for s in q:
        s = s.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _score(cand: Dict[str, Any], *, dz_title: str, primary_artist: str,
           duration: int, match_type: str) -> Optional[float]:
    yt_title = (cand.get("title") or "").lower()
    yt_channel = (cand.get("channel") or "").lower()
    yt_dur = cand.get("duration") or 0
    artist = (primary_artist or "").lower()

    # duration (0-35), hard reject > 60s off
    dur_score = 0.0
    if duration > 0 and yt_dur > 0:
        diff = abs(duration - yt_dur)
        if diff > 60:
            return None
        if diff <= 3:
            dur_score = 35
        elif diff <= 10:
            dur_score = 28
        elif diff <= 20:
            dur_score = 18
        elif diff <= 30:
            dur_score = 10
        else:
            dur_score = 5
    elif duration > 0:
        dur_score = 10

    title_ratio = difflib.SequenceMatcher(None, (dz_title or "").lower(), _clean(yt_title)).ratio()
    title_score = title_ratio * 35

    if artist and (artist in yt_title or artist in yt_channel):
        artist_score = 20.0
    else:
        head = yt_title.split(" - ")[0] if " - " in yt_title else ""
        artist_score = max(
            difflib.SequenceMatcher(None, artist, yt_channel).ratio(),
            difflib.SequenceMatcher(None, artist, head).ratio(),
        ) * 15

    trust = 7 if yt_channel.endswith("- topic") else (3 if "vevo" in yt_channel else 0)
    return dur_score + title_score + artist_score + _TYPE_BONUS.get(match_type, 0) + trust


async def match_track(meta: Dict[str, Any], *, search_fn: SearchFn = search_youtube,
                      max_candidates: int = 12) -> MatchResult:
    """
    ``meta``: {title, primary_artist, display_artists, duration, explicit}.
    Returns a MatchResult with audio/music-video/preferred slots.
    """
    title = meta.get("title") or ""
    primary = meta.get("primary_artist") or ""
    display = meta.get("display_artists") or primary
    duration = int(meta.get("duration") or 0)

    seen: set = set()
    candidates: List[Dict[str, Any]] = []
    for query in _build_queries(primary, title, display):
        for r in await search_fn(query, 5):
            vid = r.get("videoId")
            if vid and vid not in seen:
                seen.add(vid)
                candidates.append(r)
        if len(candidates) >= max_candidates:
            break

    best_audio: Optional[Slot] = None
    best_audio_score = -1.0
    best_mv: Optional[Slot] = None
    best_mv_score = -1.0

    for cand in candidates:
        mtype, rejected, reason = classify(cand.get("title", ""), cand.get("channel", ""), title)
        if rejected:
            continue
        score = _score(cand, dz_title=title, primary_artist=primary,
                       duration=duration, match_type=mtype)
        if score is None:
            continue
        confidence = round(min(score / 100.0, 1.0), 3)
        slot = Slot(cand["videoId"], mtype, confidence, f"{reason} ({score:.0f})")

        if mtype in AUDIO_TYPES and score > best_audio_score:
            best_audio, best_audio_score = slot, score
        if mtype == MatchType.OFFICIAL_MUSIC_VIDEO and score > best_mv_score:
            best_mv, best_mv_score = slot, score

    # Preferred: audio-first (mandatory rule); fall back to MV only if no audio.
    preferred = best_audio or best_mv
    if preferred is None:
        status = YoutubeMatchStatus.FAILED
    elif preferred.confidence >= _MATCH_THRESHOLD:
        status = YoutubeMatchStatus.MATCHED
    elif preferred.confidence >= _REVIEW_THRESHOLD:
        status = YoutubeMatchStatus.NEEDS_REVIEW
    else:
        status = YoutubeMatchStatus.FAILED
        preferred = None

    return MatchResult(audio=best_audio, music_video=best_mv, preferred=preferred, status=status)
