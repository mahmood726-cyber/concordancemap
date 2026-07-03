"""ConcordanceMap pipeline package.

Modules:
    ncbi_client            — cache-first NCBI E-utilities wrapper
    truthcert              — SHA-256+HMAC provenance chain
    checkpoint             — resumable per-pair stage checkpoints
    stuck_log              — JSONL append-only hard-failure log
    conclusion_classifier  — deterministic direction+significance classifier
    concordance_engine     — per-cluster Fleiss' kappa + pairwise-flip metrics
    (corpus_selector, sr_harvester, pico_resolver, calibration,
     orchestrator added in later plans)
"""

__version__ = "0.1.0"
