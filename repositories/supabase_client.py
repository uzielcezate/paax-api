"""
supabase_client.py — one reusable, backend-only Supabase gateway.

Design
------
* A single ``AsyncClient`` (service-role) is created at startup and shared for
  the process lifetime — never per request.
* Repositories depend on the narrow :class:`SupabaseGateway` protocol, not on
  ``supabase-py`` directly, so tests can inject a fake with no network and no
  ``supabase`` package installed.
* Entity *graphs* (album+tracks+artists+genres) are read and written through
  Postgres RPC functions (one round-trip, transactionally consistent). Simple
  lookups use ``fetch_one`` / ``fetch_all``.

Security
--------
The service-role key bypasses RLS and MUST stay server-side. It is read from
the environment only and is never logged or returned in a response.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from config import settings

logger = logging.getLogger("paax.supabase")

# (column, operator, value). operator in {"eq", "in", "is", "gt", "lt", "gte", "lte"}.
Filter = Tuple[str, str, Any]


@runtime_checkable
class SupabaseGateway(Protocol):
    """The narrow surface repositories use. Real + fake implementations conform."""

    async def fetch_one(
        self, table: str, filters: Sequence[Filter], columns: str = "*"
    ) -> Optional[Dict[str, Any]]:
        ...

    async def fetch_all(
        self,
        table: str,
        filters: Sequence[Filter] = (),
        columns: str = "*",
        order_by: Optional[Tuple[str, bool]] = None,   # (column, descending)
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ...

    async def rpc(self, fn: str, params: Dict[str, Any]) -> Any:
        ...

    async def update(
        self, table: str, values: Dict[str, Any], filters: Sequence[Filter]
    ) -> List[Dict[str, Any]]:
        ...

    async def insert(self, table: str, values: Dict[str, Any]) -> List[Dict[str, Any]]:
        ...

    async def delete(self, table: str, filters: Sequence[Filter]) -> List[Dict[str, Any]]:
        ...


class SupabaseUnavailable(RuntimeError):
    """Raised when a Supabase operation is attempted but no client is configured."""


class PostgrestGateway:
    """Concrete gateway backed by the ``supabase-py`` async client."""

    #: number of transient-error retries for read/write operations
    _MAX_RETRIES = 2

    def __init__(self, client: Any):
        self._client = client

    # -- reads ---------------------------------------------------------------

    async def fetch_one(self, table, filters, columns="*"):
        rows = await self.fetch_all(table, filters, columns=columns, limit=1)
        return rows[0] if rows else None

    async def fetch_all(self, table, filters=(), columns="*", order_by=None, limit=None):
        def _build():
            q = self._client.table(table).select(columns)
            for col, op, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "in":
                    q = q.in_(col, list(val))
                elif op == "is":
                    q = q.is_(col, val)
                elif op == "gt":
                    q = q.gt(col, val)
                elif op == "lt":
                    q = q.lt(col, val)
                elif op == "gte":
                    q = q.gte(col, val)
                elif op == "lte":
                    q = q.lte(col, val)
                else:
                    raise ValueError(f"unsupported filter operator: {op}")
            if order_by is not None:
                col, desc = order_by
                q = q.order(col, desc=desc)
            if limit is not None:
                q = q.limit(limit)
            return q

        resp = await self._execute(_build)
        return list(resp.data or [])

    # -- writes --------------------------------------------------------------

    async def rpc(self, fn, params):
        resp = await self._execute(lambda: self._client.rpc(fn, params))
        return resp.data

    async def update(self, table, values, filters):
        def _build():
            q = self._client.table(table).update(values)
            for col, op, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                else:
                    raise ValueError(f"update supports only eq filters, got {op}")
            return q

        resp = await self._execute(_build)
        return list(resp.data or [])

    async def insert(self, table, values):
        resp = await self._execute(lambda: self._client.table(table).insert(values))
        return list(resp.data or [])

    async def delete(self, table, filters):
        def _build():
            q = self._client.table(table).delete()
            for col, op, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "lt":
                    q = q.lt(col, val)
                else:
                    raise ValueError(f"delete supports only eq/lt filters, got {op}")
            return q

        resp = await self._execute(_build)
        return list(resp.data or [])

    # -- auth admin (service role) -------------------------------------------

    @property
    def auth_admin(self):
        """GoTrue admin API (create/delete users). Service-role only."""
        return self._client.auth.admin

    # -- internals -----------------------------------------------------------

    # -- storage -------------------------------------------------------------

    async def storage_upload(self, bucket: str, path: str, data: bytes,
                             content_type: str, upsert: bool = True) -> str:
        """Upload bytes to a Storage bucket and return the public object URL."""
        storage = self._client.storage.from_(bucket)
        await storage.upload(
            path, data,
            {"content-type": content_type, "upsert": "true" if upsert else "false"},
        )
        base = settings.supabase_url.rstrip("/")
        return f"{base}/storage/v1/object/public/{bucket}/{path}"

    async def _execute(self, build_query):
        """Run ``build_query().execute()`` with a small transient-retry loop."""
        last_exc: Optional[Exception] = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return await build_query().execute()
            except Exception as exc:  # noqa: BLE001 — normalize at repo/service layer
                last_exc = exc
                if attempt < self._MAX_RETRIES and _is_transient(exc):
                    await asyncio.sleep(0.2 * (2 ** attempt))
                    continue
                raise
        assert last_exc is not None
        raise last_exc


def _is_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(s in text for s in ("timeout", "temporarily", "connection", "reset", "503", "502"))


# ---------------------------------------------------------------------------
# Process-wide singleton (with test injection)
# ---------------------------------------------------------------------------

_gateway: Optional[SupabaseGateway] = None


async def init_supabase() -> Optional[SupabaseGateway]:
    """
    Create the shared gateway from environment credentials. Returns ``None``
    (caching/legacy still work) when Supabase is disabled or unconfigured.
    Called once from the FastAPI lifespan.
    """
    global _gateway
    if not settings.supabase_enabled:
        logger.info("[Supabase] disabled via SUPABASE_ENABLED=false")
        return None
    if not settings.supabase_configured:
        logger.warning("[Supabase] URL/service-role key missing — catalog persistence disabled")
        return None

    from supabase import acreate_client  # lazy import so tests need no package
    from supabase.lib.client_options import AsyncClientOptions

    client = await acreate_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=AsyncClientOptions(
            auto_refresh_token=False,
            persist_session=False,
            postgrest_client_timeout=settings.deezer_timeout,
        ),
    )
    _gateway = PostgrestGateway(client)
    logger.info("[Supabase] gateway initialized (host=%s)", _host_only(settings.supabase_url))
    return _gateway


def set_gateway(gateway: Optional[SupabaseGateway]) -> None:
    """Inject a gateway (used by tests)."""
    global _gateway
    _gateway = gateway


def get_gateway() -> SupabaseGateway:
    if _gateway is None:
        raise SupabaseUnavailable("Supabase gateway is not initialized")
    return _gateway


def gateway_available() -> bool:
    return _gateway is not None


def _host_only(url: str) -> str:
    """Return only the host for safe logging (never the key)."""
    try:
        return url.split("//", 1)[-1].split("/", 1)[0]
    except Exception:
        return "supabase"
