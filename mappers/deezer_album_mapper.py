"""Deezer album JSON -> catalog_upsert_album_graph payload (complete graph)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mappers.deezer_common import album_type, best_image, build_track_artists, genre_payloads
from mappers.deezer_track_mapper import parse_release, track_row_payload


def _album_artists(
    dz_album: Dict[str, Any],
    artist_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Album header credits (album-level contributors), role-aware.

    ``artist_context`` (``{"deezer_id", "name"}``) is the authoritative parent
    artist when this album is being ingested under that artist (e.g. from
    ``/artist/{id}/albums``, where Deezer often omits the nested ``artist``).

    Attribution rules (Phase 2.6):
      1. Explicit Deezer album-artist data is used and preserved.
      2. The parent artist is linked when supplied and not already present
         (deduped by deezer_id) so the album appears in the artist's discography.
      3. When neither explicit data nor parent context exists, NO placeholder
         "Unknown Artist" is emitted — the list is empty (partial graph). A
         null-deezer_id catalog artist is never persisted from this flow.
    """
    from mappers.deezer_common import map_role

    contributors = dz_album.get("contributors") or []
    main = dz_album.get("artist") or {}
    artists: List[Dict[str, Any]] = []
    seen: set = set()

    if main.get("name"):
        artists.append({"deezer_id": main.get("id"), "name": main["name"],
                        "role": "primary", "position": 1})
        if main.get("id") is not None:
            seen.add(main.get("id"))

    pos = len(artists) + 1
    for c in contributors:
        cid = c.get("id")
        if not c.get("name") or (cid is not None and cid in seen):
            continue
        role = "primary" if (cid is not None and cid == main.get("id")) else map_role(c.get("role"), track=False)
        artists.append({"deezer_id": cid, "name": c["name"], "role": role, "position": pos})
        if cid is not None:
            seen.add(cid)
        pos += 1

    # Rule 2: attribute to the authoritative parent artist when supplied.
    if artist_context and artist_context.get("name"):
        ctx_id = artist_context.get("deezer_id")
        already_linked = ctx_id is not None and ctx_id in seen
        if not already_linked:
            # First credit (empty list) → primary#1; otherwise append as primary.
            artists.append({
                "deezer_id": ctx_id,
                "name": artist_context["name"],
                "role": "primary",
                "position": len(artists) + 1,
            })

    # Rule 3: never emit a persistent "Unknown Artist" placeholder.
    return artists


def album_graph_payload(
    dz_album: Dict[str, Any],
    track_objs: Optional[Dict[int, Dict[str, Any]]] = None,
    *,
    complete: bool = True,
    metadata_status: str = "full",
    artist_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the full album graph. ``track_objs`` maps a Deezer track id to its full
    ``/track/{id}`` object (with contributors); when present the richer object is
    used so per-track collaborators are preserved. Otherwise the album tracklist
    entry (main artist only) is used.

    ``artist_context`` (``{"deezer_id", "name"}``) supplies the authoritative
    parent artist when the album is ingested under an artist's discography and
    Deezer omitted the nested ``artist`` (Phase 2.6).
    """
    track_objs = track_objs or {}
    album_cover = best_image(dz_album, "cover")
    release_date, release_year = parse_release(dz_album.get("release_date"))
    album_artists = _album_artists(dz_album, artist_context)

    # If no artist could be attributed at all, keep the graph partial rather than
    # asserting a fully-ingested album with no credits.
    effective_status = metadata_status if album_artists else "partial"

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
        "metadata_status": effective_status,
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
        "album_artists": album_artists,
        "album_genres": genre_payloads(dz_album),
        "tracks": tracks,
    }
