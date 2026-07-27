"""
Domain graph -> normalized API response models.

This is the single boundary where internal/persisted values become the stable
contract Flutter consumes. Raw Supabase rows are NEVER returned directly.
"""

from __future__ import annotations

from typing import List, Optional

from mappers.release_ordering import sort_releases
from schemas.catalog.album import AlbumGraph, AlbumResponse
from schemas.catalog.artist import (
    ArtistGraph,
    ArtistResponse,
    DiscographySections,
    ReleaseResponse,
    ReleaseSummary,
)
from schemas.catalog.common import AlbumType, MetadataStatus
from schemas.catalog.genre import GenreResponse
from schemas.catalog.playback import PlaybackResponse, PlaybackState
from schemas.catalog.track import (
    AlbumRefResponse,
    ArtistRefResponse,
    TrackGraph,
    TrackResponse,
)


def _api_status(db_status: str, override: Optional[str]) -> str:
    return override or MetadataStatus.from_db(db_status)


def _playback(state: PlaybackState) -> PlaybackResponse:
    return PlaybackResponse(
        provider="youtube",
        engine="iframe",
        video_id=state.preferred_youtube_video_id,
        audio_video_id=state.youtube_audio_video_id,
        music_video_id=state.youtube_music_video_id,
        preferred_type=state.preferred_type,
        match_status=state.youtube_match_status,
        match_confidence=state.youtube_match_confidence,
    )


def _genres(graph_genres) -> List[GenreResponse]:
    return [
        GenreResponse(id=g.id, deezer_id=g.deezer_id, name=g.name, slug=g.slug)
        for g in graph_genres
    ]


def track_response(g: TrackGraph, *, metadata_override: Optional[str] = None) -> TrackResponse:
    artists = [
        ArtistRefResponse(
            id=a.artist_id, deezer_id=a.deezer_id, name=a.name, role=a.role, position=a.position
        )
        for a in g.artists
    ]
    album = None
    if g.album is not None:
        album = AlbumRefResponse(
            id=g.album.id,
            deezer_id=g.album.deezer_id,
            title=g.album.title,
            cover_url=g.album.cover_url,
            release_date=g.album.release_date,
        )
    cover = g.image_cached_url or g.image_original_url or (g.album.cover_url if g.album else None)
    return TrackResponse(
        id=g.id,
        deezer_id=g.deezer_id,
        title=g.title,
        title_short=g.title_short,
        artists=artists,
        artist_display_name=g.artist_display_name,
        album=album,
        duration_seconds=g.duration_seconds,
        explicit=g.explicit,
        track_number=g.track_number,
        disc_number=g.disc_number,
        isrc=g.isrc,
        bpm=g.bpm,
        genres=_genres(g.genres),
        cover_url=cover,
        metadata_status=_api_status(g.metadata_status, metadata_override),
        playback=_playback(g.playback),
    )


def album_response(g: AlbumGraph, *, metadata_override: Optional[str] = None) -> AlbumResponse:
    artists = [
        ArtistRefResponse(
            id=a.artist_id, deezer_id=a.deezer_id, name=a.name, role=a.role, position=a.position
        )
        for a in g.artists
    ]
    tracks = [track_response(t) for t in g.tracks]
    total_tracks = g.total_tracks if g.total_tracks is not None else len(tracks)
    total_duration = g.total_duration_seconds
    if total_duration is None:
        total_duration = sum((t.duration_seconds or 0) for t in g.tracks)
    return AlbumResponse(
        id=g.id,
        deezer_id=g.deezer_id,
        title=g.title,
        album_type=g.album_type,
        release_date=g.release_date,
        label=g.label,
        cover_url=g.cover_url,
        total_tracks=total_tracks,
        total_duration_seconds=total_duration,
        explicit=g.explicit,
        artists=artists,
        genres=_genres(g.genres),
        tracks=tracks,
        metadata_status=_api_status(g.metadata_status, metadata_override),
    )


def _release_response(r: ReleaseSummary) -> ReleaseResponse:
    return ReleaseResponse(
        id=r.id,
        deezer_id=r.deezer_id,
        title=r.title,
        album_type=r.album_type,
        release_date=r.release_date,
        release_year=r.release_year,
        cover_url=r.cover_url,
        total_tracks=r.total_tracks,
        explicit=r.explicit,
    )


def _discography(releases: List[ReleaseSummary]) -> DiscographySections:
    sections = DiscographySections()
    # Defensive re-sort: the repository already returns releases newest-first,
    # but this guarantees malformed upstream ordering never leaks per category.
    for r in sort_releases(list(releases)):
        resp = _release_response(r)
        t = r.album_type
        if t == AlbumType.SINGLE:
            sections.singles.append(resp)
        elif t == AlbumType.EP:
            sections.eps.append(resp)
        elif t == AlbumType.COMPILATION:
            sections.compilations.append(resp)
        else:  # album/live/soundtrack/other -> main album shelf
            sections.albums.append(resp)
    return sections


def artist_response(g: ArtistGraph, *, metadata_override: Optional[str] = None) -> ArtistResponse:
    # Latest release (Phase 3.3 §4): the most recently released record of ANY
    # eligible type. Prefer the newest exactly-dated release (release_date DESC
    # NULLS LAST); fall back to the newest release overall only when none of the
    # artist's releases carry an exact date.
    ordered = sort_releases(list(g.releases))
    latest = None
    for r in ordered:
        if r.release_date:
            latest = _release_response(r)
            break
    if latest is None and ordered:
        latest = _release_response(ordered[0])
    return ArtistResponse(
        id=g.id,
        deezer_id=g.deezer_id,
        name=g.name,
        image_url=g.image_url,
        biography=g.biography,
        country_code=g.country_code,
        is_verified=g.is_verified,
        platform_followers_count=g.platform_followers_count,
        deezer_fans_count=g.deezer_fans_count,
        genres=_genres(g.genres),
        latest_release=latest,
        discography=_discography(g.releases),
        top_tracks=[track_response(t) for t in g.top_tracks],
        metadata_status=_api_status(g.metadata_status, metadata_override),
    )
