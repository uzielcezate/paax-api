"""Genre repository."""

from __future__ import annotations

from typing import List, Optional

from mappers import row_mappers as rm
from schemas.catalog.track import GenreRefLite

from repositories.catalog.base import CatalogRepositoryBase


class GenreRepository(CatalogRepositoryBase):
    table = "genres"

    async def get_genre_by_id(self, genre_id: str) -> Optional[GenreRefLite]:
        row = await self._row_by_id(genre_id)
        return rm.genre_ref(row) if row else None

    async def get_genre_by_deezer_id(self, deezer_id: int) -> Optional[GenreRefLite]:
        row = await self._row_by_deezer_id(deezer_id)
        return rm.genre_ref(row) if row else None

    async def get_genre_by_slug(self, slug: str) -> Optional[GenreRefLite]:
        row = await self.g.fetch_one("genres", [("slug", "eq", slug)])
        return rm.genre_ref(row) if row else None

    async def list_genres(self, limit: int = 100) -> List[GenreRefLite]:
        rows = await self.g.fetch_all("genres", order_by=("name", False), limit=limit)
        return [rm.genre_ref(r) for r in rows]
