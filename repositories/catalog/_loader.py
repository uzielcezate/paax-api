"""
CatalogGraphLoader — assembles full entity graphs from Supabase using a small,
fixed number of batched queries (never N+1 in the number of tracks/artists).

Shared by the track/album/artist repositories so the assembly logic (and its
query budget) lives in one place.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from mappers import row_mappers as rm
from repositories.supabase_client import SupabaseGateway
from schemas.catalog.track import GenreRefLite, TrackArtistLink, TrackGraph


def _index(rows: Iterable[Dict[str, Any]], key: str = "id") -> Dict[Any, Dict[str, Any]]:
    return {r[key]: r for r in rows}


class CatalogGraphLoader:
    def __init__(self, gateway: SupabaseGateway):
        self._g = gateway

    # -- generic helpers -----------------------------------------------------

    async def _fetch_in(self, table: str, column: str, values: Iterable[Any], columns="*"):
        vals = list({v for v in values if v is not None})
        if not vals:
            return []
        return await self._g.fetch_all(table, [(column, "in", vals)], columns=columns)

    # -- tracks --------------------------------------------------------------

    async def build_track_graphs(self, track_rows: List[Dict[str, Any]]) -> List[TrackGraph]:
        """Assemble a list of track rows into full TrackGraphs (4 queries max)."""
        if not track_rows:
            return []

        track_ids = [r["id"] for r in track_rows]
        album_ids = [r.get("album_id") for r in track_rows]

        ta_rows = await self._fetch_in("track_artists", "track_id", track_ids)
        tg_rows = await self._fetch_in("track_genres", "track_id", track_ids)

        artist_ids = {r["artist_id"] for r in ta_rows}
        genre_ids = {r["genre_id"] for r in tg_rows}

        artists_by_id = _index(await self._fetch_in("artists", "id", artist_ids))
        genres_by_id = _index(await self._fetch_in("genres", "id", genre_ids))
        albums_by_id = _index(await self._fetch_in("albums", "id", album_ids))

        ta_by_track: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for r in ta_rows:
            ta_by_track[r["track_id"]].append(r)
        tg_by_track: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for r in tg_rows:
            tg_by_track[r["track_id"]].append(r)

        graphs: List[TrackGraph] = []
        for tr in track_rows:
            links: List[TrackArtistLink] = []
            for ta in ta_by_track.get(tr["id"], []):
                artist_row = artists_by_id.get(ta["artist_id"])
                if artist_row:
                    links.append(rm.track_artist_link(ta, artist_row))

            genres: List[GenreRefLite] = []
            for tg in tg_by_track.get(tr["id"], []):
                grow = genres_by_id.get(tg["genre_id"])
                if grow:
                    genres.append(rm.genre_ref(grow))

            album_row = albums_by_id.get(tr.get("album_id"))
            album = rm.album_summary(album_row) if album_row else None
            graphs.append(rm.track_graph(tr, album=album, artists=links, genres=genres))
        return graphs

    # -- album genres / artists ---------------------------------------------

    async def load_album_genres(self, album_id: str) -> List[GenreRefLite]:
        ag_rows = await self._g.fetch_all("album_genres", [("album_id", "eq", album_id)])
        genre_ids = {r["genre_id"] for r in ag_rows}
        genres_by_id = _index(await self._fetch_in("genres", "id", genre_ids))
        return [rm.genre_ref(genres_by_id[gid]) for gid in genre_ids if gid in genres_by_id]

    async def load_album_artists(self, album_id: str):
        aa_rows = await self._g.fetch_all("album_artists", [("album_id", "eq", album_id)])
        artist_ids = {r["artist_id"] for r in aa_rows}
        artists_by_id = _index(await self._fetch_in("artists", "id", artist_ids))
        links = []
        for aa in aa_rows:
            arow = artists_by_id.get(aa["artist_id"])
            if arow:
                links.append(rm.album_artist_link(aa, arow))
        return links

    async def load_artist_genres(self, artist_id: str) -> List[GenreRefLite]:
        ag_rows = await self._g.fetch_all("artist_genres", [("artist_id", "eq", artist_id)])
        genre_ids = {r["genre_id"] for r in ag_rows}
        genres_by_id = _index(await self._fetch_in("genres", "id", genre_ids))
        return [rm.genre_ref(genres_by_id[gid]) for gid in genre_ids if gid in genres_by_id]
