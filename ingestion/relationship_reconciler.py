"""
Relationship reconciliation policy.

Most reconciliation (idempotent upserts, complete-vs-partial junction pruning,
preserve-YouTube/artwork) is enforced atomically inside the graph-upsert RPCs.
This module holds the *Python-side* policy that shapes payloads before they are
sent — chiefly deriving artist genres from an album's genres, which Deezer does
not expose on the artist object directly.
"""

from __future__ import annotations

from typing import Any, Dict, List


def derive_artist_genre_enrichments(album_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Given a full album graph payload, produce per-primary-artist upsert payloads
    that attach the album's genres to the album's primary artists.

    Returned payloads use metadata_status='partial' and complete=False so they
    NEVER downgrade a fully-ingested artist and NEVER prune existing genres —
    they only add genre associations.
    """
    genres = album_payload.get("album_genres") or []
    if not genres:
        return []
    enrichments: List[Dict[str, Any]] = []
    for artist in album_payload.get("album_artists") or []:
        if artist.get("role") != "primary" or not artist.get("name"):
            continue
        enrichments.append({
            "deezer_id": artist.get("deezer_id"),
            "name": artist["name"],
            "metadata_status": "partial",
            "complete": False,
            "genres": genres,
        })
    return enrichments
