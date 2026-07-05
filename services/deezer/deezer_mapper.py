"""
Normalizes raw Deezer API JSON into the response shapes expected by
the Paax frontend.
"""

from typing import Optional, Dict, Any, List


def map_track(dz: Dict[str, Any], playback: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Map a Deezer track object into the unified Paax track shape.

    Parameters
    ----------
    dz : dict
        Raw Deezer track JSON (from /search, /track/{id}, or album tracklist).
    playback : dict, optional
        YouTube playback block ``{videoId, matchConfidence, matchStatus}``.
    """
    artist = dz.get("artist") or {}
    album = dz.get("album") or {}

    # Build multi-artist list if 'contributors' is available
    contributors = dz.get("contributors") or []
    artists_list = []
    for c in contributors:
        name = c.get("name", "")
        aid = c.get("id")
        if name:
            artists_list.append({"name": name, "id": aid})
    # Fallback: single artist
    if not artists_list and artist.get("name"):
        artists_list.append({"name": artist["name"], "id": artist.get("id")})

    # Best cover URL: prefer xl, fallback to medium
    cover_url = (
        album.get("cover_xl")
        or album.get("cover_big")
        or album.get("cover_medium")
        or album.get("cover_small")
        or album.get("cover")
        or ""
    )

    result: Dict[str, Any] = {
        "id": str(dz.get("id", "")),
        "title": dz.get("title") or dz.get("title_short") or "Unknown",
        "artist": {
            "id": artist.get("id"),
            "name": artist.get("name", "Unknown Artist"),
        },
        "artists": artists_list,
        "album": {
            "id": album.get("id"),
            "title": album.get("title", ""),
            "coverUrl": cover_url,
        },
        "duration": dz.get("duration", 0),
        "explicit": dz.get("explicit_lyrics", False),
        "trackNumber": dz.get("track_position"),
        "discNumber": dz.get("disk_number"),
        "source": "deezer",
    }

    # Attach playback block (YouTube match)
    if playback:
        result["playback"] = playback
    else:
        result["playback"] = {
            "provider": "youtube",
            "engine": "iframe",
            "videoId": None,
            "matchConfidence": 0.0,
            "matchStatus": "pending",
        }

    return result


def map_album(dz: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Deezer album object into the Paax album shape."""
    artist = dz.get("artist") or {}

    # Build multi-artist list from contributors if present
    contributors = dz.get("contributors") or []
    artists_list = []
    for c in contributors:
        name = c.get("name", "")
        aid = c.get("id")
        if name:
            artists_list.append({"name": name, "id": aid})
    if not artists_list and artist.get("name"):
        artists_list.append({"name": artist["name"], "id": artist.get("id")})

    cover_url = (
        dz.get("cover_xl")
        or dz.get("cover_big")
        or dz.get("cover_medium")
        or dz.get("cover_small")
        or dz.get("cover")
        or ""
    )

    return {
        "id": dz.get("id"),
        "title": dz.get("title", "Unknown Album"),
        "artist": {
            "id": artist.get("id"),
            "name": artist.get("name", "Unknown Artist"),
        },
        "artists": artists_list,
        "coverUrl": cover_url,
        "releaseDate": dz.get("release_date", ""),
        "trackCount": dz.get("nb_tracks", 0),
        "type": _normalize_album_type(dz.get("record_type"), dz.get("nb_tracks")),
        "duration": dz.get("duration", 0),
        "explicit": dz.get("explicit_lyrics", False),
        "source": "deezer",
    }


def map_artist(dz: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Deezer artist object into the Paax artist shape."""
    picture = (
        dz.get("picture_xl")
        or dz.get("picture_big")
        or dz.get("picture_medium")
        or dz.get("picture_small")
        or dz.get("picture")
        or ""
    )
    return {
        "id": dz.get("id"),
        "name": dz.get("name", "Unknown Artist"),
        "picture": picture,
        "nbFans": dz.get("nb_fan", 0),
        "nbAlbums": dz.get("nb_album", 0),
        "source": "deezer",
    }


def map_album_track(
    dz_track: Dict[str, Any],
    album_info: Dict[str, Any],
    playback: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Map a track from an album tracklist (which lacks album metadata).
    Merges album-level info (cover, title, artist) into the track.
    """
    # Album tracklist tracks have minimal artist info — enrich from album
    artist = dz_track.get("artist") or album_info.get("artist") or {}
    
    # Contributors for multi-artist
    contributors = dz_track.get("contributors") or []
    artists_list = []
    for c in contributors:
        name = c.get("name", "")
        aid = c.get("id")
        if name:
            artists_list.append({"name": name, "id": aid})
    if not artists_list and artist.get("name"):
        artists_list.append({"name": artist["name"], "id": artist.get("id")})

    cover_url = album_info.get("coverUrl", "")

    result: Dict[str, Any] = {
        "id": str(dz_track.get("id", "")),
        "title": dz_track.get("title") or dz_track.get("title_short") or "Unknown",
        "artist": {
            "id": artist.get("id"),
            "name": artist.get("name", "Unknown Artist"),
        },
        "artists": artists_list,
        "album": {
            "id": album_info.get("id"),
            "title": album_info.get("title", ""),
            "coverUrl": cover_url,
        },
        "duration": dz_track.get("duration", 0),
        "explicit": dz_track.get("explicit_lyrics", False),
        "trackNumber": dz_track.get("track_position"),
        "discNumber": dz_track.get("disk_number"),
        "source": "deezer",
    }

    if playback:
        result["playback"] = playback
    else:
        result["playback"] = {
            "provider": "youtube",
            "engine": "iframe",
            "videoId": None,
            "matchConfidence": 0.0,
            "matchStatus": "pending",
        }

    return result


def _normalize_album_type(
    record_type: Optional[str], nb_tracks: Optional[int] = None
) -> str:
    """Normalize Deezer record_type to album | single | ep."""
    if record_type:
        rt = record_type.lower().strip()
        if rt in ("album",):
            return "album"
        if rt in ("single",):
            return "single"
        if rt in ("ep", "compile"):
            return "ep"
    # Fallback by track count
    if nb_tracks is not None:
        if nb_tracks == 1:
            return "single"
        if nb_tracks <= 6:
            return "ep"
    return "album"
