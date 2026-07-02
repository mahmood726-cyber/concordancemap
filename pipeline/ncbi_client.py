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
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Hardened XML parser: disable entity resolution (billion-laughs / XXE
# defense) and network access (no external DTD fetches). NCBI responses
# never require entity expansion, and cached bytes on disk should not
# trigger amplified allocation if tampered with.
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


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
    """Fetch with exponential backoff on 429/503, inlining fetch_raw's
    cache/limiter/HTTP logic (so we can inspect the raw status before
    deciding whether to retry or fail closed).

    Backoff schedule with default max_retries=5: 2s, 4s, 8s, 16s, then
    raise NCBIRateLimitError on the 5th attempt (no sleep before the
    final raise). Any non-retryable non-2xx raises MalformedResponseError
    immediately. Transport exceptions also raise MalformedResponseError.
    Cache hits short-circuit before any retry logic.
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

        if response.status_code == 200:
            if response.content:
                cache.put(url, params, response.content)
                return response.content
            raise MalformedResponseError(f"empty body for {url}")

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

    retmax default 10000 is PubMed's per-request cap; callers doing
    larger searches should paginate via retstart (out of scope here).
    Empty result returns empty list, not None.
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
        root = etree.fromstring(body, _XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise MalformedResponseError(f"esearch XML parse failed: {exc}") from exc

    pmids: list[str] = []
    for id_elem in root.iter("Id"):
        text = (id_elem.text or "").strip()
        if text:
            pmids.append(text)
    return pmids


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

    def _full_text(xpath: str) -> str:
        # Concatenate all descendant text so inline markup (e.g. <i>, <sup>,
        # <b>) does not truncate the value. Plain node.text stops at the first
        # child element, silently dropping everything after it — for PubMed
        # abstracts and titles that routinely embed italics/superscripts this
        # would lose statistical results (e.g. "significant (p<0.05) HR 0.71").
        node = article_el.find(xpath)
        return "".join(node.itertext()).strip() if node is not None else ""

    def _all_text(xpath: str) -> list[str]:
        return [(n.text or "").strip() for n in article_el.iterfind(xpath)]

    pmid = _text(".//MedlineCitation/PMID")
    title = _full_text(".//Article/ArticleTitle")

    abstract_nodes = article_el.iterfind(".//Article/Abstract/AbstractText")
    abstract = " ".join("".join(n.itertext()).strip() for n in abstract_nodes)

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

    year_text = _text(".//DateCompleted/Year") or _text(
        ".//Article/Journal/JournalIssue/PubDate/Year"
    )
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
    Large batches may exceed the default 30s fetch_raw timeout — not yet
    parameterised here; future enhancement if Task 12 integration observes
    timeouts.
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
        root = etree.fromstring(body, _XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise MalformedResponseError(f"efetch XML parse failed: {exc}") from exc

    return [_parse_pubmed_article(art) for art in root.iter("PubmedArticle")]
