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
