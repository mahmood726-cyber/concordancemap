# Plan 1: Infrastructure + NCBI Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cache-first, rate-limited, resumable NCBI E-utilities wrapper + TruthCert provenance module that ConcordanceMap (and downstream Papers A and C) will consume. Ships as a standalone, tested artifact.

**Architecture:** Token-bucket rate limiter fronts a SHA-256-keyed disk cache fronting the raw HTTP layer. Retry-with-exponential-backoff wraps the HTTP layer. Two public APIs on top: `esearch_pubmed(query)` and `efetch_pubmed(pmids)`. TruthCert computes a SHA-256+HMAC provenance chain using a key read from env var only (never from the bundle itself). Resumability is per-pair checkpoint JSON on disk.

**Tech Stack:** Python 3.11+ (3.13 is permitted but requires the scipy WMI-deadlock monkey-patch at the scipy import site — runtime gotcha, not a version bar), requests, lxml, pytest, pytest-vcr (HTTP cassettes for CI).

**Acceptance test at end of plan:** `python -m pytest -q` shows all tests passing, and a smoke script `scripts/smoke_ncbi.py` runs `esearch_pubmed("empagliflozin AND heart failure")` and `efetch_pubmed([first_3_pmids])` against cached cassettes in <10 seconds with zero live HTTP calls.

**Spec reference:** `docs/superpowers/specs/2026-04-21-concordancemap-design.md` Sections 4, 5.2, 5.8, 11, 12.1, 12.2.

---

## Task 1: Project scaffolding + sanity test

**Files:**
- Create: `pyproject.toml`
- Create: `pytest.ini`
- Create: `pipeline/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_sanity.py`

- [ ] **Step 1: Write the failing sanity test**

Create `tests/test_sanity.py`:

```python
"""Sanity check: the project package is importable."""


def test_pipeline_package_importable():
    import pipeline
    assert pipeline is not None


def test_python_version_ok():
    import sys
    assert sys.version_info >= (3, 11), "Python 3.11+ required per portfolio rule"
    # Note: Python 3.13 is permitted but requires the scipy WMI-deadlock monkey-patch
    # (see C:\Users\user\.claude\rules\lessons.md). That workaround lives in the
    # orchestrator, not here — the runtime guard belongs with scipy import, not with
    # the version check.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sanity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Create the package scaffolding**

Create `pipeline/__init__.py`:

```python
"""ConcordanceMap pipeline package.

Modules:
    ncbi_client      — cache-first NCBI E-utilities wrapper
    truthcert        — SHA-256+HMAC provenance chain
    (others added in later plans)
"""

__version__ = "0.1.0"
```

Create `tests/__init__.py` (empty file):

```python
```

Create `pyproject.toml`:

```toml
[project]
name = "concordancemap"
version = "0.1.0"
description = "Cross-SR concordance audit on PICO-matched drug-indication clusters"
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31",
    "lxml>=5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-vcr>=1.0.2",
    "vcrpy>=6.0",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["pipeline*"]
exclude = ["tests*", "data*", "dashboard*", "paper*"]
```

Create `pytest.ini`:

```ini
[pytest]
minversion = 8.0
testpaths = tests
python_files = test_*.py
addopts = -ra --strict-markers --tb=short
markers =
    integration: end-to-end pipeline tests (may be slower)
    vcr: tests using pytest-vcr cassettes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sanity.py -v`
Expected: `2 passed in <1s`

- [ ] **Step 5: Commit**

```bash
cd /c/Models/ConcordanceMap
git add pyproject.toml pytest.ini pipeline/__init__.py tests/__init__.py tests/test_sanity.py
git commit -m "$(cat <<'EOF'
scaffold: ConcordanceMap project with pytest harness

Python 3.11+ supported (3.13 permitted; WMI deadlock workaround applied at
the scipy import site, not as a version bar). pytest + pytest-vcr + vcrpy
for HTTP cassettes. Sanity test proves the pipeline package imports cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: RateLimiter (token-bucket)

**Files:**
- Create: `pipeline/ncbi_client.py`
- Create: `tests/test_ncbi_client.py`

- [ ] **Step 1: Write the failing rate-limiter tests**

Create `tests/test_ncbi_client.py`:

```python
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
    import pytest as _pytest
    with _pytest.raises(ValueError, match="rate_per_sec"):
        RateLimiter(rate_per_sec=0.0, burst=10)
    with _pytest.raises(ValueError, match="rate_per_sec"):
        RateLimiter(rate_per_sec=-1.0, burst=10)


def test_rate_limiter_rejects_bad_burst():
    """burst < 1 must fail at construction."""
    import pytest as _pytest
    with _pytest.raises(ValueError, match="burst"):
        RateLimiter(rate_per_sec=10.0, burst=0)
    with _pytest.raises(ValueError, match="burst"):
        RateLimiter(rate_per_sec=10.0, burst=-5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.ncbi_client'`

- [ ] **Step 3: Implement RateLimiter**

Create `pipeline/ncbi_client.py`:

```python
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

import time
from dataclasses import dataclass


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: `3 passed in <1s`

- [ ] **Step 5: Commit**

```bash
git add pipeline/ncbi_client.py tests/test_ncbi_client.py
git commit -m "$(cat <<'EOF'
feat(ncbi): token-bucket rate limiter with API-key vs unauth modes

10 req/s with NCBI_API_KEY, 3 req/s unauth per NCBI E-utilities policy.
Fake-time tests verify burst-within-capacity is non-blocking and 11th
request at capacity sleeps ~0.1s.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: RequestCache (SHA-256-keyed disk cache)

