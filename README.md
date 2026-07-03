# ConcordanceMap

> Cross-SR concordance audit on PICO-matched drug-indication clusters.
> Paper B2 of the "Global Evidence-Synthesis Reliability" trio.

**Status:** Plan 1 in progress (infrastructure + NCBI client). See the plan
at `docs/superpowers/plans/2026-04-21-plan-1-infrastructure-ncbi-client.md`
and the design spec at `docs/superpowers/specs/2026-04-21-concordancemap-design.md`.

## Install

Requires Python 3.11+ (3.13 is permitted; note that 3.13 + scipy needs the
WMI-deadlock monkey-patch applied at the scipy import site per portfolio rule).

```bash
pip install -e ".[dev]"
```

`[dev]` installs pytest, pytest-vcr, and vcrpy for the test harness.

## Run tests

```bash
python -m pytest -v
```

Integration tests use VCR cassettes under `tests/cassettes/`. CI runs with
`record_mode=none` — no live HTTP calls are ever made in the test suite.

## Worked example: classifier + concordance benchmark

The analysis core (deterministic conclusion classifier + per-cluster
concordance engine) can be exercised end-to-end, offline, against a versioned
hand-labeled fixture corpus:

```bash
python scripts/benchmark.py
```

This loads `data/reference/benchmark_abstracts_v1.json` (20 labeled SR
conclusion sentences grouped into 5 synthetic PICO clusters), runs
`pipeline.conclusion_classifier.classify()` on each, scores its
(direction, significance) predictions against the gold labels, then runs
`pipeline.concordance_engine.compute_cluster_concordance()` per cluster and
prints a discordance table. It is fully deterministic — same fixtures, same
numbers every run.

Example output (accuracies are properties of the classifier on the fixture
corpus, **not** empirical findings about the SR literature):

```
Fixtures classified: 20
Direction accuracy   : 0.941 (n=17, gold != AMBIGUOUS)
Significance accuracy: 0.684 (n=19, gold != AMBIGUOUS)
Prediction AMBIGUOUS rate: 0.450

Per-cluster concordance
cluster_id                     k  kc dir_kappa dflip sflip discordant
drugA__hf__mortality           5   5    -0.250 False  True       True
drugB__stroke__mace            4   4    -0.333  True False       True
drugC__copd__hosp              4   4       n/a False False      False
drugD__pain__pro               3   3       n/a False False      False
drugE__infection__safety       4   2    -1.000  True False       True

Raw discordance rate (fraction of clusters flagged): 0.600
```

Pass `--json-out data/results/benchmark.json` to also write a machine-readable
result bundle. `data/results/` is gitignored. The pinned accuracy/discordance
numbers are guarded by `tests/test_benchmark.py` so a lexicon change that
regresses accuracy fails CI.

The classifier is intentionally conservative: it abstains (`AMBIGUOUS`) rather
than guess on hedged or conflicting conclusions. The `AMBIGUOUS` rate is a
reported quantity, not a failure (see the design spec, Section 15).

`concordance_engine.fleiss_kappa()` is cross-validated to 1e-9 against
`statsmodels.stats.inter_rater.fleiss_kappa` on multi-item fixtures (that
check is skipped if statsmodels is not installed — it is not a required
dependency).

## Layout

```
pipeline/      — pipeline modules:
                   ncbi_client            (NCBI E-utilities: cache, rate-limit)
                   truthcert              (SHA-256+HMAC provenance chain)
                   checkpoint, stuck_log  (resumability + failure log)
                   conclusion_classifier  (deterministic direction+significance)
                   concordance_engine     (Fleiss' kappa + pairwise-flip)
tests/         — pytest suite + VCR cassettes
scripts/       — smoke_ncbi.py (live-cache smoke) + benchmark.py (offline demo)
data/          — reference tables (versioned), cache/results (gitignored)
dashboard/     — single-file HTML dashboard (later plans)
paper/         — E156 manuscript + optional long paper (later plans)
docs/          — specs, plans, protocols
```

## License

(pending)
