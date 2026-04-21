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
        try:
            return p.read_bytes()
        except FileNotFoundError:
            return None

    def put(self, url: str, params: dict[str, str], value: bytes) -> None:
        """Atomic write via tmp-then-replace, with orphan cleanup on failure.

        Path.replace() is atomic on POSIX and within-drive atomic on Windows
        NTFS. This closes the truncate-then-partial-write window that would
        otherwise let a killed process leave a zero-byte or partial-payload
        cache file that get() cannot distinguish from a valid response.
        The try/except guarantees a failed write_bytes or replace unlinks
        the tmp file rather than leaving a slow disk-space leak.
        """
        p = self._path(self._key(url, params))
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_bytes(value)
            tmp.replace(p)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


class MalformedResponseError(Exception):
    """NCBI returned a non-2xx, empty, or otherwise unusable response.

    Fail closed: never treat an error page as valid data (portfolio rule).
    """


def fetch_raw(
    url: str,
    params: dict[str, str],
    *,
    session,
    limiter: RateLimiter,
    cache: RequestCache,
    timeout: float = 30.0,
) -> bytes:
    """Cache-first HTTP GET. Returns raw bytes.

    Raises MalformedResponseError on non-2xx, empty body, or transport error.

    Args:
        url: endpoint URL.
        params: query parameters.
        session: requests.Session or compatible (must expose .get).
        limiter: RateLimiter acquired once per live request (cache hits bypass).
        cache: RequestCache consulted before network.
        timeout: per-request timeout in seconds. Default 30.0 suits esearch;
            efetch callers fetching large PMID batches (Task 7) should pass
            a higher value (e.g., 120.0).
    """
    cached = cache.get(url, params)
    if cached is not None:
        return cached

    limiter.acquire()
    try:
        response = session.get(url, params=params, timeout=timeout)
    except Exception as exc:
        raise MalformedResponseError(f"transport error for {url}: {exc}") from exc

    if response.status_code != 200:
        raise MalformedResponseError(
            f"non-2xx response {response.status_code} for {url}"
        )
    body: bytes = response.content
    if not body:
        raise MalformedResponseError(f"empty body for {url}")

    cache.put(url, params, body)
    return body
