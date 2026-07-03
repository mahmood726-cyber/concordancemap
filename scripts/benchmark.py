"""Reproducible benchmark for the ConcordanceMap analysis core.

Runs the deterministic conclusion classifier over a hand-labeled fixture
corpus, reports per-dimension accuracy against the gold labels, then runs the
concordance engine over the fixtures' synthetic clusters and reports the
per-cluster discordance table plus the raw discordance rate.

Fully offline and deterministic: no network, no randomness. Same fixtures ->
same numbers every run, which is the property the concordance method depends
on.

Usage:
    python scripts/benchmark.py
    python scripts/benchmark.py --fixtures data/reference/benchmark_abstracts_v1.json
    python scripts/benchmark.py --json-out data/results/benchmark.json

Exit code is 0 on success, 2 on a missing/malformed fixtures file.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

# Allow running as a plain script (python scripts/benchmark.py) without install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.conclusion_classifier import AMBIGUOUS, classify  # noqa: E402
from pipeline.concordance_engine import (  # noqa: E402
    compute_cluster_concordance,
    raw_discordance_rate,
)

DEFAULT_FIXTURES = _REPO_ROOT / "data" / "reference" / "benchmark_abstracts_v1.json"


def _utf8_stdout() -> None:
    """Force UTF-8 stdout so the table renders on Windows cp1252 consoles."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_fixtures(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"error: fixtures file not found: {path}")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: fixtures file is not valid JSON: {exc}")
    fixtures = doc.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise SystemExit("error: fixtures file has no non-empty 'fixtures' list")
    return fixtures


def run_classifier_benchmark(fixtures: list[dict]) -> dict:
    """Classify every fixture and score accuracy per dimension.

    Accuracy denominators exclude fixtures whose gold label is AMBIGUOUS on
    that dimension (a rule-based abstainer cannot be "wrong" for abstaining on
    a genuinely ambiguous abstract; those are tracked separately as the
    abstention-agreement count).
    """
    rows = []
    dir_correct = dir_total = 0
    sig_correct = sig_total = 0
    ambiguous_predictions = 0

    for fx in fixtures:
        pred = classify(fx["id"], fx["abstract"])
        gold_dir = fx["gold_direction"]
        gold_sig = fx["gold_significance"]

        dir_ok = pred.direction == gold_dir
        sig_ok = pred.significance == gold_sig

        if gold_dir != AMBIGUOUS:
            dir_total += 1
            dir_correct += int(dir_ok)
        if gold_sig != AMBIGUOUS:
            sig_total += 1
            sig_correct += int(sig_ok)
        if pred.direction == AMBIGUOUS or pred.significance == AMBIGUOUS:
            ambiguous_predictions += 1

        rows.append(
            {
                "id": fx["id"],
                "gold_direction": gold_dir,
                "pred_direction": pred.direction,
                "direction_ok": dir_ok,
                "gold_significance": gold_sig,
                "pred_significance": pred.significance,
                "significance_ok": sig_ok,
                "confidence": pred.confidence,
            }
        )

    return {
        "rows": rows,
        "direction_accuracy": (dir_correct / dir_total) if dir_total else None,
        "direction_n": dir_total,
        "significance_accuracy": (sig_correct / sig_total) if sig_total else None,
        "significance_n": sig_total,
        "ambiguous_prediction_rate": ambiguous_predictions / len(fixtures),
        "n_fixtures": len(fixtures),
    }


def run_concordance_benchmark(fixtures: list[dict]) -> dict:
    """Group fixtures by cluster_id and compute per-cluster concordance."""
    by_cluster: dict[str, list] = defaultdict(list)
    for fx in fixtures:
        cid = fx.get("cluster_id", "unclustered")
        by_cluster[cid].append(classify(fx["id"], fx["abstract"]))

    clusters = [
        compute_cluster_concordance(cid, clfs)
        for cid, clfs in sorted(by_cluster.items())
    ]
    return {
        "clusters": clusters,
        "raw_discordance_rate": raw_discordance_rate(clusters),
    }


