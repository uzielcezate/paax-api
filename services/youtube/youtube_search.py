"""
YouTube search via yt-dlp (no API key needed).

Uses ``ytsearchN:query`` with ``extract_flat=True`` for fast
metadata-only results (no stream extraction).
"""

import asyncio
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=3)


def _search_sync(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Blocking yt-dlp search — run in executor."""
    import yt_dlp  # lazy import

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",  # flat extraction for search results
    }

    # Use explicit ytsearchN: prefix for reliable search
    search_url = f"ytsearch{max_results}:{query}"

    results: List[Dict[str, Any]] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_url, download=False)

        if not info:
            return []

        entries = info.get("entries") or []
        for entry in entries:
            if not entry:
                continue
            # In flat mode, video ID may be in 'id', 'url', or parsed from 'url'
            vid = entry.get("id") or ""
            if not vid:
                url = entry.get("url", "")
                if "watch?v=" in url:
                    vid = url.split("watch?v=")[-1].split("&")[0]
                elif len(url) == 11:
                    vid = url
            if not vid or len(vid) != 11:
                continue
            results.append({
                "videoId": vid,
                "title": entry.get("title", ""),
                "channel": entry.get("uploader") or entry.get("channel") or "",
                "duration": entry.get("duration") or 0,
                "description": entry.get("description") or "",
                "viewCount": entry.get("view_count") or 0,
            })
    except Exception as e:
        print(f"[YT Search] Error for '{query}': {e}")

    return results


async def search_youtube(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Async YouTube search via yt-dlp.

    Returns list of::

        {videoId, title, channel, duration, description, viewCount}
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _search_sync, query, max_results)
