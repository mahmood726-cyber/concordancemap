"""Tests for pipeline.ncbi_client."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from pipeline.ncbi_client import RateLimiter, RequestCache


def test_rate_limiter_allows_burst_up_to_capacity(monkeypatch):
    """A fresh bucket at 10 tokens allows 10 rapid acquires without blocking."""
    fake_time = [0.0]
    monkeypatch.setattr("pipeline.ncbi_client.time.monotonic", lambda: fake_time[0])
    sleeps: list[float] = []
    monkeypatch.setattr("pipeline.ncbi_client.time.sleep", lambda s: sleeps.append(s))

    rl = RateLimiter(rate_per_sec=10.0, burst=10)
    for _ in range(10):
        rl.acquire()

    assert sleeps == [], "burst within capacity should not sleep"


def test_rate_limiter_blocks_beyond_capacity(monkeypatch):
    """The 11th acquire at rate=10/s must sleep ~0.1s to refill one token."""
    fake_time = [0.0]
    monkeypatch.setattr("pipeline.ncbi_client.time.monotonic", lambda: fake_time[0])
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        fake_time[0] += s

    monkeypatch.setattr("pipeline.ncbi_client.time.sleep", fake_sleep)

    rl = RateLimiter(rate_per_sec=10.0, burst=10)
    for _ in range(11):
        rl.acquire()

    assert len(sleeps) == 1
    assert 0.09 <= sleeps[0] <= 0.11, f"expected ~0.1s sleep, got {sleeps[0]}"


def test_rate_limiter_unauth_mode_rate(monkeypatch):
    """Unauth rate is 3/s; with burst=3 a 4th call blocks ~0.33s."""
    fake_time = [0.0]
    monkeypatch.setattr("pipeline.ncbi_client.time.monotonic", lambda: fake_time[0])
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        fake_time[0] += s

    monkeypatch.setattr("pipeline.ncbi_client.time.sleep", fake_sleep)

    rl = RateLimiter(rate_per_sec=3.0, burst=3)
    for _ in range(4):
        rl.acquire()

    assert len(sleeps) == 1
    assert 0.30 <= sleeps[0] <= 0.37, f"expected ~0.333s sleep, got {sleeps[0]}"


def test_rate_limiter_rejects_bad_rate():
    """rate_per_sec <= 0 must fail at construction (fail closed, not later)."""
    with pytest.raises(ValueError, match="rate_per_sec"):
        RateLimiter(rate_per_sec=0.0, burst=10)
    with pytest.raises(ValueError, match="rate_per_sec"):
        RateLimiter(rate_per_sec=-1.0, burst=10)


def test_rate_limiter_rejects_bad_burst():
    """burst < 1 must fail at construction."""
    with pytest.raises(ValueError, match="burst"):
        RateLimiter(rate_per_sec=10.0, burst=0)
    with pytest.raises(ValueError, match="burst"):
        RateLimiter(rate_per_sec=10.0, burst=-5)


def test_cache_miss_returns_none(tmp_path: Path):
    cache = RequestCache(cache_dir=tmp_path)
    assert cache.get("http://example.com/x", {"p": "1"}) is None


def test_cache_put_then_get_roundtrip(tmp_path: Path):
    cache = RequestCache(cache_dir=tmp_path)
    payload = b"<xml><PMID>12345</PMID></xml>"
    cache.put("http://example.com/x", {"p": "1"}, payload)
    assert cache.get("http://example.com/x", {"p": "1"}) == payload


def test_cache_key_is_sorted_params(tmp_path: Path):
    """Params in different order must hit the same cache entry."""
    cache = RequestCache(cache_dir=tmp_path)
    cache.put("http://example.com/x", {"a": "1", "b": "2"}, b"X")
    assert cache.get("http://example.com/x", {"b": "2", "a": "1"}) == b"X"


def test_cache_is_disk_backed(tmp_path: Path):
    """A second RequestCache instance pointing at the same dir sees prior writes."""
    cache1 = RequestCache(cache_dir=tmp_path)
    cache1.put("http://example.com/x", {}, b"persisted")
    cache2 = RequestCache(cache_dir=tmp_path)
    assert cache2.get("http://example.com/x", {}) == b"persisted"


def test_cache_different_urls_do_not_collide(tmp_path: Path):
    """Same params, different URLs must produce distinct cache keys."""
    cache = RequestCache(cache_dir=tmp_path)
    cache.put("http://x.com", {}, b"A")
    assert cache.get("http://y.com", {}) is None
    assert cache.get("http://x.com", {}) == b"A"


def test_cache_overwrite_replaces_value(tmp_path: Path):
    """A second put() to the same key replaces the first value atomically."""
    cache = RequestCache(cache_dir=tmp_path)
    cache.put("http://e.com/x", {}, b"v1")
    cache.put("http://e.com/x", {}, b"v2")
    assert cache.get("http://e.com/x", {}) == b"v2"
