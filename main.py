"""
paax-api — Metadata & Discovery Backend
FastAPI service powering search, browse, and catalog endpoints for Paax.

What this service does:
  - Music search via ytmusicapi
  - Track / artist / album metadata
  - Home feed, charts, moods/genres
  - Watch queue / related tracks
  - Lyrics
  - Library & playlist management (authenticated)
  - Redis-backed response caching

What this service does NOT do:
  - Stream URL resolution        → paax-stream
  - Invidious integration        → paax-stream
  - Audio format selection       → paax-stream
  - Playback URL proxying        → paax-stream
"""

from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import os
import json
import tempfile
import asyncio
import datetime
from cache import get_redis_client, make_cache_key, cache_get, cache_set
from ytmusicapi.navigation import nav, SINGLE_COLUMN_TAB, SECTION_LIST_ITEM, GRID_ITEMS, GRID, CAROUSEL_CONTENTS
from ytmusicapi.parsers.library import parse_albums
try:
    from ytmusicapi.continuations import get_continuation_params
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
yt: YTMusic = None          # type: ignore
_tmp_oauth_path: str | None = None
_is_authenticated: bool     = False
redis_client                = None  # initialised in lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize YTMusic and Redis on startup; clean up on shutdown."""
    global yt, _tmp_oauth_path, _is_authenticated, redis_client

    oauth_json_str = os.environ.get("YTMUSIC_OAUTH_JSON")

    if oauth_json_str:
        # Production: env var contains the full oauth.json content
        try:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            tmp.write(oauth_json_str)
            tmp.close()
            _tmp_oauth_path = tmp.name
            yt = YTMusic(_tmp_oauth_path)
            _is_authenticated = True
            print("[paax-api] YTMusic initialized with OAuth via env var ✓")
        except Exception as e:
            print(f"[paax-api] OAuth env init failed: {e}. Falling back to unauthenticated.")
            yt = YTMusic()
    elif os.path.exists("oauth.json"):
        # Local dev: oauth.json file present
        try:
            yt = YTMusic("oauth.json")
            _is_authenticated = True
            print("[paax-api] YTMusic initialized with local oauth.json ✓")
        except Exception as e:
            print(f"[paax-api] Local oauth.json failed: {e}. Falling back to unauthenticated.")
            yt = YTMusic()
    else:
        # Unauthenticated — public content only
        yt = YTMusic()
        print("[paax-api] YTMusic running unauthenticated (public content only)")

    # Redis
    redis_client    = get_redis_client()
    redis_enabled   = redis_client is not None
    print(f"[Cache] Redis {'enabled' if redis_enabled else 'disabled'}.")

    yield  # App runs here

    # Cleanup
    if _tmp_oauth_path and os.path.exists(_tmp_oauth_path):
        os.unlink(_tmp_oauth_path)
        print("[paax-api] Cleaned up temp oauth file.")

    if redis_client:
        await redis_client.aclose()
        print("[Cache] Redis connection closed.")


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------
app = FastAPI(
    title="paax-api",
    description="Music metadata and discovery backend for Paax. Does NOT resolve audio streams.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: always allow localhost/LAN; add prod origins via FRONTEND_ORIGINS env var
_local_origins_regex = (
    r"https?://("
    r"localhost"
    r"|127\.0\.0\.1"
    r"|10\.0\.2\.2"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?"
)
_extra_origins_raw = os.environ.get("FRONTEND_ORIGINS", "")
_extra_origins     = [o.strip() for o in _extra_origins_raw.split(",") if o.strip()]

print(f"[CORS] LAN regex active. Extra origins: {_extra_origins or '(none)'}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_origins,
    allow_origin_regex=_local_origins_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {
        "service": "paax-api",
        "description": "Music metadata and discovery backend",
        "status": "running",
    }


@app.get("/health")
def health():
    """Railway / uptime health check."""
    return {"ok": True, "authenticated": _is_authenticated}


@app.get("/cache/status")
async def cache_status():
    """Report whether Redis is configured and reachable."""
    redis_enabled = redis_client is not None
    redis_ok      = False
    if redis_enabled:
        try:
            await redis_client.ping()
            redis_ok = True
        except Exception:
            pass
    return {"redis_enabled": redis_enabled, "redis_ok": redis_ok}


# ---------------------------------------------------------------------------
# Auth (YTMusic OAuth — local dev helpers)
# ---------------------------------------------------------------------------

@app.get("/auth/status")
def get_auth_status():
    return {"authenticated": _is_authenticated}


@app.post("/auth/reload")
def reload_auth():
    """Reload YTMusic from local oauth.json (local dev only)."""
    global yt, _is_authenticated
    if os.path.exists("oauth.json"):
        try:
            yt = YTMusic("oauth.json")
            _is_authenticated = True
            return {"status": "reloaded", "authenticated": True}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
    yt = YTMusic()
    _is_authenticated = False
    return {"status": "reloaded", "authenticated": False}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_TTL_SEARCH = 900    # 15 minutes
_TTL_HOME   = 21600  # 6 hours


@app.get("/search")
async def search(q: str, filter: str = None, limit: int = 20):
    """
    Search for music content.

    filter options:
      songs | videos | albums | artists | playlists |
      community_playlists | featured_playlists | uploads
    """
    cache_key = make_cache_key("search", {"q": q, "filter": filter, "limit": limit})
    cached    = await cache_get(redis_client, cache_key)
    if cached is not None:
        print(f"[Cache] HIT {cache_key}")
        return JSONResponse(content=cached, headers={"X-Cache": "HIT"})

    try:
        results = yt.search(query=q, filter=filter, limit=limit)
        payload = {"data": results}
        await cache_set(redis_client, cache_key, payload, _TTL_SEARCH)
        print(f"[Cache] MISS {cache_key}")
        return JSONResponse(content=payload, headers={"X-Cache": "MISS"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Home / Discovery
# ---------------------------------------------------------------------------

@app.get("/home")
async def get_home():
    cache_key = make_cache_key("home", {})
    cached    = await cache_get(redis_client, cache_key)
    if cached is not None:
        print(f"[Cache] HIT {cache_key}")
        return JSONResponse(content=cached, headers={"X-Cache": "HIT"})
    try:
        data = yt.get_home()
        await cache_set(redis_client, cache_key, data, _TTL_HOME)
        print(f"[Cache] MISS {cache_key}")
        return JSONResponse(content=data, headers={"X-Cache": "MISS"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/home/discover")
def get_home_discover():
    try:
        return yt.get_home(limit=3)
    except Exception as e:
        print(f"Error fetching home/discover: {e}")
        return []


@app.get("/charts")
def get_charts(country: str = "US"):
    try:
        return yt.get_charts(country=country)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/home/charts")
def get_home_charts(country: str = "US"):
    try:
        c_code = country if country != "Global" else "ZZ"
        charts = yt.get_charts(country=c_code)
        response: Dict[str, List] = {"tracks": [], "albums": [], "artists": []}

        if "songs" in charts and "items" in charts["songs"]:
            response["tracks"] = charts["songs"]["items"]
        elif "videos" in charts and "items" in charts["videos"]:
            response["tracks"] = [i for i in charts["videos"]["items"] if "videoId" in i]

        if "albums" in charts and "items" in charts["albums"]:
            response["albums"] = charts["albums"]["items"]

        if "artists" in charts and "items" in charts["artists"]:
            response["artists"] = charts["artists"]["items"]

        return response
    except Exception as e:
        print(f"Error fetching charts for {country}: {e}")
        return {"tracks": [], "albums": [], "artists": []}


@app.get("/moods")
def get_moods():
    try:
        return yt.get_mood_categories()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/moods/{params}")
def get_mood_playlists(params: str):
    try:
        return yt.get_mood_playlists(params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Genre slug → ytmusicapi mood params mapping
GENRE_PARAMS = {
    "pop":       "ggMPOg1uX3d4cnZHdWxmd2ZP",
    "rock":      "ggMPOg1uX0xRR3hPZDlIWjd6",
    "hip hop":   "ggMPOg1uXzdzb2Rub29zaGdl",
    "indie":     "ggMPOg1uX21NWWpBbU01SDgy",
    "r&b":       "ggMPOg1uX2JxQ2hxc2J5UFhR",
    "k-pop":     "ggMPOg1uX0JrbjBDOFFPSzJW",
    "jazz":      "ggMPOg1uX3lPcDFRaE9wM1BS",
    "metal":     "ggMPOg1uXzdlSXhKZ0hMV1Z4",
    "classical": "ggMPOg1uXzNiX3JjZndocTZy",
}


@app.get("/genre/{slug}")
def get_genre_page(slug: str, country: str = "US"):
    try:
        response: Dict[str, Any] = {
            "title": slug.title(),
            "playlists": [],
            "tracks": [],
            "artists": [],
        }
        lower_slug = slug.lower()

        if lower_slug in GENRE_PARAMS:
            try:
                response["playlists"] = yt.get_mood_playlists(GENRE_PARAMS[lower_slug])
            except Exception as e:
                print(f"Error fetching mood playlists for {slug}: {e}")

        if not response["playlists"]:
            response["playlists"] = yt.search(f"{slug} playlists", filter="playlists", limit=10)

        response["tracks"]  = yt.search(f"{slug} top songs",   filter="songs",   limit=10)
        response["artists"] = yt.search(f"{slug} artists",     filter="artists", limit=10)
        return response
    except Exception as e:
        print(f"Error fetching genre page for {slug}: {e}")
        return {"title": slug, "playlists": [], "tracks": [], "artists": []}


@app.get("/home/top")
def get_home_top(genre: str, country: str = "US"):
    try:
        limit = 10
        return {
            "tracks":  yt.search(f"{genre} top songs",    filter="songs",   limit=limit),
            "albums":  yt.search(f"{genre} top albums",   filter="albums",  limit=limit),
            "artists": yt.search(f"{genre} top artists",  filter="artists", limit=limit),
        }
    except Exception as e:
        print(f"Error fetching home/top for {genre}: {e}")
        return {"tracks": [], "albums": [], "artists": []}


# ---------------------------------------------------------------------------
# Artist
# ---------------------------------------------------------------------------

@app.get("/artist/{channelId}")
def get_artist(channelId: str):
    try:
        return yt.get_artist(channelId)
    except Exception as e:
        print(f"Error fetching artist {channelId}: {e}")
        return {
            "name": "Artist (Error)", "channelId": channelId,
            "thumbnails": [],
            "songs": {"results": []}, "albums": {"results": []},
            "singles": {"results": []}, "related": {"results": []},
        }


@app.get("/artist/{channelId}/albums")
def get_artist_albums(channelId: str, params: str = None):
    try:
        return yt.get_artist_albums(channelId=channelId, params=params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/artist/{channelId}/albums/page")
def get_artist_albums_page(channelId: str, params: str = None, ctoken: str = None):
    try:
        endpoint = "browse"
        if ctoken:
            additionalParams = "&ctoken=" + ctoken + "&continuation=" + ctoken
            body     = {"browseId": channelId, "params": params}
            response = yt._send_request(endpoint, body, additionalParams)
            if "continuationContents" in response:
                contents = response["continuationContents"]
                results  = (
                    contents.get("gridContinuation") or
                    contents.get("musicShelfContinuation") or {}
                )
            else:
                results = {}
            items_raw = results.get("items") or results.get("contents") or []
            container = results
        else:
            body         = {"browseId": channelId, "params": params}
            response     = yt._send_request(endpoint, body)
            results_root = nav(response, SINGLE_COLUMN_TAB + SECTION_LIST_ITEM)
            grid         = nav(results_root, GRID, True)
            musicShelf   = nav(results_root, ["musicShelfRenderer"], True)
            if grid:
                items_raw = grid.get("items", []);  container = grid
            elif musicShelf:
                items_raw = musicShelf.get("contents", []); container = musicShelf
            else:
                carousel = nav(results_root, CAROUSEL_CONTENTS, True)
                items_raw = carousel or []; container = {}

        albums     = parse_albums(items_raw)
        next_token = None
        if "continuations" in container:
            try:
                next_token = container["continuations"][0]["nextContinuationData"]["continuation"]
            except Exception:
                pass

        return {"items": albums, "nextPageToken": next_token}
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Album
# ---------------------------------------------------------------------------

@app.get("/album/{browseId}")
def get_album(browseId: str):
    try:
        data = None
        try:
            data = yt.get_album(browseId)
        except Exception:
            try:
                data = yt.get_playlist(browseId)
                if "author" in data and "artists" not in data:
                    data["artists"] = (
                        [data["author"]] if isinstance(data["author"], dict)
                        else [{"name": str(data["author"]), "id": ""}]
                    )
            except Exception as e2:
                raise Exception(f"Failed to fetch album or playlist: {e2}")

        # Ensure tracks have duration_seconds
        if "tracks" in data:
            for track in data["tracks"]:
                if not track.get("duration_seconds"):
                    raw = track.get("duration", "")
                    if raw:
                        try:
                            parts = raw.strip().split(":")
                            val   = 0
                            for part in parts:
                                val = val * 60 + int(part)
                            track["duration_seconds"] = val
                        except Exception:
                            track["duration_seconds"] = 0
                    else:
                        track["duration_seconds"] = 0

            # ── Artist enrichment ─────────────────────────────────────────
            # ytmusicapi get_album() often returns garbage artists like
            # "32M plays" or empty arrays for tracks. Also, album tracks
            # may have OMV (Official Music Video) videoIds instead of the
            # ATV (Audio Track Video) ones used everywhere else.
            import re
            _VIEW_COUNT_RE = re.compile(
                r'^\d[\d,.]*[KMBkmb]?\s*(plays?|views?|listens?)?$',
                re.IGNORECASE,
            )

            album_artists = data.get("artists", [])
            for track in data["tracks"]:
                track_artists = track.get("artists") or []
                valid_artists = [
                    a for a in track_artists
                    if a.get("name") and not _VIEW_COUNT_RE.match(a["name"].strip())
                ]

                if not valid_artists:
                    vid = track.get("videoId", "")
                    if vid:
                        try:
                            watch = yt.get_watch_playlist(videoId=vid)
                            if watch and "tracks" in watch and watch["tracks"]:
                                w_track = watch["tracks"][0]
                                w_artists = w_track.get("artists") or []
                                if w_artists:
                                    track["artists"] = w_artists
                                w_vid = w_track.get("videoId", "")
                                if w_vid and w_vid != vid:
                                    track["videoId"] = w_vid
                        except Exception:
                            if album_artists:
                                track["artists"] = album_artists

            # Promote enriched track artists to album level if richer
            for track in data["tracks"]:
                t_artists = track.get("artists") or []
                real = [
                    a for a in t_artists
                    if a.get("name") and not _VIEW_COUNT_RE.match(a["name"].strip())
                ]
                if len(real) > len(album_artists):
                    data["artists"] = real
                    album_artists = real
                    break

        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Track / Song
# ---------------------------------------------------------------------------

@app.get("/song/{videoId}")
def get_song(videoId: str):
    try:
        return yt.get_song(videoId)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/song/{videoId}/related")
def get_song_related(videoId: str):
    try:
        return yt.get_watch_playlist(videoId=videoId)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/lyrics/{videoId}")
def get_lyrics(videoId: str):
    try:
        watch = yt.get_watch_playlist(videoId=videoId)
        if watch.get("lyrics"):
            return yt.get_lyrics(browseId=watch["lyrics"])
        return {"error": "No lyrics found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Watch queue / radio
# ---------------------------------------------------------------------------

@app.get("/watch")
def get_watch_playlist(videoId: str = None, playlistId: str = None):
    try:
        return yt.get_watch_playlist(videoId=videoId, playlistId=playlistId)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Library (authenticated)
# ---------------------------------------------------------------------------

@app.get("/library/liked")
def get_liked_songs(limit: int = 100):
    try:
        return yt.get_liked_songs(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/library/playlists")
def get_library_playlists(limit: int = 25):
    try:
        return yt.get_library_playlists(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Playlists (authenticated write operations)
# ---------------------------------------------------------------------------

@app.post("/rate")
def rate_song(videoId: str = Body(...), rating: str = Body(...)):
    """rating: 'LIKE' | 'INDIFFERENT' | 'DISLIKE'"""
    try:
        return yt.rate_song(videoId, rating)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/playlists")
def create_playlist(title: str = Body(...), description: str = Body("")):
    try:
        return yt.create_playlist(title, description)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/playlists/{playlistId}")
def delete_playlist(playlistId: str):
    try:
        return yt.delete_playlist(playlistId)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/playlists/{playlistId}/items")
def add_playlist_items(playlistId: str, videoIds: List[str] = Body(...)):
    try:
        return yt.add_playlist_items(playlistId, videoIds)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/playlists/{playlistId}/items")
def remove_playlist_items(playlistId: str, videoIds: List[str] = Body(...)):
    try:
        return yt.remove_playlist_items(playlistId, videoIds)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    print(f"[paax-api] Starting server on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

