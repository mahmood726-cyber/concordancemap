"""Regression test for the benchmark harness (scripts/benchmark.py).

Pins the deterministic benchmark outputs so any future lexicon change that
regresses classifier accuracy is caught here (the gold-set regression pattern
from spec Section 12.4). If a lexicon change is intended, update these pinned
numbers in the same commit and explain the delta in the message.

The pinned accuracies below are properties of the deterministic classifier on
the versioned fixture corpus; they are NOT scientific findings about the SR
literature.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.benchmark as bench

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _REPO_ROOT / "data" / "reference" / "benchmark_abstracts_v1.json"


@pytest.fixture(scope="module")
def fixtures():
    return bench.load_fixtures(_FIXTURES)


def test_fixtures_file_present_and_wellformed(fixtures):
    assert len(fixtures) == 20
    for fx in fixtures:
        assert set(fx) >= {"id", "abstract", "gold_direction", "gold_significance"}


def test_classifier_benchmark_pinned_accuracy(fixtures):
    result = bench.run_classifier_benchmark(fixtures)
    # Direction accuracy: 16/17 gold-non-AMBIGUOUS correct.
    assert result["direction_n"] == 17
    assert result["direction_accuracy"] == pytest.approx(16 / 17)
    # Significance accuracy: 13/19 gold-non-AMBIGUOUS correct.
    assert result["significance_n"] == 19
    assert result["significance_accuracy"] == pytest.approx(13 / 19)


def test_concordance_benchmark_pinned_rate(fixtures):
    result = bench.run_concordance_benchmark(fixtures)
    # 5 synthetic clusters, 3 flagged discordant -> 0.6.
    assert len(result["clusters"]) == 5
    assert result["raw_discordance_rate"] == pytest.approx(0.6)


def test_benchmark_json_output_roundtrips(fixtures, tmp_path):
    clf = bench.run_classifier_benchmark(fixtures)
    conc = bench.run_concordance_benchmark(fixtures)
    payload = bench._serializable(clf, conc)
    out = tmp_path / "benchmark.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["classifier"]["n_fixtures"] == 20
    assert reloaded["concordance"]["raw_discordance_rate"] == pytest.approx(0.6)


def test_main_runs_clean(tmp_path, capsys):
    out = tmp_path / "results.json"
    rc = bench.main(["--json-out", str(out)])
    assert rc == 0
    assert out.exists()
    captured = capsys.readouterr().out
    assert "ConcordanceMap benchmark" in captured
    assert "Raw discordance rate" in captured


def test_main_missing_fixtures_exits(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit):
        bench.main(["--fixtures", str(missing)])
