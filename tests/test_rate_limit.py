"""Rate limiter tests."""

import pytest

from services.rate_limit import RateLimiter
from tests.conftest import FakeRedis


async def test_allows_under_limit_then_blocks(fake_redis):
    rl = RateLimiter(fake_redis)
    results = [await rl.check("k", limit=3, window_seconds=60) for _ in range(4)]
    allowed = [r[0] for r in results]
    assert allowed == [True, True, True, False]     # 4th call over the limit


async def test_degrades_open_without_redis():
    rl = RateLimiter(None)
    allowed, remaining, reset = await rl.check("k", limit=1, window_seconds=60)
    assert allowed is True                           # never blocks on no-Redis


async def test_check_enforced_reports_enforcement(fake_redis):
    rl = RateLimiter(fake_redis)
    allowed, remaining, reset, enforced = await rl.check_enforced("k", limit=1, window_seconds=60)
    assert (allowed, enforced) == (True, True)
    allowed, _, _, enforced = await rl.check_enforced("k", limit=1, window_seconds=60)
    assert (allowed, enforced) == (False, True)     # over the limit, and it counted


async def test_check_enforced_flags_degrade_open_without_redis_or_on_error():
    allowed, _, _, enforced = await RateLimiter(None).check_enforced("k", limit=1, window_seconds=60)
    assert (allowed, enforced) == (True, False)

    class _Broken:
        async def incr(self, k):
            raise ConnectionError("redis down")

    allowed, _, _, enforced = await RateLimiter(_Broken()).check_enforced("k", limit=1, window_seconds=60)
    assert (allowed, enforced) == (True, False)
