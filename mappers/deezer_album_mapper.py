"""Deezer album JSON -> catalog_upsert_album_graph payload (complete graph)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mappers.deezer_common import album_type, best_image, build_track_artists, genre_payloads
from mappers.deezer_track_mapper import parse_release, track_row_payload


def _album_artists(dz_album: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Album header credits (album-level contributors), role-aware."""
    contributors = dz_album.get("contributors") or []
    main = dz_album.get("artist") or {}
    artists: List[Dict[str, Any]] = []
    seen = set()
    if main.get("name"):
        artists.append({"deezer_id": main.get("id"), "name": main["name"],
                        "role": "primary", "position": 1})
        if main.get("id") is not None:
            seen.add(main.get("id"))
    pos = len(artists) + 1
    from mappers.deezer_common import map_role
    for c in contributors:
        cid = c.get("id")
        if not c.get("name") or (cid is not None and cid in seen):
            continue
        role = "primary" if (cid is not None and cid == main.get("id")) else map_role(c.get("role"), track=False)
        artists.append({"deezer_id": cid, "name": c["name"], "role": role, "position": pos})
        if cid is not None:
            seen.add(cid)
        pos += 1
    if not artists:
        artists.append({"deezer_id": None, "name": "Unknown Artist", "role": "primary", "position": 1})
    return artists


def album_graph_payload(
    dz_album: Dict[str, Any],
    track_objs: Optional[Dict[int, Dict[str, Any]]] = None,
    *,
    complete: bool = True,
    metadata_status: str = "full",
) -> Dict[str, Any]:
    """
    Build the full album graph. ``track_objs`` maps a Deezer track id to its full
    ``/track/{id}`` object (with contributors); when present the richer object is
    used so per-track collaborators are preserved. Otherwise the album tracklist
    entry (main artist only) is used.
    """
    track_objs = track_objs or {}
    album_cover = best_image(dz_album, "cover")
    release_date, release_year = parse_release(dz_album.get("release_date"))

    album = {
        "deezer_id": dz_album.get("id"),
        "title": dz_album.get("title") or "Unknown Album",
        "album_type": album_type(dz_album.get("record_type"), dz_album.get("nb_tracks")),
        "release_date": release_date,
        "release_year": release_year,
        "label": dz_album.get("label"),
        "upc": dz_album.get("upc"),
        "cover_original_url": album_cover,
        "total_tracks": dz_album.get("nb_tracks"),
        "total_duration_seconds": dz_album.get("duration"),
        "explicit": bool(dz_album.get("explicit_lyrics", False)),
        "metadata_status": metadata_status,
    }

    tracklist = ((dz_album.get("tracks") or {}).get("data")) or []
    tracks: List[Dict[str, Any]] = []
    for t in tracklist:
        source = track_objs.get(t.get("id"), t)
        tracks.append(track_row_payload(source, metadata_status=metadata_status,
                                        album_cover=album_cover))

    return {
        "complete": complete,
        "album": album,
        "album_artists": _album_artists(dz_album),
        "album_genres": genre_payloads(dz_album),
        "tracks": tracks,
    }
