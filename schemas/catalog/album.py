"""Album schemas: DB row, entity graph, and normalized API response."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from .common import AlbumType, CamelModel, DbMetadataStatus, MetadataStatus
from .genre import GenreResponse
from .track import ArtistRefResponse, TrackGraph, TrackResponse


class AlbumArtistLink(BaseModel):
    artist_id: str
    deezer_id: Optional[int] = None
    name: str
    role: str
    position: int


class AlbumGraph(BaseModel):
    """A fully-loaded album with its artists, ordered tracklist and genres."""

    id: str
    deezer_id: Optional[int] = None
    title: str
    album_type: str = AlbumType.ALBUM
    release_date: Optional[str] = None
    release_year: Optional[int] = None
    label: Optional[str] = None
    upc: Optional[str] = None
    cover_original_url: Optional[str] = None
    cover_cached_url: Optional[str] = None
    total_tracks: Optional[int] = None
    total_duration_seconds: Optional[int] = None
    explicit: bool = False
    metadata_status: str = DbMetadataStatus.FULL  # raw persisted value
    metadata_updated_at: Optional[str] = None

    artists: List[AlbumArtistLink] = []
    tracks: List[TrackGraph] = []
    genres: List["GenreRefLite"] = []

    @property
    def cover_url(self) -> Optional[str]:
        return self.cover_cached_url or self.cover_original_url


from .track import GenreRefLite  # noqa: E402  (avoid a forward-ref cycle at import)

AlbumGraph.model_rebuild()


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class AlbumResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    title: str
    album_type: str = AlbumType.ALBUM
    release_date: Optional[str] = None
    label: Optional[str] = None
    cover_url: Optional[str] = None
    total_tracks: int = 0
    total_duration_seconds: int = 0
    explicit: bool = False
    artists: List[ArtistRefResponse] = []
    genres: List[GenreResponse] = []
    tracks: List[TrackResponse] = []
    metadata_status: str = MetadataStatus.FRESH
