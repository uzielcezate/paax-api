"""
Pure functions that convert Supabase catalog rows (dicts) into domain graph
models. No I/O — the graph loader supplies already-fetched rows.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from schemas.catalog.album import AlbumArtistLink, AlbumGraph
from schemas.catalog.artist import ArtistGraph, ReleaseSummary
from schemas.catalog.playback import PlaybackState
from schemas.catalog.track import (
    AlbumSummary,
    GenreRefLite,
    TrackArtistLink,
    TrackGraph,
)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def genre_ref(row: Dict[str, Any]) -> GenreRefLite:
    return GenreRefLite(
        id=row["id"],
        deezer_id=row.get("deezer_id"),
        name=row["name"],
        slug=row.get("slug"),
    )


def track_artist_link(ta_row: Dict[str, Any], artist_row: Dict[str, Any]) -> TrackArtistLink:
    return TrackArtistLink(
        artist_id=ta_row["artist_id"],
        deezer_id=artist_row.get("deezer_id"),
        name=artist_row.get("name") or "Unknown Artist",
        role=ta_row.get("role") or "primary",
        position=ta_row.get("position") or 1,
        display_name=ta_row.get("display_name"),
    )


def album_artist_link(aa_row: Dict[str, Any], artist_row: Dict[str, Any]) -> AlbumArtistLink:
    return AlbumArtistLink(
        artist_id=aa_row["artist_id"],
        deezer_id=artist_row.get("deezer_id"),
        name=artist_row.get("name") or "Unknown Artist",
        role=aa_row.get("role") or "primary",
        position=aa_row.get("position") or 1,
    )


def album_summary(row: Dict[str, Any]) -> AlbumSummary:
    return AlbumSummary(
        id=row["id"],
        deezer_id=row.get("deezer_id"),
        title=row.get("title") or "",
        cover_original_url=row.get("cover_original_url"),
        cover_cached_url=row.get("cover_cached_url"),
        release_date=_iso(row.get("release_date")),
    )


def playback_state(track_row: Dict[str, Any]) -> PlaybackState:
    conf = track_row.get("youtube_match_confidence")
    return PlaybackState(
        youtube_audio_video_id=track_row.get("youtube_audio_video_id"),
        youtube_music_video_id=track_row.get("youtube_music_video_id"),
        preferred_youtube_video_id=track_row.get("preferred_youtube_video_id"),
        youtube_audio_match_type=track_row.get("youtube_audio_match_type"),
        youtube_music_video_match_type=track_row.get("youtube_music_video_match_type"),
        youtube_match_status=track_row.get("youtube_match_status") or "pending",
        youtube_match_confidence=float(conf) if conf is not None else None,
        youtube_match_reason=track_row.get("youtube_match_reason"),
        youtube_failure_count=track_row.get("youtube_failure_count") or 0,
    )


def track_graph(
    track_row: Dict[str, Any],
    *,
    album: Optional[AlbumSummary],
    artists: list[TrackArtistLink],
    genres: list[GenreRefLite],
) -> TrackGraph:
    bpm = track_row.get("bpm")
    return TrackGraph(
        id=track_row["id"],
        deezer_id=track_row.get("deezer_id"),
        title=track_row.get("title") or "Unknown",
        title_short=track_row.get("title_short"),
        track_number=track_row.get("track_number"),
        disc_number=track_row.get("disc_number"),
        duration_seconds=track_row.get("duration_seconds"),
        explicit=bool(track_row.get("explicit")),
        isrc=track_row.get("isrc"),
        bpm=float(bpm) if bpm is not None else None,
        image_original_url=track_row.get("image_original_url"),
        image_cached_url=track_row.get("image_cached_url"),
        metadata_status=track_row.get("metadata_status") or "full",
        metadata_updated_at=_iso(track_row.get("metadata_updated_at")),
        album=album,
        artists=sorted(artists, key=lambda a: a.position),
        genres=genres,
        playback=playback_state(track_row),
    )


def album_graph(
    album_row: Dict[str, Any],
    *,
    artists: list[AlbumArtistLink],
    tracks: list[TrackGraph],
    genres: list[GenreRefLite],
) -> AlbumGraph:
    return AlbumGraph(
        id=album_row["id"],
        deezer_id=album_row.get("deezer_id"),
        title=album_row.get("title") or "Unknown Album",
        album_type=album_row.get("album_type") or "album",
        release_date=_iso(album_row.get("release_date")),
        release_year=album_row.get("release_year"),
        label=album_row.get("label"),
        upc=album_row.get("upc"),
        cover_original_url=album_row.get("cover_original_url"),
        cover_cached_url=album_row.get("cover_cached_url"),
        total_tracks=album_row.get("total_tracks"),
        total_duration_seconds=album_row.get("total_duration_seconds"),
        explicit=bool(album_row.get("explicit")),
        metadata_status=album_row.get("metadata_status") or "full",
        metadata_updated_at=_iso(album_row.get("metadata_updated_at")),
        artists=sorted(artists, key=lambda a: a.position),
        tracks=tracks,
        genres=genres,
    )


def release_summary(album_row: Dict[str, Any]) -> ReleaseSummary:
    return ReleaseSummary(
        id=album_row["id"],
        deezer_id=album_row.get("deezer_id"),
        title=album_row.get("title") or "Unknown Album",
        album_type=album_row.get("album_type") or "album",
        release_date=_iso(album_row.get("release_date")),
        release_year=album_row.get("release_year"),
        cover_original_url=album_row.get("cover_original_url"),
        cover_cached_url=album_row.get("cover_cached_url"),
        total_tracks=album_row.get("total_tracks"),
        explicit=bool(album_row.get("explicit")),
    )


def artist_graph(
    artist_row: Dict[str, Any],
    *,
    genres: list[GenreRefLite],
    releases: list[ReleaseSummary],
    top_tracks: list[TrackGraph],
) -> ArtistGraph:
    fans = artist_row.get("deezer_fans_count")
    return ArtistGraph(
        id=artist_row["id"],
        deezer_id=artist_row.get("deezer_id"),
        name=artist_row.get("name") or "Unknown Artist",
        image_original_url=artist_row.get("image_original_url"),
        image_cached_url=artist_row.get("image_cached_url"),
        biography=artist_row.get("biography"),
        country_code=artist_row.get("country_code"),
        is_verified=bool(artist_row.get("is_verified")),
        platform_followers_count=artist_row.get("platform_followers_count") or 0,
        deezer_fans_count=int(fans) if fans is not None else None,
        metadata_status=artist_row.get("metadata_status") or "full",
        metadata_updated_at=_iso(artist_row.get("metadata_updated_at")),
        genres=genres,
        releases=releases,
        top_tracks=top_tracks,
    )
