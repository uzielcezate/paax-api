"""
ArtworkService — cache catalog artwork into Supabase Storage (music-images).

Flow: original Deezer URL is returned immediately by the catalog; in the
background this downloads it securely, validates, converts to WebP, uploads to a
deterministic entity-scoped path, updates the cached URL/status, and invalidates
the affected response cache.

Security:
  * download host allowlist (Deezer CDN) — prevents arbitrary-URL / SSRF fetch;
  * hard size cap + timeout; MIME + real-image validation (Pillow);
  * destination path is derived from the internal entity id (clients never
    choose it); upload is scoped to the entity's own object;
  * service-role only (backend). Failures never surface to catalog endpoints.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from config import settings
from schemas.catalog.common import ImageCacheStatus

logger = logging.getLogger("paax.artwork")

_ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_ALLOWED_HOST_SUFFIXES = ("dzcdn.net", "deezer.com")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# entity_type -> (table, path_template, original_col, cached_col, cached_at_col)
_ENTITY = {
    "artist": ("artists", "artists/{id}/profile.webp", "image_original_url", "image_cached_url", "image_cached_at"),
    "album": ("albums", "albums/{id}/cover.webp", "cover_original_url", "cover_cached_url", "cover_cached_at"),
    "genre": ("genres", "genres/{id}/cover.webp", "image_original_url", "image_cached_url", "image_cached_at"),
}


class ArtworkService:
    def __init__(self, *, gateway, response_cache, jobs, bucket: Optional[str] = None):
        self.g = gateway
        self.cache = response_cache
        self.jobs = jobs
        self.bucket = bucket or settings.supabase_images_bucket

    # ── scheduling from the read path ───────────────────────────────────────

    def schedule_for_graph(self, entity_type: str, graph) -> None:
        """Schedule artwork caching for an artist/album graph if not yet cached."""
        if not settings.artwork_caching_enabled or entity_type not in _ENTITY:
            return
        original = getattr(graph, "image_original_url", None) or getattr(graph, "cover_original_url", None)
        cached = getattr(graph, "image_cached_url", None) or getattr(graph, "cover_cached_url", None)
        if original and not cached:
            self.jobs.schedule(
                lambda: self.cache_entity_artwork(entity_type, graph.id, original),
                label=f"artwork:{entity_type}:{graph.id}",
            )

    # ── core ────────────────────────────────────────────────────────────────

    async def cache_entity_artwork(self, entity_type: str, entity_id: str,
                                   original_url: str) -> Optional[str]:
        cfg = _ENTITY.get(entity_type)
        if cfg is None or not original_url:
            return None
        table, path_tmpl, orig_col, cached_col, cached_at_col = cfg

        if not self._host_allowed(original_url):
            logger.warning("[artwork] disallowed host for %s %s", entity_type, entity_id)
            await self._set_status(table, entity_id, ImageCacheStatus.SKIPPED)
            return None

        # Idempotency: skip if already cached/processing.
        row = await self.g.fetch_one(table, [("id", "eq", entity_id)],
                                     columns=f"id,image_cache_status,{cached_col}")
        if row and (row.get(cached_col) or row.get("image_cache_status") in
                    (ImageCacheStatus.CACHED, ImageCacheStatus.PROCESSING)):
            return row.get(cached_col)

        await self._set_status(table, entity_id, ImageCacheStatus.PROCESSING)
        try:
            data, content_type = await self._download(original_url)
            webp = self._to_webp(data)
            path = path_tmpl.format(id=entity_id)
            public_url = await self.g.storage_upload(self.bucket, path, webp, "image/webp", upsert=True)
        except Exception as exc:  # noqa: BLE001 — never break catalog endpoints
            logger.warning("[artwork] cache failed for %s %s: %s", entity_type, entity_id, exc)
            await self._set_status(table, entity_id, ImageCacheStatus.FAILED)
            return None

        await self.g.update(table, {
            cached_col: public_url,
            cached_at_col: _now_iso(),
            "image_cache_status": ImageCacheStatus.CACHED,
        }, [("id", "eq", entity_id)])
        await self._invalidate(entity_type, entity_id)
        logger.info("[artwork] cached %s %s -> %s", entity_type, entity_id, path)
        return public_url

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _host_allowed(url: str) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            return False
        return any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES)

    async def _download(self, url: str) -> tuple[bytes, str]:
        max_bytes = settings.limits.artwork_max_bytes
        timeout = settings.limits.artwork_download_timeout
        async with httpx.AsyncClient(timeout=timeout, verify=True, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype not in _ALLOWED_MIME:
                    raise ValueError(f"disallowed content-type: {ctype}")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise ValueError("artwork exceeds max size")
        return bytes(buf), ctype

    @staticmethod
    def _to_webp(data: bytes) -> bytes:
        from PIL import Image  # lazy import
        with Image.open(io.BytesIO(data)) as img:  # also validates it is a real image
            img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=82, method=4)
            return out.getvalue()

    async def _set_status(self, table: str, entity_id: str, status: str) -> None:
        try:
            await self.g.update(table, {"image_cache_status": status}, [("id", "eq", entity_id)])
        except Exception as exc:  # noqa: BLE001
            logger.debug("[artwork] status update failed: %s", exc)

    async def _invalidate(self, entity_type: str, entity_id: str) -> None:
        try:
            from cache.cache_keys import CacheKeys
            key = {"artist": CacheKeys.artist, "album": CacheKeys.album}.get(entity_type)
            if key:
                await self.cache.invalidate(key(entity_id))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[artwork] invalidate failed: %s", exc)
