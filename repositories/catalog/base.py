"""Shared base for catalog repositories."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from schemas.catalog.common import DbMetadataStatus

from repositories.catalog._loader import CatalogGraphLoader
from repositories.supabase_client import SupabaseGateway

logger = logging.getLogger("paax.repo")


class CatalogRepositoryBase:
    """Common lookup + metadata-status helpers for artist/album/track repos."""

    #: primary table name in public schema (subclass overrides)
    table: str = ""

    def __init__(self, gateway: SupabaseGateway):
        self.g = gateway
        self.loader = CatalogGraphLoader(gateway)

    async def _row_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return await self.g.fetch_one(self.table, [("id", "eq", entity_id)])

    async def _row_by_deezer_id(self, deezer_id: int) -> Optional[Dict[str, Any]]:
        return await self.g.fetch_one(self.table, [("deezer_id", "eq", deezer_id)])

    async def exists_by_deezer_id(self, deezer_id: int) -> bool:
        row = await self.g.fetch_one(self.table, [("deezer_id", "eq", deezer_id)], columns="id")
        return row is not None

    # -- metadata state transitions -----------------------------------------

    async def mark_metadata_failed(self, entity_id: str) -> None:
        """Record a controlled failure without discarding stored data."""
        await self.g.update(self.table, {"metadata_status": DbMetadataStatus.FAILED},
                            [("id", "eq", entity_id)])

    async def mark_metadata_stale(self, entity_id: str) -> None:
        await self.g.update(self.table, {"metadata_status": DbMetadataStatus.STALE},
                            [("id", "eq", entity_id)])

    async def mark_metadata_refreshing(self, entity_id: str) -> None:
        """
        The DB metadata_status vocabulary has no 'refreshing' value, so this is
        tracked in Redis (distributed lock / response-cache status) rather than
        persisted. Kept as an explicit no-op for interface completeness and so
        callers read clearly. See docs/CACHE_STRATEGY.md.
        """
        logger.debug("[repo] mark_metadata_refreshing(%s.%s) — tracked in Redis, not DB",
                     self.table, entity_id)