**Files:**
- Modify: `pipeline/ncbi_client.py` (add `RequestCache` class)
- Modify: `tests/test_ncbi_client.py` (add cache tests)

- [ ] **Step 1: Write the failing cache tests**

Append to `tests/test_ncbi_client.py`:

```python
from pathlib import Path

from pipeline.ncbi_client import RequestCache


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


def test_cache_put_failure_after_write_unlinks_tmp(tmp_path: Path, monkeypatch):
    """If replace() raises AFTER write_bytes created the .tmp file, the guard
    must actually unlink — not just run a no-op on a never-existed file."""
    cache = RequestCache(cache_dir=tmp_path)

    def fake_replace(self, target):
        # .tmp exists at this point — write_bytes succeeded.
        raise OSError("simulated rename failure")

    monkeypatch.setattr(Path, "replace", fake_replace)

    with pytest.raises(OSError, match="rename failure"):
        cache.put("http://e.com/x", {}, b"payload")

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"orphan tmp files remain: {tmp_files}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'RequestCache' from 'pipeline.ncbi_client'`

- [ ] **Step 3: Implement RequestCache**

Append to `pipeline/ncbi_client.py`:

```python
import hashlib
import json
from pathlib import Path


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/ncbi_client.py tests/test_ncbi_client.py
git commit -m "$(cat <<'EOF'
feat(ncbi): SHA-256-keyed disk cache for E-utilities responses

Params sorted before hashing so request argument order does not split
the cache. Cache is disk-backed so warm-cache test runs need zero live
HTTP calls.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Low-level HTTP fetch with UTF-8 normalization and fail-closed

**Files:**
- Modify: `pipeline/ncbi_client.py` (add `fetch_raw`, `MalformedResponseError`)
- Modify: `tests/test_ncbi_client.py` (add fetch_raw tests)

- [ ] **Step 1: Write the failing fetch_raw tests**

Append to `tests/test_ncbi_client.py`:

```python
import pytest

