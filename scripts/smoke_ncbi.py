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
