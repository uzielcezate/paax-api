"""
Normalized Deezer upstream errors.

The ingestion/service layer translates these into controlled HTTP responses so
Flutter never sees a raw stack trace or a blanket 500 for an upstream hiccup:

    DeezerNotFound      -> 404 (controlled not-found)
    DeezerRateLimited   -> 429 (+ retry metadata)
    DeezerUnavailable   -> 503 (upstream temporarily unavailable; e.g. 403/5xx)
    DeezerTimeout       -> 504
    DeezerBadResponse   -> 502 (malformed upstream payload)

All are subclasses of Exception so existing ``except Exception`` fallbacks in the
legacy hybrid services keep working unchanged.
"""

from __future__ import annotations

from typing import Optional


class DeezerError(Exception):
    """Base class for all normalized Deezer upstream failures."""

    http_status = 502
    #: whether retrying the same request later might succeed
    retryable = False

    def __init__(self, message: str, *, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class DeezerNotFound(DeezerError):
    http_status = 404
    retryable = False


class DeezerRateLimited(DeezerError):
    http_status = 429
    retryable = True


class DeezerUnavailable(DeezerError):
    """Upstream reachable but refusing/erroring transiently (403, 5xx, busy)."""

    http_status = 503
    retryable = True


class DeezerTimeout(DeezerError):
    http_status = 504
    retryable = True


class DeezerBadResponse(DeezerError):
    """Malformed or unparseable upstream payload."""

    http_status = 502
    retryable = False