from pipeline.ncbi_client import MalformedResponseError, RateLimiter, RequestCache, fetch_raw


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes, text: str | None = None):
        self.status_code = status_code
        self.content = content
        self.text = text if text is not None else content.decode("utf-8", "replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict, timeout: float):
        self.calls.append((url, dict(params)))
        return self._response


def test_fetch_raw_cache_hit_skips_http(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    cache.put("http://e.com/x", {"a": "1"}, b"cached")
    sess = _FakeSession(_FakeResponse(200, b"fresh"))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    out = fetch_raw("http://e.com/x", {"a": "1"}, session=sess, limiter=limiter, cache=cache)

    assert out == b"cached"
    assert sess.calls == [], "cache hit must not hit the network"


def test_fetch_raw_cache_miss_hits_http_and_stores(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FakeSession(_FakeResponse(200, b"<xml>ok</xml>"))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    out = fetch_raw("http://e.com/x", {"a": "1"}, session=sess, limiter=limiter, cache=cache)

    assert out == b"<xml>ok</xml>"
    assert len(sess.calls) == 1
    assert cache.get("http://e.com/x", {"a": "1"}) == b"<xml>ok</xml>"


def test_fetch_raw_fail_closed_on_non_2xx(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FakeSession(_FakeResponse(500, b"<html>500</html>"))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    with pytest.raises(MalformedResponseError):
        fetch_raw("http://e.com/x", {}, session=sess, limiter=limiter, cache=cache)

    assert cache.get("http://e.com/x", {}) is None, "failed response must not be cached"


def test_fetch_raw_fail_closed_on_empty_body(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FakeSession(_FakeResponse(200, b""))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    with pytest.raises(MalformedResponseError):
        fetch_raw("http://e.com/x", {}, session=sess, limiter=limiter, cache=cache)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'MalformedResponseError' ...`

- [ ] **Step 3: Implement fetch_raw**

Append to `pipeline/ncbi_client.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/ncbi_client.py tests/test_ncbi_client.py
git commit -m "$(cat <<'EOF'
feat(ncbi): cache-first HTTP fetcher with fail-closed policy

Never caches or returns non-2xx or empty payloads — portfolio rule forbids
treating an error page as valid data. Rate limiter acquired before every
live request; cache hits short-circuit before the limiter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Retry with exponential backoff on 429/503

**Files:**
- Modify: `pipeline/ncbi_client.py` (add `fetch_with_retries`, `NCBIRateLimitError`)
- Modify: `tests/test_ncbi_client.py` (add retry tests)

- [ ] **Step 1: Write the failing retry tests**

Append to `tests/test_ncbi_client.py`:

```python
from pipeline.ncbi_client import NCBIRateLimitError, fetch_with_retries


class _FlakySession:
    """Returns a sequence of fake responses, one per .get() call."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict, timeout: float):
        self.calls.append((url, dict(params)))
        return self._responses.pop(0)


def test_fetch_with_retries_succeeds_after_transient_429(tmp_path, monkeypatch):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FlakySession([
        _FakeResponse(429, b"slow down"),
        _FakeResponse(200, b"<ok/>"),
    ])
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)
    sleeps: list[float] = []
    monkeypatch.setattr("pipeline.ncbi_client.time.sleep", lambda s: sleeps.append(s))

    out = fetch_with_retries(
        "http://e.com/x", {}, session=sess, limiter=limiter, cache=cache, max_retries=5
    )

    assert out == b"<ok/>"
    assert len(sess.calls) == 2
    assert sleeps == [2.0], f"expected one 2s backoff, got {sleeps}"


def test_fetch_with_retries_backoff_sequence(tmp_path, monkeypatch):
    """Five 429 responses then success -> backoffs 2s, 4s, 8s, 16s."""
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FlakySession([
        _FakeResponse(429, b"x"),
        _FakeResponse(503, b"x"),
        _FakeResponse(429, b"x"),
        _FakeResponse(503, b"x"),
        _FakeResponse(200, b"<ok/>"),
    ])
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)
    sleeps: list[float] = []
    monkeypatch.setattr("pipeline.ncbi_client.time.sleep", lambda s: sleeps.append(s))

    out = fetch_with_retries(
        "http://e.com/x", {}, session=sess, limiter=limiter, cache=cache, max_retries=5
    )

    assert out == b"<ok/>"
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


def test_fetch_with_retries_raises_after_max(tmp_path, monkeypatch):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FlakySession([_FakeResponse(429, b"x") for _ in range(5)])
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)
    monkeypatch.setattr("pipeline.ncbi_client.time.sleep", lambda s: None)

    with pytest.raises(NCBIRateLimitError):
        fetch_with_retries(
            "http://e.com/x", {}, session=sess, limiter=limiter, cache=cache, max_retries=5
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'NCBIRateLimitError' ...`

- [ ] **Step 3: Implement fetch_with_retries**

Append to `pipeline/ncbi_client.py`:

```python
class NCBIRateLimitError(Exception):
    """Exceeded max retries on NCBI rate-limit (429) or service-unavailable (503)."""


_RETRYABLE_STATUSES = {429, 503}


def fetch_with_retries(
    url: str,
    params: dict[str, str],
    *,
    session,
    limiter: RateLimiter,
    cache: RequestCache,
    max_retries: int = 5,
    timeout: float = 30.0,
) -> bytes:
    """Call fetch_raw with exponential backoff on 429/503.

    Backoff schedule: 2s, 4s, 8s, 16s, 32s (then raise).
    """
    cached = cache.get(url, params)
    if cached is not None:
        return cached

    backoff = 2.0
    for attempt in range(max_retries):
        limiter.acquire()
        try:
            response = session.get(url, params=params, timeout=timeout)
        except Exception as exc:
            raise MalformedResponseError(f"transport error for {url}: {exc}") from exc

        if response.status_code == 200 and response.content:
            cache.put(url, params, response.content)
            return response.content

        if response.status_code in _RETRYABLE_STATUSES and attempt < max_retries - 1:
            time.sleep(backoff)
            backoff *= 2
            continue

        if response.status_code in _RETRYABLE_STATUSES:
            raise NCBIRateLimitError(
                f"max retries ({max_retries}) exceeded for {url} "
                f"(final status {response.status_code})"
            )
        raise MalformedResponseError(
            f"non-2xx response {response.status_code} for {url}"
        )

    raise NCBIRateLimitError(f"max retries ({max_retries}) exceeded for {url}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/ncbi_client.py tests/test_ncbi_client.py
git commit -m "$(cat <<'EOF'
feat(ncbi): exponential-backoff retry on 429/503

Backoff schedule 2/4/8/16/32s, max 5 attempts. 429 or 503 from NCBI
triggers retry; any other non-2xx fails closed immediately. Cache hit
still short-circuits all retry logic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: esearch_pubmed public API

**Files:**
- Modify: `pipeline/ncbi_client.py` (add `esearch_pubmed`, `NCBI_ESEARCH_URL`)
- Create: `tests/cassettes/esearch_empagliflozin_hf.yaml` (VCR cassette, see Step 3)
- Modify: `tests/test_ncbi_client.py` (add esearch test)

- [ ] **Step 1: Write the failing esearch test**

Append to `tests/test_ncbi_client.py`:

```python
from pipeline.ncbi_client import esearch_pubmed


def test_esearch_pubmed_parses_pmids_from_xml(tmp_path, monkeypatch):
    """Given a recorded XML response, esearch_pubmed returns the PMID list."""
    cache = RequestCache(cache_dir=tmp_path)
    fake_xml = b"""<?xml version="1.0"?>
<eSearchResult>
    <Count>3</Count>
    <IdList>
        <Id>40000001</Id>
        <Id>40000002</Id>
        <Id>40000003</Id>
    </IdList>
</eSearchResult>"""
    sess = _FakeSession(_FakeResponse(200, fake_xml))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    pmids = esearch_pubmed(
        query="empagliflozin AND heart failure",
        cache=cache,
        session=sess,
        limiter=limiter,
        api_key=None,
    )

    assert pmids == ["40000001", "40000002", "40000003"]
    assert len(sess.calls) == 1
    _, params = sess.calls[0]
    assert params["db"] == "pubmed"
    assert params["term"] == "empagliflozin AND heart failure"
    assert params["retmode"] == "xml"


def test_esearch_pubmed_empty_result_returns_empty_list(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    empty_xml = b"""<?xml version="1.0"?>
<eSearchResult><Count>0</Count><IdList/></eSearchResult>"""
    sess = _FakeSession(_FakeResponse(200, empty_xml))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    pmids = esearch_pubmed(
        query="nonsense", cache=cache, session=sess, limiter=limiter, api_key=None,
    )

    assert pmids == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'esearch_pubmed' ...`

- [ ] **Step 3: Implement esearch_pubmed**

Append to `pipeline/ncbi_client.py`:

```python
from lxml import etree

NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def esearch_pubmed(
    query: str,
    *,
    cache: RequestCache,
    session,
    limiter: RateLimiter,
    api_key: str | None,
    retmax: int = 10000,
) -> list[str]:
    """Return PMIDs matching the PubMed query.

    Retmax default 10000 is PubMed's per-request cap; callers doing larger
    searches should paginate via retstart (out of scope for this plan).
    """
    params: dict[str, str] = {
        "db": "pubmed",
        "term": query,
        "retmode": "xml",
        "retmax": str(retmax),
    }
    if api_key:
        params["api_key"] = api_key

    body = fetch_with_retries(
        NCBI_ESEARCH_URL, params, session=session, limiter=limiter, cache=cache
    )
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError as exc:
        raise MalformedResponseError(f"esearch XML parse failed: {exc}") from exc

    return [id_elem.text or "" for id_elem in root.iter("Id")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/ncbi_client.py tests/test_ncbi_client.py
git commit -m "$(cat <<'EOF'
feat(ncbi): esearch_pubmed API for PMID discovery

Wraps the E-utilities esearch endpoint. Returns PMID list; empty query
result returns empty list (not None). retmax defaults to PubMed's 10000
per-request cap. API-key parameter is optional per NCBI policy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: efetch_pubmed + SRRecord parsing

**Files:**
- Modify: `pipeline/ncbi_client.py` (add `SRRecord` dataclass, `efetch_pubmed`)
- Modify: `tests/test_ncbi_client.py` (add efetch tests)

- [ ] **Step 1: Write the failing efetch tests**

Append to `tests/test_ncbi_client.py`:

```python
from pipeline.ncbi_client import SRRecord, efetch_pubmed


_PUBMED_SAMPLE_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000001</PMID>
    <Article>
      <ArticleTitle>Empagliflozin in Heart Failure: A Systematic Review</ArticleTitle>
      <Abstract>
        <AbstractText>Empagliflozin reduced hospitalization for heart failure
        (HR 0.71, 95% CI 0.60-0.83) across 5 trials (n=15,000).</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>Smith</LastName><ForeName>A</ForeName></Author>
        <Author><LastName>Jones</LastName><ForeName>B</ForeName></Author>
      </AuthorList>
      <Journal><Title>J Cardiol</Title></Journal>
      <ELocationID EIdType="doi">10.1000/test.1</ELocationID>
      <PublicationTypeList>
        <PublicationType>Systematic Review</PublicationType>
        <PublicationType>Meta-Analysis</PublicationType>
      </PublicationTypeList>
    </Article>
    <DateCompleted><Year>2024</Year><Month>06</Month><Day>15</Day></DateCompleted>
    <MeshHeadingList>
      <MeshHeading><DescriptorName>Empagliflozin</DescriptorName></MeshHeading>
      <MeshHeading><DescriptorName>Heart Failure</DescriptorName></MeshHeading>
    </MeshHeadingList>
  </MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>"""


def test_efetch_pubmed_parses_one_record(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FakeSession(_FakeResponse(200, _PUBMED_SAMPLE_XML))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    records = efetch_pubmed(
        pmids=["40000001"],
        cache=cache,
        session=sess,
        limiter=limiter,
        api_key=None,
    )

    assert len(records) == 1
    r = records[0]
    assert isinstance(r, SRRecord)
    assert r.pmid == "40000001"
    assert "Empagliflozin in Heart Failure" in r.title
    assert "HR 0.71" in r.abstract
    assert "Empagliflozin" in r.mesh_descriptors
    assert "Heart Failure" in r.mesh_descriptors
    assert r.publication_year == 2024
    assert "Systematic Review" in r.publication_type
    assert "Meta-Analysis" in r.publication_type
    assert r.authors == ["Smith A", "Jones B"]
    assert r.journal == "J Cardiol"
    assert r.doi == "10.1000/test.1"


def test_efetch_pubmed_empty_pmids_returns_empty(tmp_path):
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FakeSession(_FakeResponse(200, b"<PubmedArticleSet/>"))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    records = efetch_pubmed(
        pmids=[], cache=cache, session=sess, limiter=limiter, api_key=None,
    )

    assert records == []
    assert sess.calls == [], "empty pmids must not hit the network"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'SRRecord' ...`

- [ ] **Step 3: Implement SRRecord + efetch_pubmed**

Append to `pipeline/ncbi_client.py`:

```python
from datetime import datetime, timezone


@dataclass
class SRRecord:
    """A PubMed-indexed systematic review record (abstract level).

    Mirrors the spec's Section 5.3 SRRecord dataclass.
    """

    pmid: str
    title: str
    abstract: str
    mesh_descriptors: list[str]
    publication_year: int
    publication_type: list[str]
    authors: list[str]
    journal: str
    doi: str | None
    fetched_at: str  # ISO8601 UTC


def _parse_pubmed_article(article_el) -> SRRecord:
    def _text(xpath: str) -> str:
        node = article_el.find(xpath)
        return (node.text or "").strip() if node is not None else ""

    def _all_text(xpath: str) -> list[str]:
        return [(n.text or "").strip() for n in article_el.iterfind(xpath)]

    pmid = _text(".//MedlineCitation/PMID")
    title = _text(".//Article/ArticleTitle")

    abstract_nodes = article_el.iterfind(".//Article/Abstract/AbstractText")
    abstract = " ".join((n.text or "").strip() for n in abstract_nodes)

    mesh = _all_text(".//MeshHeadingList/MeshHeading/DescriptorName")
    pub_types = _all_text(".//Article/PublicationTypeList/PublicationType")

    authors: list[str] = []
    for author_el in article_el.iterfind(".//Article/AuthorList/Author"):
        last = (author_el.findtext("LastName") or "").strip()
        fore = (author_el.findtext("ForeName") or "").strip()
        if last or fore:
            authors.append(f"{last} {fore}".strip())

    journal = _text(".//Article/Journal/Title")
    doi_node = article_el.find(".//Article/ELocationID[@EIdType='doi']")
    doi = (doi_node.text or "").strip() if doi_node is not None else None

    year_text = _text(".//DateCompleted/Year") or _text(".//Article/Journal/JournalIssue/PubDate/Year")
    try:
        year = int(year_text)
    except (TypeError, ValueError):
        year = 0

    return SRRecord(
        pmid=pmid,
        title=title,
        abstract=abstract,
        mesh_descriptors=mesh,
        publication_year=year,
        publication_type=pub_types,
        authors=authors,
        journal=journal,
        doi=doi or None,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def efetch_pubmed(
    pmids: list[str],
    *,
    cache: RequestCache,
    session,
    limiter: RateLimiter,
    api_key: str | None,
) -> list[SRRecord]:
    """Fetch PubMed records for the given PMIDs and parse them to SRRecord list.

    Returns empty list for empty input without hitting the network.
    """
    if not pmids:
        return []
    params: dict[str, str] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    if api_key:
        params["api_key"] = api_key

    body = fetch_with_retries(
        NCBI_EFETCH_URL, params, session=session, limiter=limiter, cache=cache
    )
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError as exc:
        raise MalformedResponseError(f"efetch XML parse failed: {exc}") from exc

    return [_parse_pubmed_article(art) for art in root.iter("PubmedArticle")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ncbi_client.py -v`
Expected: `18 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/ncbi_client.py tests/test_ncbi_client.py
git commit -m "$(cat <<'EOF'
feat(ncbi): efetch_pubmed API with SRRecord dataclass

Parses PubMed XML into SRRecord (matches spec Section 5.3 schema).
Empty PMID list returns empty list without hitting the network. DOI
extracted from ELocationID[@EIdType='doi']; pub year falls back from
DateCompleted to PubDate/Year if missing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Resumable per-pair checkpoint system

**Files:**
- Create: `pipeline/checkpoint.py`
- Create: `tests/test_checkpoint.py`

- [ ] **Step 1: Write the failing checkpoint tests**

Create `tests/test_checkpoint.py`:

```python
"""Tests for pipeline.checkpoint — resumable per-pair stage checkpoints."""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.checkpoint import CheckpointError, load_checkpoint, save_checkpoint


def test_save_then_load_roundtrip(tmp_path: Path):
    save_checkpoint(tmp_path, "pair_001", "harvest", {"pmids": ["1", "2", "3"]})
    loaded = load_checkpoint(tmp_path, "pair_001", "harvest")
    assert loaded == {"pmids": ["1", "2", "3"]}


def test_missing_checkpoint_returns_none(tmp_path: Path):
    assert load_checkpoint(tmp_path, "pair_001", "harvest") is None


def test_checkpoint_is_stage_scoped(tmp_path: Path):
    save_checkpoint(tmp_path, "pair_001", "harvest", {"a": 1})
    save_checkpoint(tmp_path, "pair_001", "pico", {"b": 2})
    assert load_checkpoint(tmp_path, "pair_001", "harvest") == {"a": 1}
    assert load_checkpoint(tmp_path, "pair_001", "pico") == {"b": 2}


def test_corrupted_checkpoint_raises(tmp_path: Path):
    pair_dir = tmp_path / "pair_001"
    pair_dir.mkdir()
    (pair_dir / "harvest.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path, "pair_001", "harvest")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_checkpoint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.checkpoint'`

- [ ] **Step 3: Implement checkpoint module**

Create `pipeline/checkpoint.py`:

```python
"""Resumable per-pair stage checkpoints.

Each pair's progress is persisted as JSON under
    <root>/<pair_id>/<stage>.json
so an interrupted run can pick up from the last completed stage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CheckpointError(Exception):
    """Checkpoint file exists but is malformed."""


def _checkpoint_path(root: Path, pair_id: str, stage: str) -> Path:
    return Path(root) / pair_id / f"{stage}.json"


def save_checkpoint(root: Path, pair_id: str, stage: str, data: Any) -> None:
    """Atomically write data as JSON to <root>/<pair_id>/<stage>.json."""
    p = _checkpoint_path(root, pair_id, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_checkpoint(root: Path, pair_id: str, stage: str) -> Any | None:
    """Return the checkpoint dict, or None if absent. Raises CheckpointError on corruption."""
    p = _checkpoint_path(root, pair_id, stage)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckpointError(f"corrupted checkpoint at {p}: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_checkpoint.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/checkpoint.py tests/test_checkpoint.py
git commit -m "$(cat <<'EOF'
feat(checkpoint): per-pair stage checkpoints for resumability

Atomic write via .tmp + replace. Missing checkpoint returns None; corrupt
JSON raises CheckpointError (fail closed — never silently re-run on stale
or malformed state).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: STUCK_FAILURES JSONL logging harness

**Files:**
- Create: `pipeline/stuck_log.py`
- Create: `tests/test_stuck_log.py`

- [ ] **Step 1: Write the failing stuck-log tests**

Create `tests/test_stuck_log.py`:

```python
"""Tests for pipeline.stuck_log — JSONL logging for hard failures."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.stuck_log import log_stuck


def test_log_stuck_appends_jsonl(tmp_path: Path):
    log_path = tmp_path / "STUCK_FAILURES.jsonl"

    log_stuck(log_path, category="ncbi_rate_limit", target="PMID:12345",
              reason="429 after 5 retries")
    log_stuck(log_path, category="malformed_xml", target="PMID:67890",
              reason="empty body")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    row0 = json.loads(lines[0])
    assert row0["category"] == "ncbi_rate_limit"
    assert row0["target"] == "PMID:12345"
    assert row0["reason"] == "429 after 5 retries"
    assert "logged_at" in row0


def test_log_stuck_creates_parent_dir(tmp_path: Path):
    log_path = tmp_path / "nested" / "dirs" / "STUCK_FAILURES.jsonl"
    log_stuck(log_path, category="x", target="y", reason="z")
    assert log_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_stuck_log.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.stuck_log'`

- [ ] **Step 3: Implement stuck_log**

Create `pipeline/stuck_log.py`:

```python
"""JSONL log for hard failures (per portfolio Sentinel convention).

One JSON object per line; append-only; UTF-8 with ensure_ascii=False so
non-ASCII targets (e.g., journal names) remain readable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def log_stuck(log_path: Path, *, category: str, target: str, reason: str) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "target": target,
        "reason": reason,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_stuck_log.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/stuck_log.py tests/test_stuck_log.py
git commit -m "$(cat <<'EOF'
feat(stuck_log): JSONL append-only logger for hard failures

One JSON object per line; UTF-8 with ensure_ascii=False so non-ASCII
targets remain human-readable. Matches portfolio Sentinel convention
for STUCK_FAILURES.jsonl.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: TruthCert HMAC key from env var (fail-closed)

**Files:**
- Create: `pipeline/truthcert.py`
- Create: `tests/test_truthcert.py`

- [ ] **Step 1: Write the failing key-handling tests**

Create `tests/test_truthcert.py`:

```python
"""Tests for pipeline.truthcert — SHA-256 provenance chain."""
from __future__ import annotations

import pytest

from pipeline.truthcert import TruthCertError, get_hmac_key


def test_get_hmac_key_reads_env_var(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "my-secret-key-1234")
    key = get_hmac_key()
    assert key == b"my-secret-key-1234"


def test_get_hmac_key_missing_raises(monkeypatch):
    monkeypatch.delenv("CONCORDANCEMAP_HMAC_KEY", raising=False)
    with pytest.raises(TruthCertError, match="CONCORDANCEMAP_HMAC_KEY"):
        get_hmac_key()


def test_get_hmac_key_empty_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "")
    with pytest.raises(TruthCertError, match="empty"):
        get_hmac_key()


def test_get_hmac_key_whitespace_only_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "   \t\n ")
    with pytest.raises(TruthCertError, match="empty"):
        get_hmac_key()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_truthcert.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.truthcert'`

- [ ] **Step 3: Implement truthcert key handling**

Create `pipeline/truthcert.py`:

```python
"""SHA-256 + HMAC provenance chain for ConcordanceMap.

HMAC key is read from env var CONCORDANCEMAP_HMAC_KEY only. Never from
the bundle itself (2026-04-14 portfolio crypto lesson — using bundle-
derived data as the HMAC key makes forgery trivial).
"""
from __future__ import annotations

import os


class TruthCertError(Exception):
    """TruthCert configuration or verification failure."""


_HMAC_ENV_VAR = "CONCORDANCEMAP_HMAC_KEY"


def get_hmac_key() -> bytes:
    """Return HMAC key bytes from env. Fails closed if missing or empty.

    Never silently defaults — missing key must halt the pipeline.
    """
    value = os.environ.get(_HMAC_ENV_VAR)
    if value is None:
        raise TruthCertError(
            f"{_HMAC_ENV_VAR} environment variable is not set. "
            f"TruthCert cannot proceed without an explicit HMAC key."
        )
    if not value.strip():
        raise TruthCertError(
            f"{_HMAC_ENV_VAR} is empty or whitespace-only; set a real key."
        )
    return value.encode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_truthcert.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/truthcert.py tests/test_truthcert.py
git commit -m "$(cat <<'EOF'
feat(truthcert): HMAC key from env var, fail-closed

Key read exclusively from CONCORDANCEMAP_HMAC_KEY env var. Missing or
empty-whitespace values raise TruthCertError; no silent default.
Enforces the 2026-04-14 portfolio crypto lesson (bundle-derived keys
are forgery templates).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: TruthCert SHA-256 chain with constant-time verify

**Files:**
- Modify: `pipeline/truthcert.py` (add `compute_chain`, `verify_chain`)
- Modify: `tests/test_truthcert.py` (add chain tests)

- [ ] **Step 1: Write the failing chain tests**

Append to `tests/test_truthcert.py`:

```python
from pipeline.truthcert import compute_chain, verify_chain


def test_compute_chain_is_deterministic(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k")
    components = {"pmids": ["1", "2", "3"], "delta": 0.42}
    a = compute_chain(components)
    b = compute_chain(components)
    assert a == b
    assert isinstance(a, str)
    assert len(a) == 64  # SHA-256 hex


def test_compute_chain_differs_on_input_change(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k")
    a = compute_chain({"x": 1})
    b = compute_chain({"x": 2})
    assert a != b


def test_compute_chain_differs_on_key_change(monkeypatch):
    components = {"x": 1}
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "key-a")
    a = compute_chain(components)
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "key-b")
    b = compute_chain(components)
    assert a != b


def test_compute_chain_is_order_invariant(monkeypatch):
    """Dict key order must not affect the hash (JSON sorted)."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k")
    a = compute_chain({"x": 1, "y": 2})
    b = compute_chain({"y": 2, "x": 1})
    assert a == b


def test_verify_chain_passes_on_untampered(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k")
    components = {"pmids": ["1", "2"], "delta": 0.1}
    chain = compute_chain(components)
    assert verify_chain(components, chain) is True


def test_verify_chain_fails_on_tampered(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k")
    components = {"pmids": ["1", "2"], "delta": 0.1}
    chain = compute_chain(components)
    tampered = {"pmids": ["1", "2"], "delta": 0.9}
    assert verify_chain(tampered, chain) is False


def test_verify_chain_uses_constant_time_compare(monkeypatch):
    """Must call hmac.compare_digest, never ==."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k")
    calls: list[tuple] = []
    import hmac as _hmac

    real_compare = _hmac.compare_digest
    monkeypatch.setattr(
        "pipeline.truthcert.hmac.compare_digest",
        lambda a, b: (calls.append((a, b)), real_compare(a, b))[1],
    )
    components = {"x": 1}
    chain = compute_chain(components)
    verify_chain(components, chain)
    assert len(calls) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_truthcert.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_chain' ...`

- [ ] **Step 3: Implement compute_chain + verify_chain**

Append to `pipeline/truthcert.py`:

```python
import hashlib
import hmac
import json
from typing import Any


def _canonical_payload(components: dict[str, Any]) -> bytes:
    """Canonical JSON for hashing: sorted keys, compact separators, UTF-8."""
    return json.dumps(
        components, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_chain(components: dict[str, Any]) -> str:
    """Return the HMAC-SHA256 hex digest of the canonical components payload.

    The HMAC key is read from CONCORDANCEMAP_HMAC_KEY env var (via
    get_hmac_key), never from components itself.
    """
    key = get_hmac_key()
    payload = _canonical_payload(components)
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_chain(components: dict[str, Any], expected: str) -> bool:
    """Constant-time verify of an HMAC chain against recomputed value."""
    actual = compute_chain(components)
    return hmac.compare_digest(actual, expected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_truthcert.py -v`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/truthcert.py tests/test_truthcert.py
git commit -m "$(cat <<'EOF'
feat(truthcert): SHA-256+HMAC chain with constant-time verify

HMAC-SHA256 over canonical JSON (sort_keys, compact separators, UTF-8).
verify_chain always uses hmac.compare_digest — never ==, per portfolio
constant-time-compare rule. Tests cover determinism, order invariance,
input/key sensitivity, and constant-time enforcement.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: End-to-end integration test with VCR cassettes

**Files:**
- Create: `tests/conftest.py` (VCR configuration)
- Create: `tests/cassettes/integration_esearch_efetch.yaml` (recorded via first live run)
- Create: `tests/test_integration_ncbi.py`
- Create: `scripts/smoke_ncbi.py` (smoke test wrapper)

- [ ] **Step 1: Write the failing integration test**

Create `tests/conftest.py`:

```python
"""VCR configuration shared by integration tests."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def vcr_config() -> dict:
    return {
        "record_mode": "none",  # CI: cassette-only, never live
        "filter_query_parameters": ["api_key"],
        "cassette_library_dir": "tests/cassettes",
        "match_on": ["method", "scheme", "host", "path", "query"],
    }
```

Create `tests/test_integration_ncbi.py`:

```python
"""End-to-end NCBI client integration test with VCR cassettes.

Cassettes are committed under tests/cassettes/. CI uses record_mode=none
so zero live HTTP requests occur; cassette regeneration is a manual step
documented in tests/cassettes/README.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from pipeline.ncbi_client import (
    RateLimiter,
    RequestCache,
    SRRecord,
    efetch_pubmed,
    esearch_pubmed,
)


@pytest.mark.integration
@pytest.mark.vcr(cassette_library_dir="tests/cassettes")
def test_esearch_then_efetch_end_to_end(tmp_path: Path):
    """esearch returns PMIDs; efetch parses them into SRRecords."""
    cache = RequestCache(cache_dir=tmp_path / "cache")
    session = requests.Session()
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    pmids = esearch_pubmed(
        query="empagliflozin AND heart failure AND systematic[sb]",
        cache=cache,
        session=session,
        limiter=limiter,
        api_key=None,
        retmax=10,
    )
    assert len(pmids) > 0, "cassette should contain at least one PMID"

    records = efetch_pubmed(
        pmids=pmids[:3],
        cache=cache,
        session=session,
        limiter=limiter,
        api_key=None,
    )

    assert len(records) > 0
    for r in records:
        assert isinstance(r, SRRecord)
        assert r.pmid.isdigit()
        assert r.title
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_ncbi.py -v -m integration`
Expected: FAIL because the cassette `tests/cassettes/test_esearch_then_efetch_end_to_end.yaml` does not exist.

- [ ] **Step 3: Record the cassette (one-time, requires network) and make the integration stable**

Record the cassette by running the integration test once in record mode:

Create `tests/cassettes/README.md`:

```markdown
# VCR cassette regeneration

Cassettes under this directory are used by `tests/test_integration_ncbi.py`
under `record_mode=none` (cassette-only; zero live HTTP in CI).

## Regenerating a cassette (one-time, requires network)

1. Set `NCBI_API_KEY` env var (recommended; respects 10 req/s rate limit).
2. Temporarily change `vcr_config` in `tests/conftest.py` to
   `record_mode="once"`.
3. Delete the existing cassette file for the test you are regenerating.
4. Run: `python -m pytest tests/test_integration_ncbi.py -v -m integration`
5. The cassette is written back to `tests/cassettes/<test_name>.yaml`.
6. Revert `record_mode` to `"none"` in `tests/conftest.py` and commit the
   cassette + the reverted config together.

Do not commit cassettes with `api_key` values — `filter_query_parameters`
in `vcr_config` strips them, but verify with `grep api_key tests/cassettes/`
before committing.
```

Record the cassette once:

```bash
# One-time, on a machine with network + optional NCBI_API_KEY
sed -i 's/"record_mode": "none"/"record_mode": "once"/' tests/conftest.py
rm -f tests/cassettes/test_esearch_then_efetch_end_to_end.yaml
python -m pytest tests/test_integration_ncbi.py -v -m integration
sed -i 's/"record_mode": "once"/"record_mode": "none"/' tests/conftest.py
grep -r api_key tests/cassettes/ && echo "ERROR: api_key leaked to cassette" || echo "clean"
```

- [ ] **Step 4: Run test to verify it passes (cassette-only)**

Run: `python -m pytest tests/test_integration_ncbi.py -v -m integration`
Expected: `1 passed` — served entirely from the cassette, no live HTTP.

Also run the full suite:

Run: `python -m pytest -v`
Expected: All tests pass (`34 passed` or similar, depending on exact count).

- [ ] **Step 5: Add smoke script and commit**

Create `scripts/smoke_ncbi.py`:

```python
"""Smoke test: exercise the NCBI client end-to-end from a warm cache.

Run after cassette recording to confirm the wiring from script to pipeline
works. Intended for human use at development checkpoints; CI relies on
the pytest cassette path instead.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.ncbi_client import RateLimiter, RequestCache, efetch_pubmed, esearch_pubmed


def main() -> int:
    cache_dir = Path(__file__).resolve().parent.parent / "data" / "cache" / "smoke"
    cache = RequestCache(cache_dir=cache_dir)
    session = requests.Session()
    limiter = RateLimiter(
        rate_per_sec=10.0 if os.environ.get("NCBI_API_KEY") else 3.0,
        burst=10 if os.environ.get("NCBI_API_KEY") else 3,
    )

    t0 = time.time()
    pmids = esearch_pubmed(
        query="empagliflozin AND heart failure AND systematic[sb]",
        cache=cache,
        session=session,
        limiter=limiter,
        api_key=os.environ.get("NCBI_API_KEY"),
        retmax=10,
    )
    print(f"esearch returned {len(pmids)} PMIDs in {time.time()-t0:.2f}s")

    t1 = time.time()
    records = efetch_pubmed(
        pmids=pmids[:3],
        cache=cache,
        session=session,
        limiter=limiter,
        api_key=os.environ.get("NCBI_API_KEY"),
    )
    print(f"efetch returned {len(records)} SRRecord(s) in {time.time()-t1:.2f}s")

    for r in records:
        print(f"  PMID {r.pmid}: {r.title[:80]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Commit:

```bash
git add tests/conftest.py tests/test_integration_ncbi.py tests/cassettes/ scripts/smoke_ncbi.py
git commit -m "$(cat <<'EOF'
test(integration): end-to-end NCBI client with VCR cassettes

Cassettes pinned at record_mode=none so CI runs make zero live HTTP
calls. Regeneration workflow documented in tests/cassettes/README.md
and gated on a manual grep for leaked api_key. Smoke script exercises
the same path from the command line for dev-time sanity.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Plan 1 Acceptance Test

After all 12 tasks are committed:

- [ ] **Full test suite passes**

Run: `python -m pytest -v`
Expected: All tests pass. Expected counts: sanity (2) + ncbi_client (18) + checkpoint (4) + stuck_log (2) + truthcert (11) + integration (1) = **38 passed** (+/- as tests evolve).

- [ ] **Smoke script runs from warm cache in <10s with zero live calls**

Run (after at least one successful cassette-based run has populated the cache):

```bash
NCBI_API_KEY= python scripts/smoke_ncbi.py
```

Expected: prints PMID count, then 3 PMIDs + titles, total runtime <10s. The `data/cache/smoke/` directory must contain entries from the prior run.

- [ ] **No leaked API keys in cassettes or logs**

Run: `grep -r api_key tests/cassettes/ data/cache/`
Expected: zero matches.

- [ ] **Shippable artifact check**

The final commit state is:
- 11 feature commits + 1 test commit = 12 commits on `master`.
- `pipeline/ncbi_client.py` exports: `RateLimiter`, `RequestCache`, `SRRecord`, `fetch_raw`, `fetch_with_retries`, `esearch_pubmed`, `efetch_pubmed`, `MalformedResponseError`, `NCBIRateLimitError`.
- `pipeline/checkpoint.py` exports: `save_checkpoint`, `load_checkpoint`, `CheckpointError`.
- `pipeline/stuck_log.py` exports: `log_stuck`.
- `pipeline/truthcert.py` exports: `get_hmac_key`, `compute_chain`, `verify_chain`, `TruthCertError`.

This completes the standalone artifact that Plan 2 will build on.

---

## Plan 1 → Plan 2 handoff

Plan 2 (Corpus + PICO resolution) will:
- Import `esearch_pubmed`, `efetch_pubmed`, `SRRecord` from `pipeline.ncbi_client`.
- Import `save_checkpoint`, `load_checkpoint` from `pipeline.checkpoint`.
- Import `log_stuck` from `pipeline.stuck_log`.
- Import `compute_chain` from `pipeline.truthcert`.

None of these signatures should change mid-project. If Plan 2 needs a new
signature (e.g., pagination support on `esearch_pubmed`), add a new
parameter with a default that preserves current behavior — do not break
the existing one.
