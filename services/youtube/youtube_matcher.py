"""
YouTube video ID matcher for Deezer tracks.

Searches YouTube with multiple query strategies and scores candidates
on duration closeness, title similarity, and channel trust signals.
"""

import re
import difflib
from typing import Optional, Dict, Any, List

from .youtube_search import search_youtube
from .youtube_cache import get_cached_match, set_cached_match

# ── Rejection keywords ────────────────────────────────────────────────────────
# Reject candidates containing these unless the Deezer title also contains them.
_REJECT_KEYWORDS = {
    "live", "en vivo", "in concert", "concert",
    "cover", "covers",
    "remix", "remixed",
    "sped up", "speed up",
    "slowed", "slow",
    "nightcore",
    "karaoke",
    "instrumental",
    "8d audio", "8d",
    "bass boosted",
    "reverb",
    "clean version",         # only reject if Deezer title is explicit
    "reaction",
    "tutorial",
    "drum cover",
    "guitar cover",
    "piano cover",
}

# ── Trust signals ─────────────────────────────────────────────────────────────
_TOPIC_SUFFIX = " - Topic"
_OFFICIAL_PATTERNS = re.compile(
    r"official\s*(audio|music\s*video|video|lyric\s*video|visualizer)",
    re.IGNORECASE,
)


async def match_track(
    artist_name: str,
    track_title: str,
    album_title: str = "",
    duration_seconds: int = 0,
    is_explicit: bool = False,
) -> Dict[str, Any]:
    """
    Find the best YouTube video ID for a Deezer track.

    Returns::

        {
            "videoId": str | None,
            "matchConfidence": float,   # 0.0 – 1.0
            "matchStatus": "matched" | "failed" | "low_confidence",
            "matchReason": str,
        }
    """
    # ── Check cache first ─────────────────────────────────────────────────
    cached = await get_cached_match(artist_name, track_title, duration_seconds)
    if cached is not None:
        return cached

    # ── Build search queries (priority order) ─────────────────────────────
    queries = [
        f"{artist_name} - {track_title} official audio",
        f"{artist_name} {track_title} topic",
        f"{artist_name} {track_title}",
    ]

    all_candidates: List[Dict[str, Any]] = []
    seen_ids = set()

    for query in queries:
        results = await search_youtube(query, max_results=5)
        for r in results:
            vid = r.get("videoId", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                all_candidates.append(r)
        # If we have enough candidates, stop searching
        if len(all_candidates) >= 10:
            break

    if not all_candidates:
        result = _no_match("no YouTube results found")
        await set_cached_match(artist_name, track_title, duration_seconds, result)
        return result

    # ── Score candidates ──────────────────────────────────────────────────
    dz_title_lower = track_title.lower()
    dz_artist_lower = artist_name.lower()

    scored: List[tuple] = []  # (score, candidate, reason)
    for cand in all_candidates:
        score, reason = _score_candidate(
            cand,
            dz_title_lower=dz_title_lower,
            dz_artist_lower=dz_artist_lower,
            dz_duration=duration_seconds,
            dz_explicit=is_explicit,
        )
        if score is not None:  # None means rejected
            scored.append((score, cand, reason))

    if not scored:
        result = _no_match("all candidates rejected")
        await set_cached_match(artist_name, track_title, duration_seconds, result)
        return result

    # Pick the highest scoring candidate
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_cand, best_reason = scored[0]

    confidence = min(best_score / 100.0, 1.0)
    status = "matched" if confidence >= 0.5 else "low_confidence"

    result = {
        "videoId": best_cand["videoId"],
        "matchConfidence": round(confidence, 3),
        "matchStatus": status,
        "matchReason": best_reason,
    }

    await set_cached_match(artist_name, track_title, duration_seconds, result)
    return result


def _score_candidate(
    cand: Dict[str, Any],
    dz_title_lower: str,
    dz_artist_lower: str,
    dz_duration: int,
    dz_explicit: bool,
) -> tuple:
    """
    Score a YouTube candidate against a Deezer track.

    Returns (score, reason) or (None, reason) if rejected.
    Score is 0–100 scale.
    """
    yt_title = (cand.get("title") or "").lower()
    yt_channel = (cand.get("channel") or "").lower()
    yt_duration = cand.get("duration") or 0

    # ── Rejection pass ────────────────────────────────────────────────────
    for keyword in _REJECT_KEYWORDS:
        if keyword in yt_title and keyword not in dz_title_lower:
            return (None, f"rejected: '{keyword}' in YT title")

    # ── Duration scoring (0–35 points) ────────────────────────────────────
    dur_score = 0
    if dz_duration > 0 and yt_duration > 0:
        diff = abs(dz_duration - yt_duration)
        if diff > 60:
            return (None, f"rejected: duration diff {diff}s > 60s")
        elif diff <= 3:
            dur_score = 35
        elif diff <= 10:
            dur_score = 28
        elif diff <= 20:
            dur_score = 18
        elif diff <= 30:
            dur_score = 10
        else:
            dur_score = 5
    elif dz_duration > 0:
        dur_score = 10  # No YT duration available — neutral

    # ── Title similarity (0–35 points) ────────────────────────────────────
    # Clean the YT title of common noise
    yt_clean = _clean_yt_title(yt_title)
    title_ratio = difflib.SequenceMatcher(None, dz_title_lower, yt_clean).ratio()
    title_score = title_ratio * 35

    # ── Artist match (0–20 points) ────────────────────────────────────────
    artist_score = 0
    # Check if artist name appears in YT title or channel
    if dz_artist_lower in yt_title or dz_artist_lower in yt_channel:
        artist_score = 20
    else:
        # Partial match
        artist_ratio = max(
            difflib.SequenceMatcher(None, dz_artist_lower, yt_channel).ratio(),
            difflib.SequenceMatcher(None, dz_artist_lower, yt_title.split(" - ")[0] if " - " in yt_title else "").ratio(),
        )
        artist_score = artist_ratio * 15

    # ── Trust signals (0–10 bonus) ────────────────────────────────────────
    trust_score = 0
    if yt_channel.endswith(_TOPIC_SUFFIX.lower()):
        trust_score += 7  # Auto-generated Topic channel — very reliable
    if _OFFICIAL_PATTERNS.search(cand.get("title", "")):
        trust_score += 5  # "Official Audio" in title
    if "vevo" in yt_channel:
        trust_score += 3

    total = dur_score + title_score + artist_score + trust_score
    reason = (
        f"dur={dur_score:.0f} title={title_score:.0f} "
        f"artist={artist_score:.0f} trust={trust_score:.0f}"
    )
    return (total, reason)


def _clean_yt_title(title: str) -> str:
    """Remove common YouTube noise from a title for comparison."""
    noise_patterns = [
        r"\(official\s*(audio|video|music\s*video|lyric\s*video|visualizer)\)",
        r"\[official\s*(audio|video|music\s*video|lyric\s*video|visualizer)\]",
        r"\(audio\s*oficial\)",
        r"\(video\s*oficial\)",
        r"\(lyrics?\)",
        r"\(audio\)",
        r"\(hd\)",
        r"\(4k\)",
        r"\(remastered\s*\d*\)",
        r"- remastered\s*\d*",
        r"\bofficial\s*(audio|video)\b",
    ]
    result = title
    for pat in noise_patterns:
        result = re.sub(pat, "", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


def _no_match(reason: str) -> Dict[str, Any]:
    return {
        "videoId": None,
        "matchConfidence": 0.0,
        "matchStatus": "failed",
        "matchReason": reason,
    }
