"""Canonical, deterministic ordering for artist discography releases.

Phase 3.3 §5: within every discography category the order must be:

  1. exact ``release_date`` descending (newest first)
  2. ``release_year`` descending when an exact date is absent
  3. deterministic title ordering (case-insensitive, then id) as the final
     tie-breaker only

Undated / unknown releases sink to the bottom (``NULLS LAST``). This is the
single source of truth for release ordering on the backend so that malformed
upstream (Deezer) ordering can never leak into the API response — the Flutter
client applies an equivalent safety sort of its own.
"""

from __future__ import annotations

from typing import List, Tuple

from schemas.catalog.artist import ReleaseSummary


def _effective_ymd(r: ReleaseSummary) -> Tuple[int, int, int]:
    """Best-effort (year, month, day) from the exact date, else the year.

    Returns ``(0, 0, 0)`` when nothing usable exists so the release sorts last.
    """
    s = r.release_date
    if s and len(s) >= 4 and s[:4].isdigit():
        year = int(s[:4])
        month = int(s[5:7]) if len(s) >= 7 and s[5:7].isdigit() else 0
        day = int(s[8:10]) if len(s) >= 10 and s[8:10].isdigit() else 0
        return (year, month, day)
    if r.release_year:
        return (int(r.release_year), 0, 0)
    return (0, 0, 0)


def _has_exact_date(r: ReleaseSummary) -> bool:
    """True only when release_date actually parses as a date (not garbage)."""
    s = r.release_date
    return bool(s and len(s) >= 4 and s[:4].isdigit())


def _recency_key(r: ReleaseSummary) -> Tuple[int, int, int, int]:
    year, month, day = _effective_ymd(r)
    # Within an identical (y, m, d), an exactly-dated release ranks above a
    # year-only one (exact=1 > 0 under the reverse=True recency sort). A
    # non-parseable release_date is NOT treated as exact.
    return (year, month, day, 1 if _has_exact_date(r) else 0)


def sort_releases(releases: List[ReleaseSummary]) -> List[ReleaseSummary]:
    """Sort in place and return, newest-first, per the Phase 3.3 §5 rules.

    Two stable passes: first the deterministic title/id tie-break (ascending),
    then recency descending. Python's sort is stable, so releases sharing a
    recency key retain the title order established by the first pass.
    """
    releases.sort(key=lambda r: ((r.title or "").casefold(), r.id or ""))
    releases.sort(key=_recency_key, reverse=True)
    return releases
