"""
Hybrid artist service: Deezer artist profile + YouTube video IDs for top tracks.
"""

import asyncio
import os
from typing import Dict, Any, List

from ..deezer.deezer_client import DeezerClient
from ..deezer.deezer_mapper import map_track, map_album, map_artist
from ..youtube.youtube_matcher import match_track

# Limit concurrent YouTube searches
_YT_SEMAPHORE = asyncio.Semaphore(3)

# Phase 3.3.1 §2: the artist page shows only a handful of "Popular" tracks, but
# this endpoint used to eagerly YouTube-match up to 50 (semaphore 3, 30s each),
# which dominated cold-artist latency. Match a bounded head of the top list and
# use a tighter per-track timeout; the rest can be matched on demand later.
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default

_TOP_MATCH_LIMIT = _int_env("HYBRID_ARTIST_TOP_MATCH_LIMIT", 15)
_TOP_MATCH_TIMEOUT = _int_env("HYBRID_ARTIST_TOP_MATCH_TIMEOUT_SECONDS", 12)


async def get_hybrid_artist(
    deezer: DeezerClient, artist_id: int
) -> Dict[str, Any]:
    """
    Fetch a complete artist profile from Deezer with YouTube-matched top tracks.

    Fetches artist info, top tracks, albums, and related artists in parallel.
    Then resolves YouTube IDs for top tracks concurrently.
    """
    # ── Parallel Deezer fetches ───────────────────────────────────────────
    artist_fut = deezer.get_artist(artist_id)
    top_fut = deezer.get_artist_top(artist_id, limit=max(_TOP_MATCH_LIMIT, 1))
    albums_fut = deezer.get_artist_albums(artist_id, limit=100)
    related_fut = deezer.get_artist_related(artist_id)

    artist_raw, top_raw, albums_raw, related_raw = await asyncio.gather(
        artist_fut, top_fut, albums_fut, related_fut,
        return_exceptions=True,
    )

    # Handle errors gracefully
    if isinstance(artist_raw, Exception):
        raise artist_raw
    top_tracks_data = top_raw.get("data", []) if not isinstance(top_raw, Exception) else []
    # Only YouTube-match a bounded head (the artist page shows a handful of
    # "Popular" tracks). Slicing here caps the eager-match fan-out regardless of
    # how many Deezer returned (Phase 3.3.1 §2 / review L2).
    top_tracks_data = top_tracks_data[:_TOP_MATCH_LIMIT]
    albums_data = albums_raw.get("data", []) if not isinstance(albums_raw, Exception) else []
    related_data = related_raw.get("data", []) if not isinstance(related_raw, Exception) else []

    # ── Map artist profile ────────────────────────────────────────────────
    artist_mapped = map_artist(artist_raw)

    # ── Resolve YouTube IDs for top tracks ────────────────────────────────
    async def resolve_track(dz_track: Dict) -> Dict[str, Any]:
        artist_name = (dz_track.get("artist") or {}).get("name", "")
        title = dz_track.get("title", "")
        album_title = (dz_track.get("album") or {}).get("title", "")
        duration = dz_track.get("duration", 0)
        explicit = dz_track.get("explicit_lyrics", False)

        try:
            async with _YT_SEMAPHORE:
                playback = await asyncio.wait_for(
                    match_track(artist_name, title, album_title, duration, explicit),
                    timeout=float(_TOP_MATCH_TIMEOUT),
                )
        except asyncio.TimeoutError:
            playback = {
                "provider": "youtube",
                "engine": "iframe",
                "videoId": None,
                "matchConfidence": 0.0,
                "matchStatus": "timeout",
            }

        playback.setdefault("provider", "youtube")
        playback.setdefault("engine", "iframe")
        return map_track(dz_track, playback=playback)

    # Resolve all top tracks concurrently (with semaphore)
    mapped_tracks_raw = await asyncio.gather(
        *[resolve_track(t) for t in top_tracks_data],
        return_exceptions=True,
    )
    mapped_tracks = [
        t for t in mapped_tracks_raw if not isinstance(t, Exception)
    ]

    # ── Map albums (classify into albums vs singles/EPs) ──────────────────
    all_releases = [map_album(a) for a in albums_data]
    albums = [r for r in all_releases if r.get("type") == "album"]
    singles = [r for r in all_releases if r.get("type") in ("single", "ep")]

    # ── Map related artists ───────────────────────────────────────────────
    related = [map_artist(a) for a in related_data]

    return {
        **artist_mapped,
        "topTracks": mapped_tracks,
        "albums": albums,
        "singles": singles,
        "relatedArtists": related,
        "source": "deezer",
    }
