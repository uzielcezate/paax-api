"""
config.py — Centralized configuration for paax-api (Phase 2).

All tunable values come from environment variables with sensible defaults.
Nothing here is hardcoded to a secret. Required production variables are
validated by ``Settings.validate_required()`` which is called at startup so the
process fails fast when misconfigured.

Import the singleton::

    from config import settings
    settings.supabase_url
    settings.ttl.catalog_artist
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Small env helpers
# ---------------------------------------------------------------------------

def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# TTL policy (Redis response-cache lifetimes, in seconds)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TTLPolicy:
    """Redis response-cache TTLs. See docs/CACHE_STRATEGY.md."""

    catalog_artist: int = _env_int("CATALOG_ARTIST_TTL_SECONDS", 86_400)          # 24h
    catalog_album: int = _env_int("CATALOG_ALBUM_TTL_SECONDS", 86_400)            # 24h
    catalog_track: int = _env_int("CATALOG_TRACK_TTL_SECONDS", 86_400)            # 24h
    discography: int = _env_int("CATALOG_DISCOGRAPHY_TTL_SECONDS", 21_600)        # 6h
    search: int = _env_int("SEARCH_CACHE_TTL_SECONDS", 1_800)                     # 30m
    home: int = _env_int("HOME_CACHE_TTL_SECONDS", 7_200)                         # 2h
    youtube_match: int = _env_int("YOUTUBE_MATCH_TTL_SECONDS", 1_209_600)         # 14d
    negative: int = _env_int("NEGATIVE_CACHE_TTL_SECONDS", 300)                   # 5m
    ingest_lock: int = _env_int("INGEST_LOCK_TTL_SECONDS", 30)                    # 30s
    refresh_lock: int = _env_int("REFRESH_LOCK_TTL_SECONDS", 30)                  # 30s


# ---------------------------------------------------------------------------
# Supabase persistent-catalog freshness windows (how long stored metadata is
# considered "fresh" before a background refresh is scheduled), in seconds.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreshnessPolicy:
    """Supabase metadata freshness windows. See docs/CACHE_STRATEGY.md."""

    artist: int = _env_int("ARTIST_FRESHNESS_SECONDS", 7 * 86_400)    # 7d
    album: int = _env_int("ALBUM_FRESHNESS_SECONDS", 30 * 86_400)     # 30d
    track: int = _env_int("TRACK_FRESHNESS_SECONDS", 30 * 86_400)     # 30d


# ---------------------------------------------------------------------------
# Concurrency & artwork limits
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Limits:
    max_deezer_concurrency: int = _env_int("MAX_DEEZER_CONCURRENCY", 5)
    max_youtube_match_concurrency: int = _env_int("MAX_YOUTUBE_MATCH_CONCURRENCY", 3)
    # Bounded fan-out for the partial-album discography upserts during artist
    # ingestion. Replaces the old serial loop that dominated first-open latency
    # for uncached artists (Phase 3.3 §3).
    max_discography_concurrency: int = _env_int("MAX_DISCOGRAPHY_CONCURRENCY", 8)
    artwork_max_bytes: int = _env_int("ARTWORK_MAX_BYTES", 5 * 1024 * 1024)       # 5 MiB
    artwork_download_timeout: int = _env_int("ARTWORK_DOWNLOAD_TIMEOUT_SECONDS", 10)
    lock_wait_seconds: int = _env_int("INGEST_LOCK_WAIT_SECONDS", 8)


# ---------------------------------------------------------------------------
# Top-level settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    # Supabase — service role, backend-only. NEVER expose the key.
    supabase_url: str = _env_str("SUPABASE_URL")
    supabase_service_role_key: str = _env_str("SUPABASE_SERVICE_ROLE_KEY")
    supabase_images_bucket: str = _env_str("SUPABASE_IMAGES_BUCKET", "music-images")

    # Deezer
    deezer_base_url: str = _env_str("DEEZER_BASE_URL", "https://api.deezer.com")
    deezer_timeout: int = _env_int("DEEZER_TIMEOUT_SECONDS", 15)

    # Redis (optional — caching degrades gracefully when absent)
    redis_url: str = _env_str("REDIS_URL")

    # Feature toggles
    supabase_enabled: bool = _env_bool("SUPABASE_ENABLED", True)
    artwork_caching_enabled: bool = _env_bool("ARTWORK_CACHING_ENABLED", True)

    ttl: TTLPolicy = field(default_factory=TTLPolicy)
    freshness: FreshnessPolicy = field(default_factory=FreshnessPolicy)
    limits: Limits = field(default_factory=Limits)

    @property
    def supabase_configured(self) -> bool:
        """True only when both URL and service-role key are present."""
        return bool(self.supabase_url and self.supabase_service_role_key)

    def validate_required(self, strict: bool) -> list[str]:
        """
        Return a list of human-readable problems with the current config.

        When ``strict`` is True (production), missing Supabase credentials are
        reported as problems. In non-strict mode (local dev / tests) they are
        allowed so the legacy endpoints still boot without Supabase.
        """
        problems: list[str] = []
        if self.supabase_enabled and strict:
            if not self.supabase_url:
                problems.append("SUPABASE_URL is required")
            if not self.supabase_service_role_key:
                problems.append("SUPABASE_SERVICE_ROLE_KEY is required")
        return problems


settings = Settings()
