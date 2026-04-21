"""Cache-first, rate-limited, resumable NCBI E-utilities wrapper.

Public API (added incrementally across plan tasks):
    RateLimiter      (Task 2 — this task)
    RequestCache     (Task 3)
    fetch_raw        (Task 4)
    with retries     (Task 5)
    esearch_pubmed   (Task 6)
    efetch_pubmed    (Task 7)
    save_checkpoint  (Task 8)
    log_stuck        (Task 9)
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class RateLimiter:
    """Token-bucket rate limiter.

    Args:
        rate_per_sec: tokens added per second (10 with API key, 3 unauth).
        burst: maximum bucket capacity.
    """

    rate_per_sec: float
    burst: int

    def __post_init__(self) -> None:
        self._tokens: float = float(self.burst)
        self._last_refill: float = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                float(self.burst),
                self._tokens + elapsed * self.rate_per_sec,
            )
            self._last_refill = now

    def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return
        deficit = 1.0 - self._tokens
        sleep_for = deficit / self.rate_per_sec
        time.sleep(sleep_for)
        self._refill()
        self._tokens -= 1.0
