"""
Search repository — trigram-ranked catalog search via the catalog_search RPC.

Returns raw ranked rows (id/deezer_id/core fields/score). The search *service*
(Phase 2.3) decides whether local results are sufficient or Deezer discovery is
needed, then loads full graphs for the winners.
"""

from __future__ import annotations

from typing import Any, Dict, List

from repositories.supabase_client import SupabaseGateway


class SearchRepository:
    def __init__(self, gateway: SupabaseGateway):
        self.g = gateway

    async def _search(self, entity_type: str, query: str, limit: int, offset: int) -> List[Dict[str, Any]]:
        data = await self.g.rpc(
            "catalog_search",
            {"p_type": entity_type, "p_query": query, "p_limit": limit, "p_offset": offset},
        )
        # supabase-py returns the jsonb payload directly (a list here).
        return list(data or [])

    async def search_artists(self, query: str, limit: int = 20, offset: int = 0):
        return await self._search("artists", query, limit, offset)

    async def search_albums(self, query: str, limit: int = 20, offset: int = 0):
        return await self._search("albums", query, limit, offset)

    async def search_tracks(self, query: str, limit: int = 20, offset: int = 0):
        return await self._search("tracks", query, limit, offset)
