"""
Lightweight in-process background job runner.

LIMITATION: these jobs are in-process asyncio tasks. They are best-effort and do
NOT survive a process restart, and they run in the API worker (so heavy fan-out
should be kept small). This is deliberate for Phase 2 stale-while-revalidate
refresh + on-demand match persistence; a durable queue (worker/Redis stream) is
the documented next step for expensive fan-out. See docs/CACHE_STRATEGY.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Set

logger = logging.getLogger("paax.jobs")


class BackgroundJobs:
    def __init__(self):
        self._tasks: Set["asyncio.Task"] = set()

    def schedule(self, coro_factory: Callable[[], Awaitable], *, label: str = "job") -> None:
        """Fire-and-track a background coroutine. Exceptions are logged, not raised."""
        try:
            task = asyncio.ensure_future(self._wrap(coro_factory, label))
        except RuntimeError:
            # No running loop (e.g. called outside async context) — skip silently.
            logger.debug("[jobs] no running loop; skipped %s", label)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _wrap(self, coro_factory: Callable[[], Awaitable], label: str) -> None:
        try:
            await coro_factory()
        except Exception as exc:  # noqa: BLE001 — background jobs never crash the app
            logger.warning("[jobs] %s failed: %s", label, exc)

    @property
    def pending(self) -> int:
        return len(self._tasks)
