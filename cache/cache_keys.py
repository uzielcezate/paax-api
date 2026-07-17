"""
Centralized Redis key registry. No raw key strings anywhere else in the code.

Namespaces (see docs/CACHE_STRATEGY.md):
  catalog:*      response cache for artist/album/track/discography
  search:*       search response cache
  home:*         home/chart response cache
  youtube:*      per-track match cache
  negative:*     short-lived not-found / temporarily-unavailable cache
  lock:*         distributed locks (ingest / refresh)
"""

from __future__ import annotations

import re


def _norm(value: str) -> str:
    v = (value or "").strip().lower()
    return re.sub(r"\s+", "_", v)


class CacheKeys:
    # -- catalog response cache ---------------------------------------------
    @staticmethod
    def artist(internal_id: str) -> str:
        return f"catalog:artist:{internal_id}"

    @staticmethod
    def artist_by_deezer(deezer_id: int) -> str:
        return f"catalog:artist:deezer:{deezer_id}"

    @staticmethod
    def artist_discography(internal_id: str) -> str:
        return f"catalog:artist:{internal_id}:discography"

    @staticmethod
    def artist_top(internal_id: str) -> str:
        return f"catalog:artist:{internal_id}:top"

    @staticmethod
    def album(internal_id: str) -> str:
        return f"catalog:album:{internal_id}"

    @staticmethod
    def album_by_deezer(deezer_id: int) -> str:
        return f"catalog:album:deezer:{deezer_id}"

    @staticmethod
    def track(internal_id: str) -> str:
        return f"catalog:track:{internal_id}"

    @staticmethod
    def track_by_deezer(deezer_id: int) -> str:
        return f"catalog:track:deezer:{deezer_id}"

    # -- search / home ------------------------------------------------------
    @staticmethod
    def search(entity_type: str, query: str, limit: int, offset: int) -> str:
        return f"search:{_norm(entity_type)}:{_norm(query)}:{limit}:{offset}"

    @staticmethod
    def home_chart(country: str, limit: int) -> str:
        return f"home:chart:{_norm(country)}:{limit}"

    # -- youtube match ------------------------------------------------------
    @staticmethod
    def youtube_match(track_id: str) -> str:
        return f"youtube:match:{track_id}"

    # -- negative cache -----------------------------------------------------
    @staticmethod
    def negative(entity_type: str, external_id) -> str:
        return f"negative:deezer:{_norm(str(entity_type))}:{external_id}"

    # -- locks --------------------------------------------------------------
    @staticmethod
    def lock_ingest(entity_type: str, external_id) -> str:
        return f"lock:ingest:{_norm(str(entity_type))}:{external_id}"

    @staticmethod
    def lock_refresh(entity_type: str, internal_id: str) -> str:
        return f"lock:refresh:{_norm(str(entity_type))}:{internal_id}"
