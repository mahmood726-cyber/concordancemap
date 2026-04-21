"""Tests for pipeline.ncbi_client."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from pipeline.ncbi_client import (
    MalformedResponseError,
    NCBIRateLimitError,
    RateLimiter,
    RequestCache,
    esearch_pubmed,
    fetch_raw,
    fetch_with_retries,
)


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


def test_fetch_raw_wraps_transport_error_as_malformed(tmp_path: Path):
    """A session.get() that raises must be re-raised as MalformedResponseError."""
    cache = RequestCache(cache_dir=tmp_path)
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    class _ErrorSession:
        def get(self, url: str, params: dict, timeout: float):
            raise ConnectionError("simulated network unreachable")

    with pytest.raises(MalformedResponseError, match="transport error"):
        fetch_raw(
            "http://e.com/x",
            {},
            session=_ErrorSession(),
            limiter=limiter,
            cache=cache,
        )

    assert cache.get("http://e.com/x", {}) is None, \
        "transport failure must not cache anything"


class _FlakySession:
    """Returns a sequence of fake responses, one per .get() call."""

    def __init__(self, responses: list):
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
    """Four retry responses (2x 429 + 2x 503) then success -> backoffs 2, 4, 8, 16s."""
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
    assert len(sess.calls) == 5, "must attempt max_retries times before raising"


def test_fetch_with_retries_non_retryable_500_raises_immediately(tmp_path):
    """A 500 must raise MalformedResponseError on the first attempt; no sleep, no retry."""
    cache = RequestCache(cache_dir=tmp_path)
    sess = _FlakySession([_FakeResponse(500, b"<html>500</html>")])
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    with pytest.raises(MalformedResponseError, match="non-2xx response 500"):
        fetch_with_retries(
            "http://e.com/x", {}, session=sess, limiter=limiter, cache=cache, max_retries=5,
        )
    assert len(sess.calls) == 1, "500 is not retryable; must not retry"
    assert cache.get("http://e.com/x", {}) is None


def test_fetch_with_retries_transport_error_wraps_as_malformed(tmp_path):
    """A transport exception must become MalformedResponseError without retry."""
    cache = RequestCache(cache_dir=tmp_path)
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    class _ErrorSession:
        def __init__(self):
            self.calls: list = []

        def get(self, url: str, params: dict, timeout: float):
            self.calls.append((url, dict(params)))
            raise ConnectionError("simulated network unreachable")

    sess = _ErrorSession()
    with pytest.raises(MalformedResponseError, match="transport error"):
        fetch_with_retries(
            "http://e.com/x", {}, session=sess, limiter=limiter, cache=cache, max_retries=5,
        )
    assert len(sess.calls) == 1, "transport errors are not retried"


def test_esearch_pubmed_parses_pmids_from_xml(tmp_path):
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


def test_esearch_pubmed_api_key_included_when_provided(tmp_path):
    """api_key=str inserts 'api_key' into request params; api_key=None omits it."""
    cache = RequestCache(cache_dir=tmp_path)
    fake_xml = b"<?xml version='1.0'?><eSearchResult><IdList><Id>1</Id></IdList></eSearchResult>"

    sess_keyed = _FakeSession(_FakeResponse(200, fake_xml))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)
    esearch_pubmed(
        query="x", cache=cache, session=sess_keyed, limiter=limiter, api_key="TESTKEY",
    )
    _, params_keyed = sess_keyed.calls[0]
    assert params_keyed["api_key"] == "TESTKEY"

    # Use fresh tmp_path subdir to avoid cache-hit short-circuit
    sess_none = _FakeSession(_FakeResponse(200, fake_xml))
    cache2 = RequestCache(cache_dir=tmp_path / "no_key")
    esearch_pubmed(
        query="x", cache=cache2, session=sess_none, limiter=limiter, api_key=None,
    )
    _, params_none = sess_none.calls[0]
    assert "api_key" not in params_none


def test_esearch_pubmed_retmax_passed_to_params(tmp_path):
    """retmax parameter must appear in request params as a string."""
    cache = RequestCache(cache_dir=tmp_path)
    fake_xml = b"<?xml version='1.0'?><eSearchResult><IdList><Id>1</Id></IdList></eSearchResult>"
    sess = _FakeSession(_FakeResponse(200, fake_xml))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    esearch_pubmed(
        query="x", cache=cache, session=sess, limiter=limiter, api_key=None, retmax=42,
    )
    _, params = sess.calls[0]
    assert params["retmax"] == "42"


def test_esearch_pubmed_filters_empty_pmid_elements(tmp_path):
    """Empty or whitespace <Id/> elements must be filtered, not returned as '' strings."""
    cache = RequestCache(cache_dir=tmp_path)
    xml_with_empty = b"""<?xml version="1.0"?>
<eSearchResult><IdList>
  <Id>123</Id>
  <Id></Id>
  <Id>   </Id>
  <Id>456</Id>
</IdList></eSearchResult>"""
    sess = _FakeSession(_FakeResponse(200, xml_with_empty))
    limiter = RateLimiter(rate_per_sec=10.0, burst=10)

    pmids = esearch_pubmed(
        query="x", cache=cache, session=sess, limiter=limiter, api_key=None,
    )
    assert pmids == ["123", "456"]
