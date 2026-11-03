"""
/v2/demo — portfolio demo sessions (see docs/features/demo-mode.md).

POST /v2/demo/sessions        → mint an isolated 10-minute demo identity
GET  /v2/demo/sessions/me     → authoritative status for the bearer token
POST /v2/demo/sessions/end    → end early: mark + delete the identity

Abuse controls: per-IP and global fixed-window rate limits (Redis; degrade
open like the rest of the API), a kill switch (DEMO_ENABLED), and the CORS
allowlist from FRONTEND_ORIGINS (the portfolio origin must be listed).
Purely additive: no existing endpoint or auth behaviour is touched.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app_container import container
import config as _config
from services.demo.demo_session_service import DemoError

logger = logging.getLogger("paax.demo")

router = APIRouter(prefix="/v2/demo", tags=["demo"])


def _error(status: int, code: str, message: str, headers=None) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}},
                        headers=headers)


def _service():
    svc = getattr(container, "demo", None)
    if svc is None or not _config.settings.demo_enabled:
        return None
    return svc


def client_ip(request: Request) -> str:
    """Client IP for the per-IP rate limit, resolved for the production topology
    client -> Cloudflare -> Railway edge -> app (api.paaxmusic.app answers with
    `Server: cloudflare` + `x-railway-edge`).

    1. `CF-Connecting-IP`: set by Cloudflare from the TCP peer; a client cannot
       forge it through the proxy. Preferred whenever present.
    2. Otherwise the RIGHTMOST `X-Forwarded-For` hop: proxies append, so the
       last entry was written by the trusted edge, while the first entry can be
       supplied by the client itself (the previous implementation trusted the
       first hop, which let a caller pick its own bucket).
    3. Otherwise the socket peer.
    """
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


async def _rate_limited(request: Request):
    limiter = container.rate_limiter
    if limiter is None:
        return None
    ip = client_ip(request)
    allowed, remaining, reset = await limiter.check(
        f"demo:create:{ip}", _config.settings.demo_rate_limit_per_ip, _config.settings.demo_rate_limit_window)
    if allowed:
        allowed, remaining, reset = await limiter.check(
            "demo:create:global", _config.settings.demo_rate_limit_global, 3600)
    if not allowed:
        return _error(429, "DEMO_RATE_LIMITED", "too many demo sessions, try again later",
                      headers={"Retry-After": str(reset),
                               "X-RateLimit-Remaining": str(remaining)})
    return None


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


@router.post("/sessions", status_code=201)
async def create_demo_session(request: Request):
    svc = _service()
    if svc is None:
        return _error(503, "DEMO_DISABLED", "demo sessions are not available")
    limited = await _rate_limited(request)
    if limited is not None:
        return limited

    client = "portfolio"
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("client"), str):
            client = body["client"][:40]
    except Exception:  # noqa: BLE001 — body is optional
        pass

    # Backstop sweep (pg_cron is the primary cleaner). Never fails the request.
    try:
        await svc.sweep_expired()
    except Exception:  # noqa: BLE001
        pass

    try:
        payload = await svc.create(client=client)
    except DemoError as exc:
        return _error(exc.status, exc.code, exc.message)
    payload["launch"] = {"web_url": _config.settings.demo_web_url, "client": client}
    return JSONResponse(status_code=201, content=payload,
                        headers={"Cache-Control": "no-store"})


@router.get("/sessions/me")
async def demo_session_status(authorization: Optional[str] = Header(default=None)):
    svc = _service()
    if svc is None:
        return _error(503, "DEMO_DISABLED", "demo sessions are not available")
    token = _bearer(authorization)
    if token is None:
        return _error(401, "DEMO_UNAUTHORIZED", "missing bearer token")
    try:
        return JSONResponse(content=await svc.status(token), headers={"Cache-Control": "no-store"})
    except DemoError as exc:
        return _error(exc.status, exc.code, exc.message)


@router.post("/sessions/end")
async def end_demo_session(authorization: Optional[str] = Header(default=None)):
    svc = _service()
    if svc is None:
        return _error(503, "DEMO_DISABLED", "demo sessions are not available")
    token = _bearer(authorization)
    if token is None:
        return _error(401, "DEMO_UNAUTHORIZED", "missing bearer token")
    try:
        return JSONResponse(content=await svc.end(token), headers={"Cache-Control": "no-store"})
    except DemoError as exc:
        return _error(exc.status, exc.code, exc.message)
