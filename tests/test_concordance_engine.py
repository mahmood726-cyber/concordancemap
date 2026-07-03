"""Tests for pipeline.concordance_engine.

Covers the Fleiss' kappa implementation (including a cross-validation against
statsmodels when available) and the per-cluster concordance/discordance logic.
"""
from __future__ import annotations

import pytest

from pipeline.conclusion_classifier import AMBIGUOUS, ConclusionClassification
from pipeline.concordance_engine import (
    ClusterConcordance,
    compute_cluster_concordance,
    fleiss_kappa,
    raw_discordance_rate,
)


def _clf(pmid, direction, significance, confidence="ALL_RULES_FIRED"):
    return ConclusionClassification(
        pmid=pmid,
        direction=direction,
        significance=significance,
        rules_fired=[],
        confidence=confidence,
        conclusion_span="",
    )


# ---------------------------------------------------------------------------
# Fleiss' kappa
# ---------------------------------------------------------------------------


def test_fleiss_perfect_agreement_single_category_returns_none():
    # All raters same category -> chance-corrected kappa undefined (0/0).
    assert fleiss_kappa([["a", "a", "a", "a"]]) is None


def test_fleiss_perfect_agreement_multi_item():
    # Two items, each internally unanimous but on different categories:
    # observed agreement is perfect, expected < 1, kappa == 1.
    k = fleiss_kappa([["a", "a", "a"], ["b", "b", "b"]])
    assert k == pytest.approx(1.0)


def test_fleiss_total_disagreement_is_low():
    k = fleiss_kappa([["a", "b", "c"]])
    assert k is not None
    assert k < 0.0  # worse than chance for a single split item


def test_fleiss_empty_returns_none():
    assert fleiss_kappa([]) is None


def test_fleiss_single_rater_returns_none():
    assert fleiss_kappa([["a"]]) is None


def test_fleiss_ragged_items_raise():
    with pytest.raises(ValueError, match="same number of ratings"):
        fleiss_kappa([["a", "b"], ["a", "b", "c"]])


def test_fleiss_matches_statsmodels():
    statsmodels = pytest.importorskip("statsmodels.stats.inter_rater")
    import numpy as np

    items = [
        ["a", "a", "a", "b"],
        ["b", "b", "c", "c"],
        ["a", "b", "a", "a"],
        ["c", "c", "c", "c"],
        ["a", "a", "b", "b"],
    ]
    cats = ["a", "b", "c"]
    mat = np.array([[item.count(c) for c in cats] for item in items])
    expected = statsmodels.fleiss_kappa(mat)
    assert fleiss_kappa(items) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# Cluster concordance
# ---------------------------------------------------------------------------


def test_all_agree_benefit_is_concordant():
    clfs = [_clf(str(i), "benefit", "sig") for i in range(5)]
    c = compute_cluster_concordance("cluster1", clfs)
    assert c.k_total == 5
    assert c.k_classifiable == 5
    assert c.direction_pairwise_flip is False
    assert c.significance_pairwise_flip is False
    assert c.discordant is False


def test_polar_direction_flip_is_discordant():
    clfs = [
        _clf("1", "benefit", "sig"),
        _clf("2", "benefit", "sig"),
        _clf("3", "harm", "sig"),
    ]
    c = compute_cluster_concordance("cluster2", clfs)
    assert c.direction_pairwise_flip is True
    assert c.discordant is True


def test_polar_significance_flip_is_discordant():
    clfs = [
        _clf("1", "benefit", "sig"),
        _clf("2", "benefit", "nonsig"),
    ]
    c = compute_cluster_concordance("cluster3", clfs)
    assert c.significance_pairwise_flip is True
    assert c.discordant is True


def test_low_kappa_is_discordant_without_polar_flip():
    # benefit vs null (not polar opposites) but disagreement lowers kappa.
    clfs = [
        _clf("1", "benefit", "unclear"),
        _clf("2", "null", "unclear"),
        _clf("3", "benefit", "unclear"),
        _clf("4", "null", "unclear"),
    ]
    c = compute_cluster_concordance("cluster4", clfs)
    assert c.direction_pairwise_flip is False  # benefit/null are not poles
    assert c.direction_kappa is not None
    assert c.direction_kappa < 0.6
    assert c.discordant is True


def test_ambiguous_records_excluded_from_kappa_but_counted():
    clfs = [
        _clf("1", "benefit", "sig"),
        _clf("2", "benefit", "sig"),
        _clf("3", AMBIGUOUS, AMBIGUOUS, confidence=AMBIGUOUS),
    ]
    c = compute_cluster_concordance("cluster5", clfs)
    assert c.k_total == 3
    assert c.k_classifiable == 2
    assert c.ambiguous_rate == pytest.approx(1 / 3)
    assert c.discordant is False


def test_single_classifiable_record_not_discordant():
    clfs = [_clf("1", "benefit", "sig")]
    c = compute_cluster_concordance("cluster6", clfs)
    assert c.direction_kappa is None
    assert c.significance_kappa is None
    assert c.discordant is False


def test_empty_cluster_is_not_discordant():
    c = compute_cluster_concordance("empty", [])
    assert c.k_total == 0
    assert c.k_classifiable == 0
    assert c.ambiguous_rate == 0.0
    assert c.direction_kappa is None
    assert c.discordant is False


def test_all_ambiguous_cluster():
    clfs = [_clf(str(i), AMBIGUOUS, AMBIGUOUS, confidence=AMBIGUOUS) for i in range(3)]
    c = compute_cluster_concordance("cluster7", clfs)
    assert c.k_classifiable == 0
    assert c.ambiguous_rate == pytest.approx(1.0)
    assert c.discordant is False


def test_partial_record_contributes_resolved_dimension():
    # A PARTIAL record: direction resolved, significance AMBIGUOUS.
    clfs = [
        _clf("1", "benefit", "sig"),
        _clf("2", "harm", AMBIGUOUS, confidence="PARTIAL"),
    ]
    c = compute_cluster_concordance("cluster8", clfs)
    # Direction poles benefit+harm both present -> flip.
    assert c.direction_pairwise_flip is True
    assert c.discordant is True


def test_returns_dataclass():
    c = compute_cluster_concordance("x", [_clf("1", "benefit", "sig")])
    assert isinstance(c, ClusterConcordance)
    assert c.cluster_id == "x"


def test_non_str_cluster_id_raises():
    with pytest.raises(TypeError, match="cluster_id must be str"):
        compute_cluster_concordance(123, [])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# raw_discordance_rate
# ---------------------------------------------------------------------------


def test_raw_discordance_rate():
    concordant = compute_cluster_concordance("a", [_clf("1", "benefit", "sig"), _clf("2", "benefit", "sig")])
    discordant = compute_cluster_concordance("b", [_clf("1", "benefit", "sig"), _clf("2", "harm", "sig")])
    rate = raw_discordance_rate([concordant, discordant])
    assert rate == pytest.approx(0.5)


def test_raw_discordance_rate_empty_returns_none():
    assert raw_discordance_rate([]) is None
