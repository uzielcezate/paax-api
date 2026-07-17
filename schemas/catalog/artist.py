"""Artist schemas: DB row, entity graph, and normalized API response."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from .album import AlbumResponse
from .common import CamelModel, DbMetadataStatus, MetadataStatus
from .genre import GenreResponse
from .track import TrackGraph, TrackResponse


class ReleaseSummary(BaseModel):
    """A compact album entry used to build discography sections."""

    id: str
    deezer_id: Optional[int] = None
    title: str
    album_type: str = "album"
    release_date: Optional[str] = None
    cover_original_url: Optional[str] = None
    cover_cached_url: Optional[str] = None
    total_tracks: Optional[int] = None
    explicit: bool = False

    @property
    def cover_url(self) -> Optional[str]:
        return self.cover_cached_url or self.cover_original_url


class ArtistGraph(BaseModel):
    """A fully-loaded artist with genres, releases and (optional) top tracks."""

    id: str
    deezer_id: Optional[int] = None
    name: str
    image_original_url: Optional[str] = None
    image_cached_url: Optional[str] = None
    biography: Optional[str] = None
    country_code: Optional[str] = None
    is_verified: bool = False
    platform_followers_count: int = 0
    deezer_fans_count: Optional[int] = None
    metadata_status: str = DbMetadataStatus.FULL  # raw persisted value
    metadata_updated_at: Optional[str] = None

    genres: List["GenreRefLite"] = []
    releases: List[ReleaseSummary] = []
    top_tracks: List[TrackGraph] = []

    @property
    def image_url(self) -> Optional[str]:
        return self.image_cached_url or self.image_original_url


from .track import GenreRefLite  # noqa: E402

ArtistGraph.model_rebuild()


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class ReleaseResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    title: str
    album_type: str = "album"
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    total_tracks: Optional[int] = None
    explicit: bool = False


class DiscographySections(CamelModel):
    albums: List[ReleaseResponse] = []
    eps: List[ReleaseResponse] = []
    singles: List[ReleaseResponse] = []
    compilations: List[ReleaseResponse] = []


class ArtistResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    name: str
    image_url: Optional[str] = None
    biography: Optional[str] = None
    country_code: Optional[str] = None
    is_verified: bool = False
    platform_followers_count: int = 0
    # Deezer fan count is an EXTERNAL signal — never labeled as Paax followers.
    deezer_fans_count: Optional[int] = None
    genres: List[GenreResponse] = []
    latest_release: Optional[ReleaseResponse] = None
    discography: DiscographySections = DiscographySections()
    top_tracks: List[TrackResponse] = []
    metadata_status: str = MetadataStatus.FRESH
