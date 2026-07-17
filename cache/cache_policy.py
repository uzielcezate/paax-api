"""TTL + freshness policy lookups (values live in config)."""

from __future__ import annotations

from config import settings


class TTL:
    """Redis response-cache TTLs (seconds), sourced from config.settings.ttl."""

    @staticmethod
    def artist() -> int:
        return settings.ttl.catalog_artist

    @staticmethod
    def album() -> int:
        return settings.ttl.catalog_album

    @staticmethod
    def track() -> int:
        return settings.ttl.catalog_track

    @staticmethod
    def discography() -> int:
        return settings.ttl.discography

    @staticmethod
    def search() -> int:
        return settings.ttl.search

    @staticmethod
    def home() -> int:
        return settings.ttl.home

    @staticmethod
    def youtube_match() -> int:
        return settings.ttl.youtube_match

    @staticmethod
    def negative() -> int:
        return settings.ttl.negative


def is_stale(metadata_updated_at_epoch: float | None, entity_type: str, now: float) -> bool:
    """
    True when a persisted entity is past its freshness window and a background
    refresh should be scheduled. A null timestamp (partial ingest) is stale.
    """
    if metadata_updated_at_epoch is None:
        return True
    window = {
        "artist": settings.freshness.artist,
        "album": settings.freshness.album,
        "track": settings.freshness.track,
    }.get(entity_type, settings.freshness.track)
    return (now - metadata_updated_at_epoch) > window
