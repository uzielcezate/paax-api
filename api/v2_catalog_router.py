"""
Normalized Supabase-first /v2 catalog endpoints (Phase 2.5).

These return ONE stable normalized model (see docs/api.md). They are additive:
the legacy /v2/artist, /v2/album, /v2/track, /v2/search, /v2/chart endpoints are
left untouched for the current Flutter app; Flutter adopts these in Phase 3
(prepared-but-not-connected). Because the legacy singular routes and /v2/search,
/v2/chart already exist, the normalized discovery/home are served at /v2/find and
/v2/home to avoid clobbering endpoints the live app depends on.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app_container import container
from mappers import discovery_mapper as dm
from services.deezer.errors import DeezerError

logger = logging.getLogger("paax.v2")

router = APIRouter(prefix="/v2", tags=["v2-catalog"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _require():
    if not container.supabase_ready:
        raise HTTPException(status_code=503, detail="catalog backend not configured")
    return container


def _result(data, status: str, *, not_found_detail: str = "not found") -> JSONResponse:
    if data is not None:
        return JSONResponse(content=data, headers={"X-Cache": status})
    if status == "processing":
        return JSONResponse(status_code=503, content={"error": {"code": "PROCESSING",
                            "message": "resource is being prepared"}}, headers={"Retry-After": "2"})
    if status == "degraded":
        return JSONResponse(status_code=503, content={"error": {"code": "UPSTREAM_DEGRADED",
                            "message": "upstream temporarily unavailable"}})
    raise HTTPException(status_code=404, detail=not_found_detail)


async def _rate_limit(request: Request, bucket: str, limit: int, window: int) -> None:
    c = container
    if c.rate_limiter is None:
        return
    client = request.client.host if request.client else "anon"
    allowed, remaining, reset = await c.rate_limiter.check(f"{bucket}:{client}", limit, window)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(reset),
                                     "X-RateLimit-Limit": str(limit),
                                     "X-RateLimit-Remaining": str(remaining)})


def _handle_deezer(exc: DeezerError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc),
                         headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None)


# ── artists ──────────────────────────────────────────────────────────────────

@router.get("/artists/deezer/{deezer_id}")
async def artist_by_deezer(deezer_id: int):
    c = _require()
    try:
        data, status = await c.catalog.get_artist(deezer_id)
    except DeezerError as exc:
        raise _handle_deezer(exc)
    return _result(data, status, not_found_detail="artist not found")


@router.get("/artists/{artist_id}")
async def artist_by_id(artist_id: str):
    c = _require()
    data, status = await c.catalog.get_artist_by_internal_id(artist_id)
    return _result(data, status, not_found_detail="artist not found")


@router.get("/artists/{artist_id}/discography")
async def artist_discography(artist_id: str):
    c = _require()
    data, status = await c.catalog.get_artist_by_internal_id(artist_id)
    if data is None:
        return _result(None, status, not_found_detail="artist not found")
    return JSONResponse(content={"artistId": artist_id, "discography": data.get("discography"),
                                 "latestRelease": data.get("latestRelease")},
                        headers={"X-Cache": status})


@router.get("/artists/{artist_id}/top")
async def artist_top(artist_id: str, limit: int = 20):
    c = _require()
    limit = max(1, min(limit, 50))
    artist, status = await c.catalog.get_artist_by_internal_id(artist_id)
    if artist is None:
        return _result(None, status, not_found_detail="artist not found")
    deezer_id = artist.get("deezerId")
    try:
        dz = await c.deezer.get_artist_top(deezer_id, limit=limit)
    except DeezerError as exc:
        raise _handle_deezer(exc)

    items = []
    to_match = []
    for t in (dz.get("data") or [])[:limit]:
        stored = await c.catalog.tracks.get_track_by_deezer_id(t.get("id"))
        item = dm.deezer_track_item(t)
        if stored is not None:
            item["id"] = stored.id
            item["playback"]["videoId"] = stored.playback.preferred_youtube_video_id
            item["playback"]["matchStatus"] = stored.playback.youtube_match_status
            if not stored.playback.preferred_youtube_video_id:
                to_match.append(stored.id)
        items.append(item)
    if to_match:
        c.playback.schedule_ids(to_match)
    return JSONResponse(content={"artistId": artist_id, "items": items})


# ── albums ───────────────────────────────────────────────────────────────────

@router.get("/albums/deezer/{deezer_id}")
async def album_by_deezer(deezer_id: int):
    c = _require()
    try:
        data, status = await c.catalog.get_album(deezer_id)
    except DeezerError as exc:
        raise _handle_deezer(exc)
    if data is not None:
        _schedule_album_matches(c, data)
    return _result(data, status, not_found_detail="album not found")


@router.get("/albums/{album_id}")
async def album_by_id(album_id: str):
    c = _require()
    data, status = await c.catalog.get_album_by_internal_id(album_id)
    if data is not None:
        _schedule_album_matches(c, data)
    return _result(data, status, not_found_detail="album not found")


def _schedule_album_matches(c, album_data: dict) -> None:
    ids = [t.get("id") for t in album_data.get("tracks", [])
           if t.get("id") and not (t.get("playback") or {}).get("videoId")]
    if ids:
        c.playback.schedule_ids(ids)


# ── tracks ───────────────────────────────────────────────────────────────────

@router.get("/tracks/deezer/{deezer_id}")
async def track_by_deezer(deezer_id: int):
    c = _require()
    try:
        data, status = await c.catalog.get_track(deezer_id)
    except DeezerError as exc:
        raise _handle_deezer(exc)
    return _result(data, status, not_found_detail="track not found")


@router.get("/tracks/{track_id}")
async def track_by_id(track_id: str):
    c = _require()
    data, status = await c.catalog.get_track_by_internal_id(track_id)
    return _result(data, status, not_found_detail="track not found")


@router.post("/tracks/{track_id}/resolve-playback")
async def resolve_playback(track_id: str, request: Request):
    c = _require()
    await _rate_limit(request, "resolve", limit=30, window=60)
    playback = await c.playback.resolve_and_persist(track_id)
    return {"trackId": track_id, "playback": playback}


@router.post("/tracks/{track_id}/report-playback-failure")
async def report_playback_failure(track_id: str, request: Request):
    c = _require()
    await _rate_limit(request, "report", limit=20, window=60)
    playback = await c.playback.report_failure(track_id)
    return {"trackId": track_id, "playback": playback}


# ── discovery / home (normalized; see module docstring for path choice) ──────

@router.get("/find")
async def find(request: Request, q: str, type: str = "tracks", limit: int = 20, offset: int = 0):
    c = _require()
    await _rate_limit(request, "find", limit=60, window=60)
    limit = max(1, min(limit, 50))
    data, status = await c.search.search(type, q, limit=limit, offset=offset)
    return JSONResponse(content=data, headers={"X-Cache": status})


@router.get("/home")
async def home(country: str = "US", limit: int = 50):
    c = _require()
    limit = max(1, min(limit, 100))
    data, status = await c.home.get_home(country, limit)
    return _result(data, status, not_found_detail="home unavailable")
