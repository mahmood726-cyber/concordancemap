"""Tests for pipeline.ncbi_client."""
from __future__ import annotations

import time

import pytest

from pipeline.ncbi_client import RateLimiter


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
