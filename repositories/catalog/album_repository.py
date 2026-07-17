"""Album repository — Supabase reads with full, ordered track graphs."""

from __future__ import annotations

from typing import Any, Dict, Optional

from mappers import row_mappers as rm
from schemas.catalog.album import AlbumGraph

from repositories.catalog.base import CatalogRepositoryBase


class AlbumRepository(CatalogRepositoryBase):
    table = "albums"

    async def get_album_by_id(self, album_id: str) -> Optional[AlbumGraph]:
        return await self._graph(await self._row_by_id(album_id))

    async def get_album_by_deezer_id(self, deezer_id: int) -> Optional[AlbumGraph]:
        return await self._graph(await self._row_by_deezer_id(deezer_id))

    # get_album_with_tracks is the canonical full read.
    get_album_with_tracks = get_album_by_id

    async def upsert_album_graph(self, payload: Dict[str, Any]) -> Optional[str]:
        """Atomic album-graph upsert (artists, album, tracks, credits, genres)."""
        return await self._upsert_graph("catalog_upsert_album_graph", payload)

    async def _graph(self, album_row: Optional[Dict[str, Any]]) -> Optional[AlbumGraph]:
        if not album_row:
            return None
        album_id = album_row["id"]

        # Ordered tracklist (disc, then track number) — one query.
        track_rows = await self.g.fetch_all(
            "tracks",
            [("album_id", "eq", album_id)],
            order_by=("disc_number", False),
        )
        # Secondary sort by track_number in Python (PostgREST single order key here).
        track_rows.sort(key=lambda r: (r.get("disc_number") or 1, r.get("track_number") or 0))

        tracks = await self.loader.build_track_graphs(track_rows)
        artists = await self.loader.load_album_artists(album_id)
        genres = await self.loader.load_album_genres(album_id)
        return rm.album_graph(album_row, artists=artists, tracks=tracks, genres=genres)
