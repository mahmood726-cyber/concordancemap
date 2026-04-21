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

## Layout

```
pipeline/      — pipeline modules (ncbi_client, truthcert, ...)
tests/         — pytest suite + VCR cassettes
scripts/       — CLI entry points and smoke scripts
data/          — reference tables, cache, results (cache/results gitignored)
dashboard/     — single-file HTML dashboard (later plans)
paper/         — E156 manuscript + optional long paper (later plans)
docs/          — specs, plans, protocols
```

## License

(pending)
