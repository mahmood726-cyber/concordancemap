"""Per-cluster concordance metrics (spec Section 5.6).

Given the classifier labels for the systematic reviews in one PICO cluster,
compute how much those independently-conducted reviews agree. The primary
input to the estimand is the boolean ``discordant`` flag.

Two concordance signals per dimension (direction, significance):

  1. **Fleiss' kappa** over the categorical labels. See ``fleiss_kappa`` for
     the exact definition used and its single-item interpretation.
  2. **Pairwise polar-opposite flip**: True iff any two reviews in the cluster
     land on opposite poles (benefit vs harm for direction; sig vs nonsig for
     significance). This is deliberately stricter than kappa — one polar
     disagreement is enough to flag a cluster, matching the spec's stance that
     a single outlier undermines the single-ground-truth claim (spec 18).

Empty-input guard (Sentinel P1 rule, 2026-04-15 lesson): every positional
access here is preceded by an explicit length check; an empty or all-AMBIGUOUS
cluster yields ``None`` kappas and ``discordant=False`` rather than raising.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from pipeline.conclusion_classifier import AMBIGUOUS, ConclusionClassification

# Discordance threshold from the pre-registered commitments (spec Section 10,
# item 10). Kept as a module constant so the threshold is auditable and any
# change is a visible diff.
KAPPA_DISCORDANCE_THRESHOLD = 0.6

_DIRECTION_POLES = frozenset({"benefit", "harm"})
_SIGNIFICANCE_POLES = frozenset({"sig", "nonsig"})


@dataclass
class ClusterConcordance:
    """Concordance summary for one PICO cluster (spec Section 5.6)."""

    cluster_id: str
    k_total: int
    k_classifiable: int
    direction_kappa: float | None
    direction_pairwise_flip: bool
    significance_kappa: float | None
    significance_pairwise_flip: bool
    ambiguous_rate: float
    discordant: bool


def fleiss_kappa(ratings: Sequence[Sequence[str]]) -> float | None:
    """Fleiss' kappa for a list of items, each a list of categorical labels.

    Standard multi-rater definition (Fleiss 1971). For ``N`` items each rated
    by ``n`` raters over ``k`` categories with ``n_ij`` = count of raters
    assigning category ``j`` to item ``i``::

        p_j    = sum_i n_ij / (N * n)            # marginal per category
        P_i    = (sum_j n_ij^2 - n) / (n*(n-1))  # observed agreement, item i
        P_bar  = mean_i P_i
        P_bar_e = sum_j p_j^2
        kappa  = (P_bar - P_bar_e) / (1 - P_bar_e)

    Returns ``None`` (undefined) when:
      * there are no items, or
      * any item has < 2 ratings (P_i needs n >= 2), or
      * the expected-agreement denominator ``1 - P_bar_e`` is 0 (all raters
        chose a single category — perfect but degenerate agreement).

    Concordance interpretation for this project: a *cluster* is one "item" and
    its member SRs are the "raters". With a single cluster this reduces to the
    within-item agreement ``P_i`` corrected for chance, which is exactly the
    per-cluster agreement the estimand wants. ``concordance_engine`` calls it
    with a one-item list; the function also accepts multi-item input so the
    same implementation can be R-validated against ``irr::kappam.fleiss`` on
    multi-item fixtures.
    """
    items = [list(r) for r in ratings if r is not None]
    if not items:
        return None
    # All items must share the same number of raters for classical Fleiss.
    n_raters = len(items[0])
    if n_raters < 2:
        return None
    if any(len(item) != n_raters for item in items):
        raise ValueError(
            "fleiss_kappa: all items must have the same number of ratings "
            f"(got {sorted({len(i) for i in items})})"
        )

    categories = sorted({label for item in items for label in item})
    n_items = len(items)

    # n_ij matrix as list of Counters.
    counts = [Counter(item) for item in items]

    # Marginal proportion per category.
    total_assignments = n_items * n_raters
    p_j = {
        c: sum(cnt.get(c, 0) for cnt in counts) / total_assignments
        for c in categories
    }

    # Observed agreement per item.
    p_i_values = []
    for cnt in counts:
        sq = sum(v * v for v in cnt.values())
        p_i_values.append((sq - n_raters) / (n_raters * (n_raters - 1)))
    p_bar = sum(p_i_values) / n_items

    p_bar_e = sum(v * v for v in p_j.values())

    denom = 1.0 - p_bar_e
    if denom == 0:
        # All assignments in a single category: agreement is perfect but
        # chance-corrected kappa is undefined (0/0). Report None; callers
        # treat a single-category cluster as concordant via the pairwise flip
        # path (which will be False).
        return None
    return (p_bar - p_bar_e) / denom


def _pairwise_flip(labels: Sequence[str], poles: frozenset[str]) -> bool:
    """True iff both opposite poles appear among the labels."""
    present = set(labels)
    return poles.issubset(present)


def compute_cluster_concordance(
    cluster_id: str,
    classifications: Sequence[ConclusionClassification],
) -> ClusterConcordance:
    """Compute concordance metrics for one cluster.

    Args:
        cluster_id: identifier echoed into the output.
        classifications: the classifier outputs for every SR in the cluster.

    Returns:
        ClusterConcordance. AMBIGUOUS-confidence records are excluded from the
        kappa/flip computation (per spec 5.6 step 1) but counted in
        ``ambiguous_rate`` and ``k_total``.

    A cluster with fewer than 2 classifiable reviews has ``None`` kappas and
    is ``discordant=False`` (no disagreement is observable with < 2 raters).
    """
    if not isinstance(cluster_id, str):
        raise TypeError(f"cluster_id must be str, got {type(cluster_id).__name__}")

    records = list(classifications)
    k_total = len(records)

    classifiable = [c for c in records if c.confidence != AMBIGUOUS]
    k_classifiable = len(classifiable)
    ambiguous_rate = (k_total - k_classifiable) / k_total if k_total else 0.0

    # Restrict each dimension to records whose label on that dimension is a
    # real category (not AMBIGUOUS), independent of the overall confidence
    # bucket, so a PARTIAL record still contributes its resolved dimension.
    dir_labels = [
        c.direction
        for c in classifiable
        if c.direction != AMBIGUOUS
    ]
    sig_labels = [
        c.significance
        for c in classifiable
        if c.significance != AMBIGUOUS
    ]

    direction_kappa = (
        fleiss_kappa([dir_labels]) if len(dir_labels) >= 2 else None
    )
    significance_kappa = (
        fleiss_kappa([sig_labels]) if len(sig_labels) >= 2 else None
    )

    direction_flip = _pairwise_flip(dir_labels, _DIRECTION_POLES)
    significance_flip = _pairwise_flip(sig_labels, _SIGNIFICANCE_POLES)

    discordant = (
        (direction_kappa is not None and direction_kappa < KAPPA_DISCORDANCE_THRESHOLD)
        or direction_flip
        or significance_flip
    )

    return ClusterConcordance(
        cluster_id=cluster_id,
        k_total=k_total,
        k_classifiable=k_classifiable,
        direction_kappa=direction_kappa,
        direction_pairwise_flip=direction_flip,
        significance_kappa=significance_kappa,
        significance_pairwise_flip=significance_flip,
        ambiguous_rate=ambiguous_rate,
        discordant=discordant,
    )


def raw_discordance_rate(clusters: Sequence[ClusterConcordance]) -> float | None:
    """Fraction of clusters flagged discordant (the raw, uncorrected delta).

    Returns ``None`` for an empty input (the estimand is undefined with no
    clusters) rather than dividing by zero.
    """
    if not clusters:
        return None
    return sum(1 for c in clusters if c.discordant) / len(clusters)
