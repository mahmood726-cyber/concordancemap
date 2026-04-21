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
