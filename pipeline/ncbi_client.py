"""Cache-first, rate-limited, resumable NCBI E-utilities wrapper.

Public API (added incrementally across plan tasks):
    RateLimiter          (Task 2 — this task)
    RequestCache         (Task 3)
    fetch_raw            (Task 4)
    fetch_with_retries   (Task 5)
    esearch_pubmed       (Task 6)
    efetch_pubmed + SRRecord (Task 7)

Separate modules (created in later tasks, NOT in ncbi_client.py):
    pipeline.checkpoint  (Task 8) — save_checkpoint / load_checkpoint
    pipeline.stuck_log   (Task 9) — log_stuck
    pipeline.truthcert   (Tasks 10-11) — HMAC provenance chain
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RateLimiter:
    """Token-bucket rate limiter.

    Not thread-safe. Intended for single-threaded use. For concurrent use,
    wrap acquire() in a threading.Lock at the call site.

    Args:
        rate_per_sec: tokens added per second (10 with API key, 3 unauth).
            Must be > 0.
        burst: maximum bucket capacity. Must be >= 1.
    """

    rate_per_sec: float
    burst: int

    def __post_init__(self) -> None:
        if self.rate_per_sec <= 0:
            raise ValueError(
                f"rate_per_sec must be positive, got {self.rate_per_sec}"
            )
        if self.burst < 1:
            raise ValueError(f"burst must be >= 1, got {self.burst}")
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


class RequestCache:
    """SHA-256-keyed disk cache for HTTP responses.

    Key = SHA-256 of f"{url}|{json.dumps(sorted params)}". Value stored as
    raw bytes in a file named <key>.bin under cache_dir.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str, params: dict[str, str]) -> str:
        normalized = json.dumps(sorted(params.items()), separators=(",", ":"))
        raw = f"{url}|{normalized}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.bin"

    def get(self, url: str, params: dict[str, str]) -> bytes | None:
        p = self._path(self._key(url, params))
        if not p.exists():
            return None
        return p.read_bytes()

    def put(self, url: str, params: dict[str, str], value: bytes) -> None:
        p = self._path(self._key(url, params))
        p.write_bytes(value)
