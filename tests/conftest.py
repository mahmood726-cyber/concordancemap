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
