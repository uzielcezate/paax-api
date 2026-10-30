"""
DemoSessionService — isolated, server-enforced 10-minute portfolio demo identities.

Each visitor gets a REAL, throwaway Supabase user (never a shared account):

  1. ``auth.admin.create_user`` with a random, never-stored password,
     ``email_confirm=True`` and ``app_metadata.paax_demo = true``. app_metadata
     cannot be edited by clients; the database-side guard keys off it.
  2. The profile row (created by the existing signup trigger) is completed with
     the service role so the Flutter startup machine routes straight to the
     shell — no Complete Profile / onboarding screens for a visitor.
  3. ``demo_sessions`` records the AUTHORITATIVE expiry.
  4. The one-time password is exchanged for a normal GoTrue session (password
     grant with the PUBLIC anon key) and the tokens are returned. They belong
     to the throwaway identity only and die with it.

No library/follow data is seeded: Home shows the real catalog discovery
sections (Trending / Recently added) exactly as a brand-new real user sees them.

Ending (explicit or by sweep) deletes the auth user, which cascades to the
profile, library, playlists and notifications — nothing survives the session.

Everything is injected (gateway, auth admin, token client, clock) so the unit
tests run without network or the ``supabase`` package.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

import httpx

logger = logging.getLogger("paax.demo")

DEMO_FLAG = "paax_demo"


class DemoError(Exception):
    """Carries an HTTP status + stable error code for the router."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class AuthAdmin(Protocol):
    async def create_user(self, attributes: Dict[str, Any]) -> Any: ...
    async def delete_user(self, id: str, should_soft_delete: bool = False) -> None: ...


class TokenClient(Protocol):
    """Talks to GoTrue with a PUBLIC API key (never the service role / secret key)."""

    async def password_grant(self, email: str, password: str) -> Dict[str, Any]: ...
    async def get_user(self, access_token: str) -> Optional[Dict[str, Any]]: ...


class GoTrueHttp:
    """httpx implementation of :class:`TokenClient`.

    ``public_api_key`` is either the modern ``sb_publishable_...`` key or the
    legacy anon JWT. Both are accepted by the Supabase API gateway as the
    ``apikey`` header. The value is treated as an opaque string: it is never
    decoded, verified or used as a Bearer token.
    """

    def __init__(self, supabase_url: str, public_api_key: str, timeout: float = 10.0):
        if public_api_key.startswith("sb_secret_"):
            raise ValueError("GoTrueHttp must use a PUBLIC key, never sb_secret_/service role")
        self._base = supabase_url.rstrip("/") + "/auth/v1"
        self._headers = {"apikey": public_api_key, "Content-Type": "application/json"}
        self._timeout = timeout

    async def password_grant(self, email, password):
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(f"{self._base}/token", params={"grant_type": "password"},
                                headers=self._headers, json={"email": email, "password": password})
        if r.status_code != 200:
            raise DemoError(502, "DEMO_TOKEN_EXCHANGE_FAILED",
                            f"token exchange failed ({r.status_code})")
        return r.json()

    async def get_user(self, access_token):
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.get(f"{self._base}/user",
                               headers={**self._headers, "Authorization": f"Bearer {access_token}"})
        if r.status_code != 200:
            return None
        return r.json()


@dataclass(frozen=True)
class DemoConfig:
    session_seconds: int = 600
    email_domain: str = "demo.paaxmusic.app"
    display_name: str = "Demo Visitor"
    # Clearly-not-a-person profile facts required by Profile.isComplete.
    birth_date: str = "2000-01-01"
    country_code: str = "US"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _user_id(created: Any) -> Optional[str]:
    """Extract the id from a UserResponse / dict / object."""
    user = getattr(created, "user", None)
    if user is None and isinstance(created, dict):
        user = created.get("user", created)
    if user is None:
        return None
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


