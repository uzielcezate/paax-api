"""
Shared test fixtures: an in-memory FakeGateway implementing SupabaseGateway, and
a small catalog fixture (a multi-artist track, an album with distinct per-track
credits, and an artist with several dated releases).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

Filter = Tuple[str, str, Any]


class FakeGateway:
    """In-memory SupabaseGateway. Tables are lists of row dicts."""

    def __init__(self, tables: Dict[str, List[Dict[str, Any]]], rpc_returns=None):
        self._tables = copy.deepcopy(tables)
        self._rpc_returns = rpc_returns or {}
        self.calls: List[Tuple[str, Any]] = []

    def _match(self, row: Dict[str, Any], filters: Sequence[Filter]) -> bool:
        for col, op, val in filters:
            cell = row.get(col)
            if op == "eq":
                if cell != val:
                    return False
            elif op == "in":
                if cell not in list(val):
                    return False
            elif op == "is":
                if cell is not val:
                    return False
            else:
                raise AssertionError(f"FakeGateway: unsupported op {op}")
        return True

    async def fetch_all(self, table, filters=(), columns="*", order_by=None, limit=None):
        self.calls.append((table, list(filters)))
        rows = [copy.deepcopy(r) for r in self._tables.get(table, []) if self._match(r, filters)]
        if order_by is not None:
            col, desc = order_by
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def fetch_one(self, table, filters, columns="*"):
        rows = await self.fetch_all(table, filters, columns=columns, limit=1)
        return rows[0] if rows else None

    async def rpc(self, fn, params):
        self.calls.append((f"rpc:{fn}", params))
        val = self._rpc_returns.get(fn)
        return val(params) if callable(val) else val

    async def update(self, table, values, filters):
        updated = []
        for row in self._tables.get(table, []):
            if self._match(row, filters):
                row.update(values)
                updated.append(copy.deepcopy(row))
        return updated


@pytest.fixture
def catalog_tables() -> Dict[str, List[Dict[str, Any]]]:
    artists = [
        {"id": "art-miko", "deezer_id": 111, "name": "Young Miko",
         "normalized_name": "young miko", "image_original_url": "http://img/miko.jpg",
         "image_cached_url": None, "deezer_fans_count": 500000,
         "platform_followers_count": 12, "is_verified": True,
         "metadata_status": "full", "metadata_updated_at": "2026-07-01T00:00:00Z"},
        {"id": "art-feid", "deezer_id": 222, "name": "Feid",
         "normalized_name": "feid", "image_original_url": "http://img/feid.jpg",
         "deezer_fans_count": 900000, "platform_followers_count": 3,
         "metadata_status": "full"},
    ]
    genres = [
        {"id": "gen-latin", "deezer_id": 197, "name": "Latin", "slug": "latin"},
        {"id": "gen-reggaeton", "deezer_id": 165, "name": "Reggaeton", "slug": "reggaeton"},
    ]
    albums = [
        {"id": "alb-att", "deezer_id": 333, "title": "att.", "normalized_title": "att.",
         "album_type": "album", "release_date": "2024-04-05", "release_year": 2024,
         "label": "Sony", "cover_original_url": "http://img/att.jpg",
         "cover_cached_url": None, "total_tracks": 2, "total_duration_seconds": 360,
         "explicit": True, "metadata_status": "full"},
        {"id": "alb-old", "deezer_id": 300, "title": "trap kitty", "normalized_title": "trap kitty",
         "album_type": "album", "release_date": "2022-01-01", "release_year": 2022,
         "cover_original_url": "http://img/tk.jpg", "total_tracks": 1, "explicit": True,
         "metadata_status": "full"},
        {"id": "alb-single", "deezer_id": 350, "title": "classy 101", "normalized_title": "classy 101",
         "album_type": "single", "release_date": "2023-03-01", "cover_original_url": "http://img/c.jpg",
         "total_tracks": 1, "explicit": False, "metadata_status": "full"},
    ]
    tracks = [
        {"id": "trk-offline", "deezer_id": 123, "album_id": "alb-att", "title": "offline",
         "title_short": "offline", "normalized_title": "offline", "track_number": 6,
         "disc_number": 1, "duration_seconds": 180, "explicit": True, "isrc": "USABC2400001",
         "youtube_audio_video_id": "aud123", "youtube_music_video_id": "mv123",
         "preferred_youtube_video_id": "aud123", "youtube_audio_match_type": "audio_only",
         "youtube_music_video_match_type": "official_music_video",
         "youtube_match_status": "matched", "youtube_match_confidence": 0.96,
         "youtube_failure_count": 0, "image_original_url": None,
         "metadata_status": "full", "platform_play_count": 1000},
        {"id": "trk-solo", "deezer_id": 124, "album_id": "alb-att", "title": "solo track",
         "title_short": "solo track", "normalized_title": "solo track", "track_number": 1,
         "disc_number": 1, "duration_seconds": 180, "explicit": False,
         "youtube_match_status": "pending", "youtube_failure_count": 0,
         "metadata_status": "full", "platform_play_count": 10},
    ]
    # offline = Young Miko (primary) + Feid (featured); solo = Young Miko only.
    track_artists = [
        {"track_id": "trk-offline", "artist_id": "art-miko", "role": "primary",
         "position": 1, "display_name": None},
        {"track_id": "trk-offline", "artist_id": "art-feid", "role": "featured",
         "position": 2, "display_name": None},
        {"track_id": "trk-solo", "artist_id": "art-miko", "role": "primary",
         "position": 1, "display_name": None},
    ]
    album_artists = [
        {"album_id": "alb-att", "artist_id": "art-miko", "role": "primary", "position": 1},
        {"album_id": "alb-old", "artist_id": "art-miko", "role": "primary", "position": 1},
        {"album_id": "alb-single", "artist_id": "art-feid", "role": "primary", "position": 1},
        {"album_id": "alb-single", "artist_id": "art-miko", "role": "featured", "position": 2},
    ]
    track_genres = [
        {"track_id": "trk-offline", "genre_id": "gen-latin", "relevance_score": None},
    ]
    album_genres = [
        {"album_id": "alb-att", "genre_id": "gen-latin", "relevance_score": None},
        {"album_id": "alb-att", "genre_id": "gen-reggaeton", "relevance_score": None},
    ]
    artist_genres = [
        {"artist_id": "art-miko", "genre_id": "gen-latin", "relevance_score": None},
    ]
    return {
        "artists": artists, "genres": genres, "albums": albums, "tracks": tracks,
        "track_artists": track_artists, "album_artists": album_artists,
        "track_genres": track_genres, "album_genres": album_genres,
        "artist_genres": artist_genres,
    }


@pytest.fixture
def gateway(catalog_tables):
    return FakeGateway(catalog_tables)
