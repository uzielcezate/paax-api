"""
Normalized search / home / chart item builders.

Two sources feed the same normalized item shape:
  * local  — rows from the catalog_search RPC / stored catalog (have internal id
             and, for tracks, a persisted playback videoId);
  * deezer — raw Deezer objects surfaced for discovery (deezerId only until
             background ingestion persists them).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from mappers.deezer_common import best_image


# ── local (stored) rows ────────────────────────────────────────────────────

def local_track_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "track",
        "id": row.get("id"),
        "deezerId": row.get("deezer_id"),
        "title": row.get("title"),
        "artistName": row.get("primary_artist_name") or "",
        "coverUrl": row.get("image_cached_url") or row.get("image_original_url"),
        "durationSeconds": row.get("duration_seconds"),
        "explicit": bool(row.get("explicit")),
        "playback": {
            "provider": "youtube", "engine": "iframe",
            "videoId": row.get("preferred_youtube_video_id"),
            "matchStatus": row.get("youtube_match_status") or "pending",
        },
        "source": "catalog",
    }


def local_album_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "album",
        "id": row.get("id"),
        "deezerId": row.get("deezer_id"),
        "title": row.get("title"),
        "artistName": row.get("primary_artist_name") or "",
        "coverUrl": row.get("cover_cached_url") or row.get("cover_original_url"),
        "releaseDate": str(row["release_date"]) if row.get("release_date") else None,
        "albumType": row.get("album_type"),
        "source": "catalog",
    }


def local_artist_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "artist",
        "id": row.get("id"),
        "deezerId": row.get("deezer_id"),
        "name": row.get("name"),
        "imageUrl": row.get("image_cached_url") or row.get("image_original_url"),
        "source": "catalog",
    }


def local_item(entity_type: str, row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tracks": local_track_item,
        "albums": local_album_item,
        "artists": local_artist_item,
    }[entity_type](row)


# ── deezer (discovery) objects ─────────────────────────────────────────────

def deezer_track_item(dz: Dict[str, Any]) -> Dict[str, Any]:
    album = dz.get("album") or {}
    artist = dz.get("artist") or {}
    return {
        "type": "track",
        "id": None,
        "deezerId": dz.get("id"),
        "title": dz.get("title"),
        "artistName": artist.get("name") or "",
        "coverUrl": best_image(album, "cover"),
        "durationSeconds": dz.get("duration"),
        "explicit": bool(dz.get("explicit_lyrics", False)),
        "playback": {"provider": "youtube", "engine": "iframe", "videoId": None, "matchStatus": "pending"},
        "source": "deezer",
    }


def deezer_album_item(dz: Dict[str, Any]) -> Dict[str, Any]:
    artist = dz.get("artist") or {}
    return {
        "type": "album",
        "id": None,
        "deezerId": dz.get("id"),
        "title": dz.get("title"),
        "artistName": artist.get("name") or "",
        "coverUrl": best_image(dz, "cover"),
        "releaseDate": dz.get("release_date") or None,
        "albumType": dz.get("record_type"),
        "source": "deezer",
    }


def deezer_artist_item(dz: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "artist",
        "id": None,
        "deezerId": dz.get("id"),
        "name": dz.get("name"),
        "imageUrl": best_image(dz, "picture"),
        "source": "deezer",
    }


def deezer_item(entity_type: str, dz: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tracks": deezer_track_item,
        "albums": deezer_album_item,
        "artists": deezer_artist_item,
    }[entity_type](dz)
