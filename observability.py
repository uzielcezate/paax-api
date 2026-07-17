"""
Observability: request-correlation IDs + structured logging.

A correlation id is taken from the inbound ``X-Request-ID`` header (or generated)
and stored in a context var so every log line during that request carries it. It
is echoed back in the ``X-Request-ID`` response header.

Never logs secrets: the Supabase service-role key, OAuth tokens, cookies, or full
auth headers are never passed to the logger by any caller in this codebase.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id(existing: str | None = None) -> str:
    rid = (existing or "").strip() or uuid.uuid4().hex[:16]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure a single structured-ish stdout handler with the correlation id."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quiet noisy libraries.
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
