"""Artwork caching service tests (validation, WebP, persistence, security)."""

import io

import pytest

from cache.response_cache import ResponseCache
from services.artwork.artwork_service import ArtworkService
from services.catalog.jobs import BackgroundJobs
from tests.conftest import FakeGateway


def _png_bytes() -> bytes:
    from PIL import Image
    img = Image.new("RGB", (8, 8), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _service(gateway):
    return ArtworkService(gateway=gateway, response_cache=ResponseCache(None), jobs=BackgroundJobs())


async def test_caches_artwork_to_storage_and_updates_row(monkeypatch):
    gw = FakeGateway({"artists": [{
        "id": "a1", "image_original_url": "https://e-cdn-images.dzcdn.net/images/artist/x.jpg",
        "image_cached_url": None, "image_cache_status": "pending",
    }]})
    svc = _service(gw)

    async def fake_download(url):
        return _png_bytes(), "image/png"
    monkeypatch.setattr(svc, "_download", fake_download)

    url = await svc.cache_entity_artwork("artist", "a1",
                                         "https://e-cdn-images.dzcdn.net/images/artist/x.jpg")
    assert url and url.endswith("artists/a1/profile.webp")
    row = await gw.fetch_one("artists", [("id", "eq", "a1")])
    assert row["image_cached_url"] == url
    assert row["image_cache_status"] == "cached"
    # uploaded as webp
    up = next(c for c in gw.calls if c[0] == "storage_upload")
    assert up[1]["content_type"] == "image/webp"


async def test_rejects_disallowed_host(monkeypatch):
    gw = FakeGateway({"artists": [{"id": "a1", "image_cache_status": "pending"}]})
    svc = _service(gw)
    called = {"n": 0}

    async def fake_download(url):
        called["n"] += 1
        return _png_bytes(), "image/png"
    monkeypatch.setattr(svc, "_download", fake_download)

    url = await svc.cache_entity_artwork("artist", "a1", "https://evil.example.com/x.jpg")
    assert url is None
    assert called["n"] == 0                      # never downloaded from a bad host
    row = await gw.fetch_one("artists", [("id", "eq", "a1")])
    assert row["image_cache_status"] == "skipped"


async def test_skips_when_already_cached(monkeypatch):
    gw = FakeGateway({"artists": [{
        "id": "a1", "image_cached_url": "https://storage.example/x.webp",
        "image_cache_status": "cached",
    }]})
    svc = _service(gw)
    called = {"n": 0}

    async def fake_download(url):
        called["n"] += 1
        return _png_bytes(), "image/png"
    monkeypatch.setattr(svc, "_download", fake_download)

    await svc.cache_entity_artwork("artist", "a1",
                                   "https://e-cdn-images.dzcdn.net/images/artist/x.jpg")
    assert called["n"] == 0                       # idempotent — no re-download
