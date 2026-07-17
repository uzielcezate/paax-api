"""Track repository — Supabase reads + YouTube-match writes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from schemas.catalog.track import TrackGraph

from repositories.catalog.base import CatalogRepositoryBase

# Columns the matcher is allowed to write (whitelist — never trust arbitrary keys).
_YOUTUBE_WRITE_COLUMNS = {
    "youtube_audio_video_id",
    "youtube_music_video_id",
    "preferred_youtube_video_id",
    "youtube_audio_match_type",
    "youtube_music_video_match_type",
    "youtube_match_status",
    "youtube_match_confidence",
    "youtube_match_reason",
    "youtube_match_updated_at",
    "youtube_last_verified_at",
    "youtube_failure_count",
}


class TrackRepository(CatalogRepositoryBase):
    table = "tracks"

    async def _graph_from_row(self, row: Optional[Dict[str, Any]]) -> Optional[TrackGraph]:
        if not row:
            return None
        graphs = await self.loader.build_track_graphs([row])
        return graphs[0] if graphs else None

    async def get_track_by_id(self, track_id: str) -> Optional[TrackGraph]:
        return await self._graph_from_row(await self._row_by_id(track_id))

    async def get_track_by_deezer_id(self, deezer_id: int) -> Optional[TrackGraph]:
        return await self._graph_from_row(await self._row_by_deezer_id(deezer_id))

    async def get_track_by_isrc(self, isrc: str) -> Optional[TrackGraph]:
        """First track matching an ISRC (ISRC is indexed but not unique)."""
        rows = await self.g.fetch_all("tracks", [("isrc", "eq", isrc)], limit=1)
        return await self._graph_from_row(rows[0] if rows else None)

    async def get_row_by_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Raw row (used by the matcher, which needs canonical metadata + state)."""
        return await self._row_by_id(track_id)

    async def update_youtube_match(self, track_id: str, values: Dict[str, Any]) -> None:
        """
        Persist a YouTube match. Only whitelisted youtube_* columns are written,
        so canonical Deezer metadata can never be overwritten here.
        """
        payload = {k: v for k, v in values.items() if k in _YOUTUBE_WRITE_COLUMNS}
        if not payload:
            return
        await self.g.update("tracks", payload, [("id", "eq", track_id)])
