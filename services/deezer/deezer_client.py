"""
Async Deezer API client.

Phase 2 hardening (backward-compatible with the legacy hybrid services):
  * TLS verification ENABLED (was ``verify=False`` — see docs/security.md).
  * Normalized upstream errors (services/deezer/errors.py), including Deezer's
    quirk of returning HTTP 200 with an ``{"error": {...}}`` body.
  * Bounded concurrency (MAX_DEEZER_CONCURRENCY) so ingestion can't fan out
    unboundedly.
  * In-flight request de-duplication: concurrent identical GETs share one call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from config import settings
from services.deezer.errors import (
    DeezerBadResponse,
    DeezerNotFound,
    DeezerRateLimited,
    DeezerTimeout,
    DeezerUnavailable,
)

logger = logging.getLogger("paax.deezer")

_MAX_RETRIES = 3


class DeezerClient:
    """Thin async wrapper around api.deezer.com with secure, bounded access."""

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None,
                 max_concurrency: Optional[int] = None):
        self._base_url = base_url or settings.deezer_base_url
        self._timeout = timeout or settings.deezer_timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._sem = asyncio.Semaphore(max_concurrency or settings.limits.max_deezer_concurrency)
        self._inflight: Dict[str, "asyncio.Future[Dict[str, Any]]"] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
                follow_redirects=True,
                verify=True,  # secure TLS (certifi CA bundle) — never disable
            )
        return self._client

    # ── core GET (deduped) ──────────────────────────────────────────────────

    @staticmethod
    def _key(path: str, params: Optional[Dict]) -> str:
        if not params:
            return path
        parts = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{path}?{parts}"

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        key = self._key(path, params)
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing  # de-dupe concurrent identical requests

        fut: "asyncio.Future[Dict[str, Any]]" = asyncio.ensure_future(self._do_get(path, params))
        self._inflight[key] = fut
        try:
            return await fut
        finally:
            self._inflight.pop(key, None)

    async def _do_get(self, path: str, params: Optional[Dict]) -> Dict[str, Any]:
        client = await self._ensure_client()
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with self._sem:
                    resp = await client.get(path, params=params)
            except (httpx.TimeoutException,) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise DeezerTimeout(f"Deezer timeout on {path}") from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise DeezerUnavailable(f"Deezer transport error on {path}: {exc}") from exc

            status = resp.status_code
            if status == 429:
                retry_after = _parse_retry_after(resp)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(retry_after or (2 ** attempt))
                    continue
                raise DeezerRateLimited(f"Deezer 429 on {path}", retry_after=retry_after)
            if status == 404:
                raise DeezerNotFound(f"Deezer 404 on {path}")
            if status == 403:
                raise DeezerUnavailable(f"Deezer 403 on {path}")
            if status >= 500:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise DeezerUnavailable(f"Deezer {status} on {path}")

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise DeezerBadResponse(f"Deezer non-JSON payload on {path}") from exc

            _raise_for_deezer_error_body(path, data)
            return data

        # unreachable, but keep the type checker happy
        raise DeezerUnavailable(f"Deezer request failed on {path}: {last_exc}")

    # ── Search ──────────────────────────────────────────────────────────────

    async def search_tracks(self, query: str, limit: int = 25) -> Dict:
        return await self._get("/search", {"q": query, "limit": limit})

    async def search_albums(self, query: str, limit: int = 25) -> Dict:
        return await self._get("/search/album", {"q": query, "limit": limit})

    async def search_artists(self, query: str, limit: int = 25) -> Dict:
        return await self._get("/search/artist", {"q": query, "limit": limit})

    # ── Artist ──────────────────────────────────────────────────────────────

    async def get_artist(self, artist_id: int) -> Dict:
        return await self._get(f"/artist/{artist_id}")

    async def get_artist_top(self, artist_id: int, limit: int = 50) -> Dict:
        return await self._get(f"/artist/{artist_id}/top", {"limit": limit})

    async def get_artist_albums(self, artist_id: int, limit: int = 100) -> Dict:
        return await self._get(f"/artist/{artist_id}/albums", {"limit": limit})

    async def get_artist_related(self, artist_id: int) -> Dict:
        return await self._get(f"/artist/{artist_id}/related")

    # ── Album ───────────────────────────────────────────────────────────────

    async def get_album(self, album_id: int) -> Dict:
        return await self._get(f"/album/{album_id}")

    async def get_album_tracks(self, album_id: int, limit: int = 300) -> Dict:
        """Album tracklist (each track has its own artist; contributors need /track/{id})."""
        return await self._get(f"/album/{album_id}/tracks", {"limit": limit})

    # ── Track ───────────────────────────────────────────────────────────────

    async def get_track(self, track_id: int) -> Dict:
        return await self._get(f"/track/{track_id}")

    # ── Chart ───────────────────────────────────────────────────────────────

    async def get_chart(self) -> Dict:
        return await self._get("/chart")

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _parse_retry_after(resp: httpx.Response) -> Optional[int]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _raise_for_deezer_error_body(path: str, data: Any) -> None:
    """Deezer often returns HTTP 200 with an ``error`` object; normalize it."""
    if not isinstance(data, dict):
        return
    err = data.get("error")
    if not err:
        return
    code = None
    message = ""
    if isinstance(err, dict):
        code = err.get("code")
        message = err.get("message", "") or ""
    text = f"Deezer error on {path}: code={code} {message}".strip()

    if code == 800:                       # DataException "no data"
        raise DeezerNotFound(text)
    if code == 4 or "quota" in message.lower():
        raise DeezerRateLimited(text)
    if code == 700:                       # service busy
        raise DeezerUnavailable(text)
    raise DeezerUnavailable(text)
