"""Distributed lock + response cache unit tests."""

import pytest

from cache.cache_keys import CacheKeys
from cache.distributed_lock import DistributedLock, make_lock
from cache.response_cache import CacheStatus, ResponseCache


# ── keys ────────────────────────────────────────────────────────────────────

def test_keys_are_namespaced_and_centralized():
    assert CacheKeys.artist_by_deezer(111) == "catalog:artist:deezer:111"
    assert CacheKeys.search("Tracks", "Young Miko", 20, 0) == "search:tracks:young_miko:20:0"
    assert CacheKeys.lock_ingest("album", 333) == "lock:ingest:album:333"
    assert CacheKeys.negative("track", 9) == "negative:deezer:track:9"


# ── distributed lock ─────────────────────────────────────────────────────────

async def test_lock_mutual_exclusion(fake_redis):
    a = make_lock(fake_redis, "lock:x", 30)
    b = make_lock(fake_redis, "lock:x", 30)
    assert await a.acquire() is True
    assert a.owned is True
    assert await b.acquire() is False       # held by a
    await a.release()
    assert await b.acquire() is True         # now free
    await b.release()


async def test_lock_release_is_ownership_safe(fake_redis):
    a = make_lock(fake_redis, "lock:y", 30)
    await a.acquire()
    # Simulate a's TTL expiring and b grabbing the lock.
    fake_redis.store["lock:y"] = "someone-elses-token"
    await a.release()                        # must NOT delete b's lock
    assert fake_redis.store.get("lock:y") == "someone-elses-token"


async def test_lock_degrades_open_without_redis():
    lock = make_lock(None, "lock:z", 30)
    assert await lock.acquire() is True      # proceed without mutual exclusion
    assert lock.owned is False
    await lock.release()


async def test_lock_bounded_wait_returns_false(fake_redis):
    a = make_lock(fake_redis, "lock:w", 30)
    await a.acquire()
    b = DistributedLock(fake_redis, "lock:w", 30, wait_seconds=0.15, poll_interval=0.05)
    assert await b.acquire() is False        # gives up after the bounded wait


# ── response cache ───────────────────────────────────────────────────────────

async def test_response_cache_envelope_roundtrip(fake_redis):
    rc = ResponseCache(fake_redis)
    assert await rc.get("k") is None
    await rc.set("k", {"hello": "world"}, ttl=60, status=CacheStatus.HIT)
    env = await rc.get("k")
    assert env["status"] == CacheStatus.HIT
    assert env["data"] == {"hello": "world"}


async def test_response_cache_negative_and_invalidate(fake_redis):
    rc = ResponseCache(fake_redis)
    await rc.set_negative("negative:deezer:track:9", ttl=60, reason="not_found")
    assert (await rc.get_negative("negative:deezer:track:9"))["reason"] == "not_found"

    await rc.set("k", {"a": 1}, ttl=60)
    await rc.invalidate("k")
    assert await rc.get("k") is None
