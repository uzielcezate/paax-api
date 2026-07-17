"""Track schemas: DB row, entity graph, and normalized API response."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from .common import CamelModel, DbMetadataStatus, MetadataStatus
from .genre import GenreResponse
from .playback import PlaybackResponse, PlaybackState


# ---------------------------------------------------------------------------
# Internal domain models
# ---------------------------------------------------------------------------

class TrackArtistLink(BaseModel):
    """A track's real credited artist (never derived from the album header)."""

    artist_id: str
    deezer_id: Optional[int] = None
    name: str
    role: str
    position: int
    display_name: Optional[str] = None


class AlbumSummary(BaseModel):
    """Compact album context embedded in a track."""

    id: str
    deezer_id: Optional[int] = None
    title: str
    cover_original_url: Optional[str] = None
    cover_cached_url: Optional[str] = None
    release_date: Optional[str] = None

    @property
    def cover_url(self) -> Optional[str]:
        return self.cover_cached_url or self.cover_original_url


class TrackGraph(BaseModel):
    """
    A fully-loaded track: identity, album context, *its own* credited artists,
    genres, playback mapping and freshness. This is what track repositories
    return and what the response mapper consumes.
    """

    id: str
    deezer_id: Optional[int] = None
    title: str
    title_short: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration_seconds: Optional[int] = None
    explicit: bool = False
    isrc: Optional[str] = None
    bpm: Optional[float] = None
    bpm_confidence: Optional[float] = None
    image_original_url: Optional[str] = None
    image_cached_url: Optional[str] = None
    metadata_status: str = DbMetadataStatus.FULL  # raw persisted value
    metadata_updated_at: Optional[str] = None

    album: Optional[AlbumSummary] = None
    artists: List[TrackArtistLink] = []
    genres: List["GenreRefLite"] = []
    playback: PlaybackState = PlaybackState()

    @property
    def artist_display_name(self) -> str:
        primary = [a for a in self.artists if a.role == "primary"]
        featured = [a for a in self.artists if a.role == "featured"]
        ordered = sorted(primary + featured, key=lambda a: a.position)
        if not ordered:
            ordered = sorted(self.artists, key=lambda a: a.position)
        # Prefer an explicit display_name if the primary carries one.
        for a in ordered:
            if a.role == "primary" and a.display_name:
                return a.display_name
        return ", ".join(a.name for a in ordered) or "Unknown Artist"


class GenreRefLite(BaseModel):
    id: str
    deezer_id: Optional[int] = None
    name: str
    slug: Optional[str] = None


TrackGraph.model_rebuild()


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class ArtistRefResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    name: str
    role: str
    position: int


class AlbumRefResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    title: str
    cover_url: Optional[str] = None
    release_date: Optional[str] = None


class TrackResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    title: str
    title_short: Optional[str] = None
    artists: List[ArtistRefResponse] = []
    artist_display_name: str = "Unknown Artist"
    album: Optional[AlbumRefResponse] = None
    duration_seconds: Optional[int] = None
    explicit: bool = False
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    isrc: Optional[str] = None
    bpm: Optional[float] = None
    genres: List[GenreResponse] = []
    cover_url: Optional[str] = None
    metadata_status: str = MetadataStatus.FRESH
    playback: PlaybackResponse = PlaybackResponse()
