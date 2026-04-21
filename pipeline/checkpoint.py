"""Resumable per-pair stage checkpoints.

Each pair's progress is persisted as JSON under
    <root>/<pair_id>/<stage>.json
so an interrupted run can pick up from the last completed stage.

Atomic writes via .tmp + replace (same pattern as RequestCache.put
in ncbi_client.py). Corrupted checkpoint files raise CheckpointError
rather than returning stale or malformed data — fail closed.
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
    """Atomically write data as JSON to <root>/<pair_id>/<stage>.json.

    The parent directory is created if missing. The write goes through a
    .tmp sibling and then Path.replace() to eliminate the partial-write
    window (same pattern as RequestCache.put in ncbi_client.py).
    """
    p = _checkpoint_path(root, pair_id, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_checkpoint(root: Path, pair_id: str, stage: str) -> Any | None:
    """Return the checkpoint dict, or None if absent.

    Raises CheckpointError on JSON parse failure — never silently returns
    stale or malformed data.
    """
    p = _checkpoint_path(root, pair_id, stage)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckpointError(f"corrupted checkpoint at {p}: {exc}") from exc
