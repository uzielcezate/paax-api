"""
Application container — builds and holds the Phase 2 service graph once at
startup (never per request). Exposes a module-level singleton ``container`` that
routers read. Supabase-dependent services are only built when Supabase is
configured; otherwise ``supabase_ready`` is False and the normalized endpoints
respond 503 (the legacy /v2 endpoints keep working).
"""

from __future__ import annotations

import logging

from cache.response_cache import ResponseCache
from ingestion.catalog_ingestion_service import CatalogIngestionService
from repositories.catalog.album_repository import AlbumRepository
from repositories.catalog.artist_repository import ArtistRepository
from repositories.catalog.search_repository import SearchRepository
from repositories.catalog.track_repository import TrackRepository
from repositories.supabase_client import init_supabase
from services.artwork.artwork_service import ArtworkService
from services.catalog.catalog_service import CatalogService
from services.catalog.home_service import HomeService
from services.catalog.jobs import BackgroundJobs
from services.catalog.playback_service import PlaybackMatchingService
from services.catalog.search_service import SearchService
from services.demo.demo_session_service import DemoConfig, DemoSessionService, GoTrueHttp
from services.rate_limit import RateLimiter

logger = logging.getLogger("paax.container")


class AppContainer:
    def __init__(self):
        self.redis = None
        self.deezer = None
        self.gateway = None
        self.jobs = None
        self.response_cache = None
        self.rate_limiter = None
        self.ingestion = None
        self.artwork = None
        self.catalog = None
        self.search = None
        self.home = None
        self.playback = None
        self.demo = None
        self.matcher_available = False

    async def startup(self, *, redis, deezer) -> None:
        self.redis = redis
        self.deezer = deezer
        self.jobs = BackgroundJobs()
        self.response_cache = ResponseCache(redis)
        self.rate_limiter = RateLimiter(redis)

        try:
            import yt_dlp  # noqa: F401
            self.matcher_available = True
        except Exception:
            self.matcher_available = False

        self.gateway = await init_supabase()
        if self.gateway is None:
            logger.warning("[container] Supabase not configured — normalized /v2 disabled")
            return

        artist_repo = ArtistRepository(self.gateway)
        album_repo = AlbumRepository(self.gateway)
        track_repo = TrackRepository(self.gateway)
        search_repo = SearchRepository(self.gateway)

        self.ingestion = CatalogIngestionService(deezer, artist_repo, album_repo, track_repo)
        self.artwork = ArtworkService(gateway=self.gateway, response_cache=self.response_cache,
                                      jobs=self.jobs)
        self.catalog = CatalogService(
            redis=redis, response_cache=self.response_cache, jobs=self.jobs,
            ingestion=self.ingestion, artist_repo=artist_repo, album_repo=album_repo,
            track_repo=track_repo, artwork=self.artwork)
        self.search = SearchService(response_cache=self.response_cache, jobs=self.jobs,
                                    search_repo=search_repo, ingestion=self.ingestion, deezer=deezer)
        self.home = HomeService(redis=redis, response_cache=self.response_cache, jobs=self.jobs,
                                deezer=deezer, ingestion=self.ingestion)
        self.playback = PlaybackMatchingService(track_repo=track_repo, jobs=self.jobs,
                                                catalog_service=self.catalog)
        self.demo = self._build_demo()
        logger.info("[container] Phase 2 services ready (matcher=%s, demo=%s)",
                    self.matcher_available, self.demo is not None)

    def _build_demo(self):
        """Portfolio demo sessions — only when Supabase + a public API key are set.

        The public key is SUPABASE_PUBLISHABLE_KEY (preferred) or the legacy
        SUPABASE_ANON_KEY; see Settings.supabase_public_api_key.
        """
        from config import settings
        if not settings.demo_configured or not hasattr(self.gateway, "auth_admin"):
            logger.info("[container] demo sessions disabled "
                        "(DEMO_ENABLED / SUPABASE_PUBLISHABLE_KEY / SUPABASE_ANON_KEY)")
            return None
        logger.info("[container] demo token exchange uses the %s key",
                    settings.supabase_public_api_key_kind)
        return DemoSessionService(
            gateway=self.gateway,
            auth_admin=self.gateway.auth_admin,
            token_client=GoTrueHttp(settings.supabase_url, settings.supabase_public_api_key),
            config=DemoConfig(
                session_seconds=settings.demo_session_seconds,
                email_domain=settings.demo_email_domain,
                display_name=settings.demo_display_name,
            ),
        )

    @property
    def supabase_ready(self) -> bool:
        return self.gateway is not None and self.catalog is not None


#: process-wide singleton
container = AppContainer()
