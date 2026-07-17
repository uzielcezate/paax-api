"""Deezer track JSON -> catalog_upsert_track_graph payload."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from mappers.deezer_common import best_image, build_track_artists

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_release(date_str: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """Return (iso_date_or_none, year_or_none); reject Deezer's '0000-00-00'."""
    if not date_str:
        return None, None
    m = _DATE_RE.match(date_str.strip())
    if not m:
        return None, None
    year = int(m.group(1))
    if year < 1000 or year > 2100:
        return None, None
    if m.group(2) == "00" or m.group(3) == "00":
        return None, year
    return date_str.strip(), year


def _bpm(dz: Dict[str, Any]) -> Optional[float]:
    bpm = dz.get("bpm")
    try:
        val = float(bpm)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def track_row_payload(dz: Dict[str, Any], *, metadata_status: str = "full",
                      album_cover: Optional[str] = None) -> Dict[str, Any]:
    """The track object embedded in album/track graph payloads."""
    dz_album = dz.get("album") or {}
    return {
        "deezer_id": dz.get("id"),
        "title": dz.get("title") or dz.get("title_short") or "Unknown",
        "title_short": dz.get("title_short"),
        "track_number": dz.get("track_position"),
        "disc_number": dz.get("disk_number") or 1,
        "duration_seconds": dz.get("duration"),
        "explicit": bool(dz.get("explicit_lyrics", False)),
        "isrc": dz.get("isrc"),
        "bpm": _bpm(dz),
        "image_original_url": album_cover or best_image(dz_album, "cover"),
        "metadata_status": metadata_status,
        "artists": build_track_artists(dz),
        "genres": [],
    }


def _album_summary_payload(dz_album: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not dz_album or not dz_album.get("id"):
        return None
    release_date, release_year = parse_release(dz_album.get("release_date"))
    return {
        "deezer_id": dz_album.get("id"),
        "title": dz_album.get("title") or "Unknown Album",
        "cover_original_url": best_image(dz_album, "cover"),
        "release_date": release_date,
        "release_year": release_year,
        # Standalone track ingestion knows only a summary album -> partial.
        "metadata_status": "partial",
    }


def track_graph_payload(dz: Dict[str, Any], *, metadata_status: str = "full",
                        complete: bool = True) -> Dict[str, Any]:
    """Full payload for ``catalog_upsert_track_graph``."""
    payload: Dict[str, Any] = {
        "track": track_row_payload(dz, metadata_status=metadata_status),
        "complete": complete,
    }
    album = _album_summary_payload(dz.get("album") or {})
    if album is not None:
        payload["album"] = album
    return payload
