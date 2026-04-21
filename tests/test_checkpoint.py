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
