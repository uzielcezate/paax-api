"""Artist repository — Supabase reads with discography assembly."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mappers import row_mappers as rm
from schemas.catalog.artist import ArtistGraph, ReleaseSummary

from repositories.catalog.base import CatalogRepositoryBase


def _release_sort_key(r: ReleaseSummary):
    # Newest first; releases without a date sort last.
    return (r.release_date or "", )


class ArtistRepository(CatalogRepositoryBase):
    table = "artists"

    async def get_artist_by_id(self, artist_id: str) -> Optional[ArtistGraph]:
        return await self._graph(await self._row_by_id(artist_id))

    async def get_artist_by_deezer_id(self, deezer_id: int) -> Optional[ArtistGraph]:
        return await self._graph(await self._row_by_deezer_id(deezer_id))

    get_artist_with_releases = get_artist_by_id

    async def upsert_artist_graph(self, payload: Dict[str, Any]) -> Optional[str]:
        """Atomic artist-graph upsert (artist + genres)."""
        return await self._upsert_graph("catalog_upsert_artist_graph", payload)

    async def load_releases(self, artist_id: str) -> List[ReleaseSummary]:
        """All albums crediting this artist, newest release first."""
        aa_rows = await self.g.fetch_all("album_artists", [("artist_id", "eq", artist_id)])
        album_ids = list({r["album_id"] for r in aa_rows})
        if not album_ids:
            return []
        album_rows = await self.g.fetch_all("albums", [("id", "in", album_ids)])
        releases = [rm.release_summary(a) for a in album_rows]
        releases.sort(key=_release_sort_key, reverse=True)
        return releases

    async def _graph(self, artist_row: Optional[Dict[str, Any]]) -> Optional[ArtistGraph]:
        if not artist_row:
            return None
        artist_id = artist_row["id"]
        genres = await self.loader.load_artist_genres(artist_id)
        releases = await self.load_releases(artist_id)
        # top_tracks are populated by the service layer (Deezer top + matching);
        # the persistent read layer returns the stored discography only.
        return rm.artist_graph(artist_row, genres=genres, releases=releases, top_tracks=[])
