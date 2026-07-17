"""
Controlled-value vocabularies and shared base models for the catalog domain.

These MUST match the Phase 1 DB CHECK constraints exactly (the database is the
source of truth). Verified against supabase/migrations/20260716090100_catalog.sql
and pg_constraint on 2026-07-17.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel as _pyd_to_camel


# ---------------------------------------------------------------------------
# Persisted metadata freshness (artists/albums/tracks.metadata_status)
# DB CHECK: ('partial','full','stale','failed')
# ---------------------------------------------------------------------------

class DbMetadataStatus:
    PARTIAL = "partial"   # ingested from an incomplete provider response
    FULL = "full"         # complete, current metadata
    STALE = "stale"       # past freshness window; refresh due
    FAILED = "failed"     # last refresh failed; prior data preserved

    ALL = {PARTIAL, FULL, STALE, FAILED}


# ---------------------------------------------------------------------------
# API-facing freshness label (derived, returned to Flutter as metadataStatus).
# "refreshing" is a transient, response-only signal (a background refresh was
# scheduled); it is NEVER written to the DB, which has no such value.
# ---------------------------------------------------------------------------

class MetadataStatus:
    FRESH = "fresh"
    STALE = "stale"
    REFRESHING = "refreshing"
    PARTIAL = "partial"
    FAILED = "failed"

    ALL = {FRESH, STALE, REFRESHING, PARTIAL, FAILED}

    @staticmethod
    def from_db(db_status: str) -> str:
        """Map a persisted metadata_status to the API freshness label."""
        return {
            DbMetadataStatus.FULL: MetadataStatus.FRESH,
            DbMetadataStatus.PARTIAL: MetadataStatus.PARTIAL,
            DbMetadataStatus.STALE: MetadataStatus.STALE,
            DbMetadataStatus.FAILED: MetadataStatus.FAILED,
        }.get(db_status, MetadataStatus.FRESH)


# ---------------------------------------------------------------------------
# Image cache lifecycle (*.image_cache_status)
# DB CHECK: ('pending','processing','cached','failed','skipped')
# ---------------------------------------------------------------------------

class ImageCacheStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    CACHED = "cached"
    FAILED = "failed"
    SKIPPED = "skipped"

    ALL = {PENDING, PROCESSING, CACHED, FAILED, SKIPPED}


# ---------------------------------------------------------------------------
# YouTube playback match lifecycle (tracks.youtube_match_status)
# DB CHECK: ('pending','resolving','matched','failed','stale','needs_review','unavailable')
# ---------------------------------------------------------------------------

class YoutubeMatchStatus:
    PENDING = "pending"            # never attempted
    RESOLVING = "resolving"        # a match attempt is in flight
    MATCHED = "matched"            # confident match stored
    FAILED = "failed"             # attempted, no acceptable candidate
    STALE = "stale"               # previously matched but flagged for re-check
    NEEDS_REVIEW = "needs_review"  # a low-confidence candidate was stored
    UNAVAILABLE = "unavailable"    # repeatedly failed playback; no known ID

    ALL = {PENDING, RESOLVING, MATCHED, FAILED, STALE, NEEDS_REVIEW, UNAVAILABLE}


# ---------------------------------------------------------------------------
# YouTube candidate classification (Phase 2 adds these columns:
# tracks.youtube_audio_match_type / youtube_music_video_match_type)
# ---------------------------------------------------------------------------

class MatchType:
    AUDIO_ONLY = "audio_only"                    # Topic / official audio / album-art upload
    OFFICIAL_MUSIC_VIDEO = "official_music_video"
    LIVE = "live"
    LYRIC_VIDEO = "lyric_video"
    REMIX = "remix"
    COVER = "cover"
    OTHER = "other"

    ALL = {
        AUDIO_ONLY, OFFICIAL_MUSIC_VIDEO, LIVE, LYRIC_VIDEO, REMIX, COVER, OTHER,
    }
    #: Types acceptable as an audio-only *preferred* playback source, best first.
    AUDIO_PREFERENCE_ORDER = [AUDIO_ONLY, LYRIC_VIDEO, OFFICIAL_MUSIC_VIDEO]


# ---------------------------------------------------------------------------
# Album release type (albums.album_type)
# DB CHECK: ('album','single','ep','compilation','live','soundtrack','other')
# ---------------------------------------------------------------------------

class AlbumType:
    ALBUM = "album"
    SINGLE = "single"
    EP = "ep"
    COMPILATION = "compilation"
    LIVE = "live"
    SOUNDTRACK = "soundtrack"
    OTHER = "other"

    ALL = {ALBUM, SINGLE, EP, COMPILATION, LIVE, SOUNDTRACK, OTHER}


# ---------------------------------------------------------------------------
# Artist roles
# track_artists.role: primary,featured,producer,composer,remixer,performer,vocalist,other
# album_artists.role: primary,featured,producer,composer,remixer,performer,other  (no vocalist)
# ---------------------------------------------------------------------------

class ArtistRole:
    PRIMARY = "primary"
    FEATURED = "featured"
    PRODUCER = "producer"
    COMPOSER = "composer"
    REMIXER = "remixer"
    PERFORMER = "performer"
    VOCALIST = "vocalist"
    OTHER = "other"

    TRACK_ALL = {PRIMARY, FEATURED, PRODUCER, COMPOSER, REMIXER, PERFORMER, VOCALIST, OTHER}
    ALBUM_ALL = {PRIMARY, FEATURED, PRODUCER, COMPOSER, REMIXER, PERFORMER, OTHER}

    @classmethod
    def track_role(cls, role: str) -> str:
        r = (role or "").lower().strip()
        return r if r in cls.TRACK_ALL else cls.OTHER

    @classmethod
    def album_role(cls, role: str) -> str:
        r = (role or "").lower().strip()
        return r if r in cls.ALBUM_ALL else cls.OTHER


class CamelModel(BaseModel):
    """
    Base for API response models. Fields are declared snake_case in Python and
    serialized as camelCase (``model_dump(by_alias=True)``). Construction accepts
    either casing.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_pyd_to_camel,
    )
