import httpx
import asyncio
from typing import Optional, Dict, Any, List

_BASE_URL = "https://api.deezer.com"
_TIMEOUT = 15.0


class DeezerClient:
    """Thin async wrapper around api.deezer.com."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=_TIMEOUT,
                headers={"Accept": "application/json"},
                follow_redirects=True,
                verify=False,  # Railway (Linux) has proper CA; local Windows may not
            )
        return self._client

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        client = await self._ensure_client()
        for attempt in range(3):
            resp = await client.get(path, params=params)
            if resp.status_code == 429:
                wait = 2 ** attempt
                print(f"[Deezer] 429 rate-limited, retrying in {wait}s…")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise Exception("Deezer API rate limit exceeded after 3 retries")

    # ── Search ────────────────────────────────────────────────────────────────

    async def search_tracks(self, query: str, limit: int = 25) -> Dict:
        return await self._get("/search", {"q": query, "limit": limit})

    async def search_albums(self, query: str, limit: int = 25) -> Dict:
        return await self._get("/search/album", {"q": query, "limit": limit})

    async def search_artists(self, query: str, limit: int = 25) -> Dict:
        return await self._get("/search/artist", {"q": query, "limit": limit})

    # ── Artist ────────────────────────────────────────────────────────────────

    async def get_artist(self, artist_id: int) -> Dict:
        return await self._get(f"/artist/{artist_id}")

    async def get_artist_top(self, artist_id: int, limit: int = 50) -> Dict:
        return await self._get(f"/artist/{artist_id}/top", {"limit": limit})

    async def get_artist_albums(self, artist_id: int, limit: int = 100) -> Dict:
        return await self._get(f"/artist/{artist_id}/albums", {"limit": limit})

    async def get_artist_related(self, artist_id: int) -> Dict:
        return await self._get(f"/artist/{artist_id}/related")

    # ── Album ─────────────────────────────────────────────────────────────────

    async def get_album(self, album_id: int) -> Dict:
        return await self._get(f"/album/{album_id}")

    # ── Track ─────────────────────────────────────────────────────────────────

    async def get_track(self, track_id: int) -> Dict:
        return await self._get(f"/track/{track_id}")

    # ── Chart ─────────────────────────────────────────────────────────────────

    async def get_chart(self) -> Dict:
        return await self._get("/chart")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
