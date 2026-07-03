"""Module-boundary contract test: conclusion_classifier -> concordance_engine.

Per the MetaReproducer P0-1 lesson (field-name drift between modules is the
#1 silent-failure source, spec Section 12.3): build a minimal, production-
shaped classifier output, feed it straight into the concordance engine, and
assert the result is a fully-populated ClusterConcordance rather than a
silent-failure sentinel (None, {}, "unknown_*").

This test fails loudly if either module renames a shared field.
"""
from __future__ import annotations

from pipeline.conclusion_classifier import classify
from pipeline.concordance_engine import ClusterConcordance, compute_cluster_concordance


def test_classifier_output_flows_into_concordance_engine():
    # Two real abstracts producing opposite direction poles.
    a = classify("benefit-1", "Conclusions: The drug significantly reduced mortality.")
    b = classify("harm-1", "Conclusions: The drug significantly increased the risk of stroke.")

    result = compute_cluster_concordance("drug__indication__mortality", [a, b])

    # Not a silent-failure sentinel.
    assert result is not None
    assert isinstance(result, ClusterConcordance)

    # Every field is populated with the expected type.
    assert result.cluster_id == "drug__indication__mortality"
    assert result.k_total == 2
    assert result.k_classifiable == 2
    assert isinstance(result.direction_pairwise_flip, bool)
    assert isinstance(result.significance_pairwise_flip, bool)
    assert isinstance(result.ambiguous_rate, float)
    assert isinstance(result.discordant, bool)

    # The engine consumed the classifier's .direction field: benefit vs harm
    # are polar opposites, so this cluster must be flagged discordant.
    assert result.direction_pairwise_flip is True
    assert result.discordant is True


def test_classifier_field_names_are_consumed_by_engine():
    # Regression guard: the engine reads .confidence, .direction, .significance.
    # If any is renamed on ConclusionClassification, this raises AttributeError
    # inside compute_cluster_concordance rather than silently mislabelling.
    c = classify("1", "Conclusions: There was no significant difference between groups.")
    for attr in ("pmid", "direction", "significance", "confidence"):
        assert hasattr(c, attr), f"classifier output missing contract field {attr!r}"

    result = compute_cluster_concordance("cid", [c])
    assert result.k_total == 1
