"""
Hybrid search: Deezer metadata + YouTube video ID matching.
"""

import asyncio
from typing import Dict, Any, List

from ..deezer.deezer_client import DeezerClient
from ..deezer.deezer_mapper import map_track, map_album, map_artist
from ..youtube.youtube_matcher import match_track

# Limit concurrent YouTube searches
_YT_SEMAPHORE = asyncio.Semaphore(3)


async def hybrid_search_tracks(
    deezer: DeezerClient, query: str, limit: int = 25
) -> Dict[str, Any]:
    """
    Search Deezer for tracks, then resolve YouTube video IDs for each.

    Returns ``{"data": [mapped_track, ...]}``
    """
    dz_result = await deezer.search_tracks(query, limit=limit)
    dz_tracks = dz_result.get("data") or []

    if not dz_tracks:
        return {"data": []}

    # Resolve YouTube matches with controlled concurrency
    async def resolve_one(dz_track: Dict) -> Dict[str, Any]:
        artist = (dz_track.get("artist") or {}).get("name", "")
        title = dz_track.get("title") or ""
        album_title = (dz_track.get("album") or {}).get("title", "")
        duration = dz_track.get("duration") or 0
        explicit = dz_track.get("explicit_lyrics", False)

        try:
            async with _YT_SEMAPHORE:
                playback = await asyncio.wait_for(
                    match_track(artist, title, album_title, duration, explicit),
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

        # Add provider/engine to playback
        playback.setdefault("provider", "youtube")
        playback.setdefault("engine", "iframe")

        return map_track(dz_track, playback=playback)

    mapped = await asyncio.gather(
        *[resolve_one(t) for t in dz_tracks],
        return_exceptions=True,
    )

    # Filter out exceptions
    results = []
    for item in mapped:
        if isinstance(item, Exception):
            print(f"[HybridSearch] Track resolve error: {item}")
            continue
        results.append(item)

    return {"data": results}


async def hybrid_search_albums(
    deezer: DeezerClient, query: str, limit: int = 25
) -> Dict[str, Any]:
    """
    Search Deezer for albums (no YouTube matching needed for album cards).
    """
    dz_result = await deezer.search_albums(query, limit=limit)
    dz_albums = dz_result.get("data") or []
    return {"data": [map_album(a) for a in dz_albums]}


async def hybrid_search_artists(
    deezer: DeezerClient, query: str, limit: int = 25
) -> Dict[str, Any]:
    """
    Search Deezer for artists (no YouTube matching needed).
    """
    dz_result = await deezer.search_artists(query, limit=limit)
    dz_artists = dz_result.get("data") or []
    return {"data": [map_artist(a) for a in dz_artists]}
