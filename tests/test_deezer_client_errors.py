"""Deezer client normalized-error + dedup tests (respx-mocked httpx)."""

import asyncio

import httpx
import pytest
import respx

from services.deezer.deezer_client import DeezerClient
from services.deezer.errors import (
    DeezerNotFound,
    DeezerRateLimited,
    DeezerTimeout,
    DeezerUnavailable,
)

BASE = "https://api.deezer.com"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(*a, **k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _instant)


@respx.mock
async def test_http_404_maps_to_not_found():
    respx.get(f"{BASE}/track/1").mock(return_value=httpx.Response(404))
    client = DeezerClient()
    with pytest.raises(DeezerNotFound):
        await client.get_track(1)
    await client.close()


@respx.mock
async def test_http_403_maps_to_unavailable():
    respx.get(f"{BASE}/track/1").mock(return_value=httpx.Response(403))
    client = DeezerClient()
    with pytest.raises(DeezerUnavailable):
        await client.get_track(1)
    await client.close()


@respx.mock
async def test_http_429_maps_to_rate_limited():
    respx.get(f"{BASE}/track/1").mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    client = DeezerClient()
    with pytest.raises(DeezerRateLimited) as ei:
        await client.get_track(1)
    assert ei.value.retry_after == 1
    await client.close()


@respx.mock
async def test_deezer_200_error_body_code_800_not_found():
    respx.get(f"{BASE}/track/1").mock(
        return_value=httpx.Response(200, json={"error": {"type": "DataException", "message": "no data", "code": 800}})
    )
    client = DeezerClient()
    with pytest.raises(DeezerNotFound):
        await client.get_track(1)
    await client.close()


@respx.mock
async def test_deezer_200_quota_error_rate_limited():
    respx.get(f"{BASE}/track/1").mock(
        return_value=httpx.Response(200, json={"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}})
    )
    client = DeezerClient()
    with pytest.raises(DeezerRateLimited):
        await client.get_track(1)
    await client.close()


@respx.mock
async def test_timeout_maps_to_deezer_timeout():
    respx.get(f"{BASE}/track/1").mock(side_effect=httpx.ConnectTimeout("timed out"))
    client = DeezerClient()
    with pytest.raises(DeezerTimeout):
        await client.get_track(1)
    await client.close()


@respx.mock
async def test_success_returns_payload():
    respx.get(f"{BASE}/track/1").mock(return_value=httpx.Response(200, json={"id": 1, "title": "ok"}))
    client = DeezerClient()
    data = await client.get_track(1)
    assert data["title"] == "ok"
    await client.close()


@respx.mock
async def test_concurrent_identical_requests_are_deduped():
    route = respx.get(f"{BASE}/track/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    client = DeezerClient()
    results = await asyncio.gather(client.get_track(1), client.get_track(1), client.get_track(1))
    assert all(r["id"] == 1 for r in results)
    assert route.call_count == 1  # de-duplicated to a single upstream call
    await client.close()
