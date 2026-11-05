"""
/v2/demo — portfolio demo sessions (see docs/features/demo-mode.md).

POST /v2/demo/sessions        → mint an isolated 10-minute demo identity
GET  /v2/demo/sessions/me     → authoritative status for the bearer token
POST /v2/demo/sessions/end    → end early: mark + delete the identity

Abuse controls: per-IP and global fixed-window rate limits (Redis). Unlike the
catalog endpoints, session CREATION fails CLOSED (503 DEMO_UNAVAILABLE) when the
limits cannot be enforced — Redis missing or down — because every session mints
a real Supabase identity (DEMO_REQUIRE_RATE_LIMITER, default true). The per-IP
bucket is keyed ONLY on the proxy-controlled client address (`X-Real-IP`, see
`trusted_client_ip`); creation also fails closed when that identity is missing
or malformed. Plus a kill switch (DEMO_ENABLED) and the CORS allowlist from
FRONTEND_ORIGINS (the portfolio origin must be listed; CORS is a browser
courtesy, not the guard). Purely additive: no existing endpoint or auth
behaviour is touched.
"""

from __future__ import annotations

import ipaddress
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


def trusted_client_ip(request: Request) -> Optional[str]:
    """Rate-limit identity for POST /v2/demo/sessions, or None when it cannot be
    trusted (the caller then fails closed).

    Production topology: client -> Cloudflare -> Railway public HTTP edge -> app.
    Only ONE header on that path is written by infrastructure the caller cannot
    pre-fill: `X-Real-IP`. The Railway edge REPLACES any caller-sent
    `X-Real-IP` / `X-Forwarded-For` with its own (custom domains included) and,
    when the TCP peer is Cloudflare (published IPv4 ranges) with
    `CF-Connecting-IP` present, sets `X-Real-IP` to that visitor address.

    * via Cloudflare: `X-Real-IP` == the visitor (what the limiter wants);
    * direct to the Railway edge (Host-header bypass of Cloudflare): the edge
      does NOT trust a forged `CF-Connecting-IP`, so `X-Real-IP` == the caller's
      real socket address -> the caller still cannot pick its bucket;
    * `CF-Connecting-IP` reaches the app UNCHANGED on both paths and
      `X-Forwarded-For` ordering is not a documented contract, so neither is
      read here (the previous implementation preferred `CF-Connecting-IP`,
      which the direct path could forge).

    Documented limitation: requests that never cross the public edge — Railway
    private networking (`*.railway.internal`) or a TCP proxy — get NO header
    sanitisation, so any service in the environment could set `X-Real-IP`.
    The demo router is only exposed through the public edge.

    `DEMO_CLIENT_IP_HEADER` (default `x-real-ip`) names the trusted header; an
    EMPTY value switches to the socket peer for local development only.
    Values must parse as a single IP literal (no lists). IPv6 is bucketed per
    /64 so a visitor cannot rotate through a whole delegated prefix for free.
    """
    header = _config.settings.demo_client_ip_header
    if header:
        raw = request.headers.get(header, "").strip()
    else:  # local development: no edge in front
        raw = (request.client.host if request.client else "").strip()
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if addr.version == 6:
        return str(ipaddress.ip_network((addr, 64), strict=False))
    return str(addr)


def _identity_untrusted(reason: str):
    # Fail closed: never key a Supabase-identity-minting limit on a guessable
    # or missing address. Only the reason is logged, never the address.
    logger.warning("[demo] create refused: client identity untrusted (%s)", reason)
    return _error(503, "DEMO_UNAVAILABLE", "demo sessions are temporarily unavailable",
                  headers={"Retry-After": "60"})


def _limiter_unavailable():
    # Fail closed: minting identities without an enforced limit is not allowed.
    return _error(503, "DEMO_UNAVAILABLE", "demo sessions are temporarily unavailable",
                  headers={"Retry-After": "60"})


async def _rate_limited(request: Request):
    settings = _config.settings
    limiter = container.rate_limiter
    if limiter is None:
        return _limiter_unavailable() if settings.demo_require_rate_limiter else None
    ip = trusted_client_ip(request)
    if ip is None:
        return _identity_untrusted("missing_or_invalid")
    allowed, remaining, reset, enforced = await limiter.check_enforced(
        f"demo:create:{ip}", settings.demo_rate_limit_per_ip, settings.demo_rate_limit_window)
    if allowed and enforced:
        allowed, remaining, reset, enforced = await limiter.check_enforced(
            "demo:create:global", settings.demo_rate_limit_global, 3600)
    if not enforced and settings.demo_require_rate_limiter:
        return _limiter_unavailable()
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
