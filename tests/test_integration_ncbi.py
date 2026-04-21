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
