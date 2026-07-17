"""Shared helpers for Deezer -> upsert-payload mapping."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Deezer contributor.role -> catalog role vocabulary.
_ROLE_MAP = {
    "main": "primary",
    "featured": "featured",
    "featuring": "featured",
    "producer": "producer",
    "composer": "composer",
    "writer": "composer",       # DB track role has no 'writer'; nearest is 'composer'
    "remixer": "remixer",
    "performer": "performer",
    "vocalist": "vocalist",
}


def slugify(name: str) -> str:
    """Produce a slug matching ^[a-z0-9]+(-[a-z0-9]+)*$ (or '' if impossible)."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower())
    return s.strip("-")


def best_image(obj: Dict[str, Any], prefix: str) -> Optional[str]:
    """Best available Deezer image URL for a prefix like 'cover' or 'picture'."""
    for suffix in ("_xl", "_big", "_medium", "_small", ""):
        url = obj.get(f"{prefix}{suffix}")
        if url:
            return url
    return None


def map_role(deezer_role: Optional[str], *, track: bool = True) -> str:
    role = _ROLE_MAP.get((deezer_role or "").strip().lower())
    if role is None:
        return "other"
    if not track and role == "vocalist":  # album_artists has no 'vocalist'
        return "performer"
    return role


def album_type(record_type: Optional[str], nb_tracks: Optional[int]) -> str:
    rt = (record_type or "").strip().lower()
    mapping = {
        "album": "album", "single": "single", "ep": "ep",
        "compile": "compilation", "compilation": "compilation",
        "live": "live", "soundtrack": "soundtrack",
    }
    if rt in mapping:
        return mapping[rt]
    if nb_tracks is not None:
        if nb_tracks <= 1:
            return "single"
        if nb_tracks <= 6:
            return "ep"
    return "album"


def genre_payloads(dz_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract genre payloads from a Deezer object's genres.data (albums)."""
    genres = ((dz_obj.get("genres") or {}).get("data")) or []
    out: List[Dict[str, Any]] = []
    seen = set()
    for g in genres:
        name = g.get("name")
        gid = g.get("id")
        if not name or gid in seen:
            continue
        seen.add(gid)
        out.append({"deezer_id": gid, "name": name, "slug": slugify(name), "source": "deezer"})
    return out


def build_track_artists(dz_track: Dict[str, Any], *, track: bool = True) -> List[Dict[str, Any]]:
    """
    A track/album's OWN credited artists, ordered, with roles. The main artist
    (``artist``) is position 1 / primary; ``contributors`` add the rest. Never
    derived from an album header — the caller passes the track's own object.
    """
    main = dz_track.get("artist") or {}
    contributors = dz_track.get("contributors") or []
    artists: List[Dict[str, Any]] = []
    seen = set()

    main_id = main.get("id")
    if main.get("name"):
        artists.append({
            "deezer_id": main_id, "name": main["name"], "role": "primary", "position": 1,
        })
        if main_id is not None:
            seen.add(main_id)

    pos = len(artists) + 1
    for c in contributors:
        cid = c.get("id")
        name = c.get("name")
        if not name:
            continue
        if cid is not None and cid in seen:
            continue
        role = "primary" if (cid is not None and cid == main_id) else map_role(c.get("role"), track=track)
        artists.append({"deezer_id": cid, "name": name, "role": role, "position": pos})
        if cid is not None:
            seen.add(cid)
        pos += 1

    if not artists:
        artists.append({"deezer_id": None, "name": "Unknown Artist", "role": "primary", "position": 1})
    return artists
