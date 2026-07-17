"""Deezer artist JSON -> catalog_upsert_artist_graph payload."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mappers.deezer_common import best_image


def artist_payload(
    dz: Dict[str, Any],
    *,
    metadata_status: str = "full",
    complete: bool = False,
    genres: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Bare-artist payload accepted by ``catalog_upsert_artist_graph``."""
    payload: Dict[str, Any] = {
        "deezer_id": dz.get("id"),
        "name": dz.get("name") or "Unknown Artist",
        "image_original_url": best_image(dz, "picture"),
        "deezer_fans_count": dz.get("nb_fan"),
        "metadata_status": metadata_status,
        "complete": complete,
    }
    if genres:
        payload["genres"] = genres
    return payload
