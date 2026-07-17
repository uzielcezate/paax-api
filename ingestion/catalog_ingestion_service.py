"""
CatalogIngestionService — converts Deezer responses into the Phase 1 schema via
the atomic graph-upsert RPCs.

Guarantees:
  * Complete album ingestion persists the whole graph (artists, album,
    album_artists, album_genres, tracks, track_artists, track_genres, genres).
  * Per-track collaborators are fetched from /track/{id} (bounded concurrency),
    never derived from the album header.
  * Existing YouTube matches and cached artwork are preserved by the RPCs.
  * Upstream failures raise normalized DeezerError (never a raw 500).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from mappers.deezer_album_mapper import album_graph_payload
from mappers.deezer_artist_mapper import artist_payload
from mappers.deezer_track_mapper import track_graph_payload
from services.deezer.errors import DeezerError

from ingestion.relationship_reconciler import derive_artist_genre_enrichments

logger = logging.getLogger("paax.ingest")


class CatalogIngestionService:
    def __init__(self, deezer, artist_repo, album_repo, track_repo,
                 *, fetch_track_details: bool = True):
        self.deezer = deezer
        self.artists = artist_repo
        self.albums = album_repo
        self.tracks = track_repo
        self.fetch_track_details = fetch_track_details

    # ── Artist ──────────────────────────────────────────────────────────────

    async def ingest_artist(self, deezer_artist_id: int) -> Optional[str]:
        dz = await self.deezer.get_artist(deezer_artist_id)
        payload = artist_payload(dz, metadata_status="full", complete=False)
        logger.info("[ingest] artist deezer_id=%s '%s'", deezer_artist_id, dz.get("name"))
        return await self.artists.upsert_artist_graph({"artist": payload})

    # ── Album (complete graph) ──────────────────────────────────────────────

    async def ingest_album(self, deezer_album_id: int, *, complete: bool = True) -> Optional[str]:
        dz_album = await self.deezer.get_album(deezer_album_id)
        tracklist = ((dz_album.get("tracks") or {}).get("data")) or []

        track_objs: Dict[int, Dict[str, Any]] = {}
        if self.fetch_track_details and tracklist:
            track_ids = [t["id"] for t in tracklist if t.get("id")]
            results = await asyncio.gather(*[self._safe_track(tid) for tid in track_ids])
            track_objs = {tid: obj for tid, obj in results if obj is not None}

        payload = album_graph_payload(dz_album, track_objs, complete=complete)
        album_uuid = await self.albums.upsert_album_graph(payload)
        logger.info("[ingest] album deezer_id=%s '%s' tracks=%d details=%d -> %s",
                    deezer_album_id, dz_album.get("title"), len(tracklist),
                    len(track_objs), album_uuid)

        # Enrich primary artists with the album's genres (additive, non-pruning).
        for enrichment in derive_artist_genre_enrichments(payload):
            try:
                await self.artists.upsert_artist_graph({"artist": enrichment})
            except DeezerError:
                raise
            except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                logger.warning("[ingest] artist-genre enrichment failed: %s", exc)
        return album_uuid

    async def _safe_track(self, track_id: int) -> Tuple[int, Optional[Dict[str, Any]]]:
        try:
            return track_id, await self.deezer.get_track(track_id)
        except DeezerError as exc:
            logger.warning("[ingest] track detail %s failed: %s", track_id, exc)
            return track_id, None

    # ── Track (standalone) ──────────────────────────────────────────────────

    async def ingest_track(self, deezer_track_id: int) -> Optional[str]:
        dz = await self.deezer.get_track(deezer_track_id)
        payload = track_graph_payload(dz, metadata_status="full", complete=True)
        logger.info("[ingest] track deezer_id=%s '%s'", deezer_track_id, dz.get("title"))
        return await self.tracks.upsert_track_graph(payload)

    # ── Search / chart result ingestion (minimal, partial) ──────────────────

    async def ingest_search_track(self, dz_track: Dict[str, Any]) -> Optional[str]:
        payload = track_graph_payload(dz_track, metadata_status="partial", complete=False)
        return await self.tracks.upsert_track_graph(payload)

    async def ingest_search_album(self, dz_album: Dict[str, Any]) -> Optional[str]:
        payload = album_graph_payload(dz_album, None, complete=False, metadata_status="partial")
        return await self.albums.upsert_album_graph(payload)

    async def ingest_search_artist(self, dz_artist: Dict[str, Any]) -> Optional[str]:
        payload = artist_payload(dz_artist, metadata_status="partial", complete=False)
        return await self.artists.upsert_artist_graph({"artist": payload})

    async def ingest_search_results(self, entity_type: str,
                                    dz_items: List[Dict[str, Any]]) -> List[str]:
        """Bulk-ingest partial entities returned by a Deezer search."""
        fn = {
            "tracks": self.ingest_search_track,
            "albums": self.ingest_search_album,
            "artists": self.ingest_search_artist,
        }.get(entity_type)
        if fn is None:
            return []
        results = await asyncio.gather(*[_safe(fn, item) for item in dz_items])
        return [r for r in results if r]


async def _safe(fn, item):
    try:
        return await fn(item)
    except Exception as exc:  # noqa: BLE001 — one bad search item shouldn't fail the batch
        logger.warning("[ingest] search-item ingest failed: %s", exc)
        return None