def _fmt(value) -> str:
    if value is None:
        return "  n/a"
    return f"{value:.3f}"


def print_report(clf_result: dict, conc_result: dict) -> None:
    print("=" * 70)
    print("ConcordanceMap benchmark")
    print("=" * 70)
    print()
    print(f"Fixtures classified: {clf_result['n_fixtures']}")
    print(
        f"Direction accuracy   : {_fmt(clf_result['direction_accuracy'])} "
        f"(n={clf_result['direction_n']}, gold != AMBIGUOUS)"
    )
    print(
        f"Significance accuracy: {_fmt(clf_result['significance_accuracy'])} "
        f"(n={clf_result['significance_n']}, gold != AMBIGUOUS)"
    )
    print(
        f"Prediction AMBIGUOUS rate: {clf_result['ambiguous_prediction_rate']:.3f}"
    )
    print()

    # Misclassifications, if any, for transparency.
    misses = [
        r for r in clf_result["rows"] if not (r["direction_ok"] and r["significance_ok"])
    ]
    if misses:
        print(f"Fixtures with any dimension mismatch ({len(misses)}):")
        print(
            f"  {'id':<5} {'gold_dir':<10} {'pred_dir':<10} "
            f"{'gold_sig':<9} {'pred_sig':<9}"
        )
        for r in misses:
            print(
                f"  {r['id']:<5} {r['gold_direction']:<10} {r['pred_direction']:<10} "
                f"{r['gold_significance']:<9} {r['pred_significance']:<9}"
            )
        print()

    print("-" * 70)
    print("Per-cluster concordance")
    print("-" * 70)
    header = (
        f"{'cluster_id':<28} {'k':>3} {'kc':>3} "
        f"{'dir_kappa':>9} {'dflip':>5} {'sflip':>5} {'discordant':>10}"
    )
    print(header)
    for c in conc_result["clusters"]:
        print(
            f"{c.cluster_id:<28} {c.k_total:>3} {c.k_classifiable:>3} "
            f"{_fmt(c.direction_kappa):>9} "
            f"{str(c.direction_pairwise_flip):>5} "
            f"{str(c.significance_pairwise_flip):>5} "
            f"{str(c.discordant):>10}"
        )
    print()
    rate = conc_result["raw_discordance_rate"]
    print(f"Raw discordance rate (fraction of clusters flagged): {_fmt(rate)}")
    print("=" * 70)


def _serializable(clf_result: dict, conc_result: dict) -> dict:
    return {
        "classifier": {
            "n_fixtures": clf_result["n_fixtures"],
            "direction_accuracy": clf_result["direction_accuracy"],
            "direction_n": clf_result["direction_n"],
            "significance_accuracy": clf_result["significance_accuracy"],
            "significance_n": clf_result["significance_n"],
            "ambiguous_prediction_rate": clf_result["ambiguous_prediction_rate"],
            "rows": clf_result["rows"],
        },
        "concordance": {
            "raw_discordance_rate": conc_result["raw_discordance_rate"],
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "k_total": c.k_total,
                    "k_classifiable": c.k_classifiable,
                    "direction_kappa": c.direction_kappa,
                    "direction_pairwise_flip": c.direction_pairwise_flip,
                    "significance_kappa": c.significance_kappa,
                    "significance_pairwise_flip": c.significance_pairwise_flip,
                    "ambiguous_rate": c.ambiguous_rate,
                    "discordant": c.discordant,
                }
                for c in conc_result["clusters"]
            ],
        },
    }


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES,
        help="Path to a benchmark_abstracts JSON file.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write machine-readable results JSON.",
    )
    args = parser.parse_args(argv)

    fixtures = load_fixtures(args.fixtures)
    clf_result = run_classifier_benchmark(fixtures)
    conc_result = run_concordance_benchmark(fixtures)
    print_report(clf_result, conc_result)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(_serializable(clf_result, conc_result), indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote results JSON to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
