"""
Hybrid album service: Deezer album metadata + YouTube video IDs for tracks.
"""

import asyncio
from typing import Dict, Any

from ..deezer.deezer_client import DeezerClient
from ..deezer.deezer_mapper import map_album, map_album_track
from ..youtube.youtube_matcher import match_track

# Limit concurrent YouTube searches to avoid thread pool starvation
_YT_SEMAPHORE = asyncio.Semaphore(3)


async def get_hybrid_album(
    deezer: DeezerClient, album_id: int
) -> Dict[str, Any]:
    """
    Fetch album details from Deezer, then resolve YouTube video IDs for each track.
    """
    dz_album = await deezer.get_album(album_id)

    # ── Map album metadata ────────────────────────────────────────────────
    album_mapped = map_album(dz_album)

    # Album-level info for track enrichment
    album_info = {
        "id": dz_album.get("id"),
        "title": dz_album.get("title", ""),
        "coverUrl": album_mapped.get("coverUrl", ""),
        "artist": (dz_album.get("artist") or {}),
    }

    # ── Get tracklist ─────────────────────────────────────────────────────
    tracks_data = []
    if "tracks" in dz_album and "data" in dz_album["tracks"]:
        tracks_data = dz_album["tracks"]["data"]
    elif "tracks" in dz_album and isinstance(dz_album["tracks"], list):
        tracks_data = dz_album["tracks"]

    if not tracks_data:
        album_mapped["tracks"] = []
        return album_mapped

    # ── Resolve YouTube IDs with controlled concurrency ───────────────────
    async def resolve_track(dz_track: Dict) -> Dict[str, Any]:
        artist_name = (dz_track.get("artist") or album_info.get("artist") or {}).get("name", "")
        title = dz_track.get("title", "")
        album_title = album_info.get("title", "")
        duration = dz_track.get("duration", 0)
        explicit = dz_track.get("explicit_lyrics", False)

        try:
            async with _YT_SEMAPHORE:
                playback = await asyncio.wait_for(
                    match_track(artist_name, title, album_title, duration, explicit),
                    timeout=30.0,
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
        return map_album_track(dz_track, album_info, playback=playback)

    mapped_tracks_raw = await asyncio.gather(
        *[resolve_track(t) for t in tracks_data],
        return_exceptions=True,
    )
    mapped_tracks = [
        t for t in mapped_tracks_raw if not isinstance(t, Exception)
    ]

    album_mapped["tracks"] = mapped_tracks
    return album_mapped