class DemoSessionService:
    def __init__(
        self,
        *,
        gateway,
        auth_admin: AuthAdmin,
        token_client: TokenClient,
        config: DemoConfig = DemoConfig(),
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._gw = gateway
        self._admin = auth_admin
        self._tokens = token_client
        self._cfg = config
        self._now = now

    # ── create ───────────────────────────────────────────────────────────────

    async def create(self, *, client: str = "portfolio") -> Dict[str, Any]:
        cfg = self._cfg
        started = self._now()
        expires = started + timedelta(seconds=cfg.session_seconds)
        username = f"demo_{secrets.token_hex(4)}"
        email = f"demo-{uuid.uuid4().hex}@{cfg.email_domain}"
        password = secrets.token_urlsafe(32)  # never stored, never logged

        try:
            created = await self._admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "app_metadata": {DEMO_FLAG: True, "demo_client": client},
                "user_metadata": {"username": username, "display_name": cfg.display_name},
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[demo] create_user failed: %s", type(exc).__name__)
            raise DemoError(502, "DEMO_IDENTITY_CREATE_FAILED", "could not create demo identity")

        uid = _user_id(created)
        if not uid:
            raise DemoError(502, "DEMO_IDENTITY_CREATE_FAILED", "identity response had no id")

        try:
            await self._bootstrap_profile(uid, username)
            await self._gw.insert("demo_sessions", {
                "user_id": uid,
                "started_at": _iso(started),
                "expires_at": _iso(expires),
                "client": client,
            })
            tokens = await self._tokens.password_grant(email, password)
        except Exception:
            # Never leave a half-initialised identity behind.
            await self._delete_user_quietly(uid)
            raise

        return {
            "session": {
                "id": uid,
                "started_at": _iso(started),
                "expires_at": _iso(expires),
                "duration_seconds": cfg.session_seconds,
                "server_time": _iso(self._now()),
            },
            "auth": {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "token_type": tokens.get("token_type", "bearer"),
                "expires_in": tokens.get("expires_in"),
            },
            "user": {"id": uid, "username": username, "display_name": cfg.display_name},
        }

    async def _bootstrap_profile(self, uid: str, username: str) -> None:
        cfg = self._cfg
        values = {
            "display_name": cfg.display_name,
            "birth_date": cfg.birth_date,
            "country_code": cfg.country_code,
            "onboarding_completed": True,
        }
        updated = await self._gw.update("profiles", values, [("id", "eq", uid)])
        if not updated:
            # Trigger may lag or the username may have collided — insert the minimum.
            await self._gw.insert("profiles", {"id": uid, "username": username, **values})

    # ── status / end ─────────────────────────────────────────────────────────

    async def _resolve_demo_user(self, access_token: str) -> str:
        user = await self._tokens.get_user(access_token)
        if not user:
            raise DemoError(401, "DEMO_UNAUTHORIZED", "invalid or expired token")
        meta = user.get("app_metadata") or {}
        if meta.get(DEMO_FLAG) is not True:
            raise DemoError(403, "DEMO_NOT_A_DEMO_USER", "token does not belong to a demo identity")
        return user["id"]

    async def status(self, access_token: str) -> Dict[str, Any]:
        uid = await self._resolve_demo_user(access_token)
        row = await self._gw.fetch_one("demo_sessions", [("user_id", "eq", uid)])
        return self._status_payload(uid, row)

    def _status_payload(self, uid: str, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        now = self._now()
        expires = _parse_ts(row.get("expires_at")) if row else None
        ended = _parse_ts(row.get("ended_at")) if row else None
        active = bool(row) and ended is None and expires is not None and expires > now
        remaining = int(max(0, (expires - now).total_seconds())) if (active and expires) else 0
        return {
            "session": {
                "id": uid,
                "active": active,
                "expires_at": _iso(expires) if expires else None,
                "remaining_seconds": remaining,
                "server_time": _iso(now),
            }
        }

    async def end(self, access_token: str) -> Dict[str, Any]:
        """Explicit end: mark the row, then delete the identity (cascade wipes data)."""
        uid = await self._resolve_demo_user(access_token)
        await self._gw.update("demo_sessions", {"ended_at": _iso(self._now())},
                              [("user_id", "eq", uid)])
        await self._delete_user_quietly(uid)
        return {"session": {"id": uid, "active": False, "ended": True,
                            "server_time": _iso(self._now())}}

    # ── sweep ────────────────────────────────────────────────────────────────

    async def sweep_expired(self, *, grace_seconds: int = 60, limit: int = 20) -> int:
        """Backstop for pg_cron: delete identities whose session expired."""
        cutoff = self._now() - timedelta(seconds=grace_seconds)
        try:
            rows: List[Dict[str, Any]] = await self._gw.fetch_all(
                "demo_sessions", [("expires_at", "lt", _iso(cutoff))],
                columns="user_id", order_by=("expires_at", False), limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[demo] sweep query failed: %s", type(exc).__name__)
            return 0
        deleted = 0
        for r in rows:
            if await self._delete_user_quietly(r["user_id"]):
                deleted += 1
        if deleted:
            logger.info("[demo] swept %d expired demo identities", deleted)
        return deleted

    async def _delete_user_quietly(self, uid: str) -> bool:
        try:
            await self._admin.delete_user(uid)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[demo] delete_user(%s) failed: %s", uid[:8], type(exc).__name__)
            return False
