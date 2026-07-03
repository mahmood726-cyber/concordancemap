"""Tests for pipeline.conclusion_classifier.

The classifier is deterministic and rule-based; these fixtures pin its
behaviour on representative conclusion sentences and on the hard cases
(negation, conflicting signals, hedged language, missing abstract).
"""
from __future__ import annotations

import pytest

from pipeline.conclusion_classifier import (
    AMBIGUOUS,
    ConclusionClassification,
    LEXICON_VERSION,
    classify,
    extract_conclusion_span,
)


# ---------------------------------------------------------------------------
# Direction dimension
# ---------------------------------------------------------------------------


def test_benefit_reduced_mortality():
    r = classify("1", "Conclusions: The intervention significantly reduced all-cause mortality.")
    assert r.direction == "benefit"
    assert r.significance == "sig"
    assert r.confidence == "ALL_RULES_FIRED"


def test_benefit_improved_outcomes():
    r = classify("2", "In conclusion, treatment improved survival compared with placebo.")
    assert r.direction == "benefit"


def test_benefit_lower_risk():
    r = classify("3", "Conclusions: The drug was associated with a lower risk of hospitalization.")
    assert r.direction == "benefit"


def test_harm_increased_risk():
    r = classify("4", "Conclusions: The exposure significantly increased the risk of stroke.")
    assert r.direction == "harm"
    assert r.significance == "sig"


def test_harm_higher_mortality():
    r = classify("5", "We conclude that the treatment was associated with a higher mortality rate.")
    assert r.direction == "harm"


def test_null_no_significant_difference():
    r = classify("6", "Conclusions: There was no significant difference between groups.")
    assert r.direction == "null"
    assert r.significance == "nonsig"


def test_null_did_not_improve():
    r = classify("7", "Conclusions: The intervention did not improve overall survival.")
    assert r.direction == "null"


def test_null_similar_outcomes():
    r = classify("8", "Conclusions: Outcomes were similar between the two arms.")
    assert r.direction == "null"


# ---------------------------------------------------------------------------
# Negation guard (DossierGap 2026-04-15 lesson)
# ---------------------------------------------------------------------------


def test_negated_benefit_does_not_fire_benefit():
    # "did not reduce mortality" must NOT be classified as benefit.
    r = classify("9", "Conclusions: The drug did not reduce mortality.")
    assert r.direction != "benefit"
    # It should be picked up as null via the did_not_reduce pattern.
    assert r.direction == "null"


def test_no_reduction_is_not_benefit():
    r = classify("10", "Conclusions: There was no reduction in the risk of the primary outcome.")
    assert r.direction != "benefit"


def test_failed_to_increase_is_not_harm():
    r = classify("11", "Conclusions: The agent failed to increase the incidence of adverse events.")
    assert r.direction != "harm"


# ---------------------------------------------------------------------------
# Conflict -> AMBIGUOUS (never coerced)
# ---------------------------------------------------------------------------


def test_benefit_and_harm_conflict_is_ambiguous():
    r = classify(
        "12",
        "Conclusions: The drug reduced the risk of hospitalization but "
        "increased the risk of bleeding.",
    )
    assert r.direction == AMBIGUOUS


def test_benefit_and_null_conflict_is_ambiguous():
    r = classify(
        "13",
        "Conclusions: Treatment reduced the rate of events, but there was "
        "no significant difference in mortality.",
    )
    assert r.direction == AMBIGUOUS


def test_no_direction_signal_is_ambiguous():
    r = classify("14", "Conclusions: Further large randomized trials are warranted.")
    assert r.direction == AMBIGUOUS


# ---------------------------------------------------------------------------
# Significance dimension
# ---------------------------------------------------------------------------


def test_significance_p_value():
    r = classify("15", "Conclusions: Mortality was reduced (p = 0.01) with the intervention.")
    assert r.significance == "sig"


def test_significance_nonsig_p_value():
    r = classify("16", "Conclusions: The effect on mortality was not clear (p = 0.42).")
    assert r.significance == "nonsig"


def test_significance_not_significant_phrase():
    r = classify("17", "Conclusions: The reduction was not statistically significant.")
    assert r.significance == "nonsig"


def test_significance_unclear_hedged():
    r = classify("18", "Conclusions: The treatment may reduce cardiovascular events.")
    assert r.significance == "unclear"


def test_significance_trend_toward_is_unclear():
    r = classify("19", "Conclusions: There was a trend toward benefit that did not reach significance.")
    # "not ... significan" -> nonsig wins over the unclear trend cue.
    assert r.significance in {"unclear", "nonsig"}


def test_sig_token_inside_no_significant_not_counted_sig():
    # "no significant" must resolve to nonsig, not sig.
    r = classify("20", "Conclusions: There was no significant effect on the primary endpoint.")
    assert r.significance == "nonsig"


# ---------------------------------------------------------------------------
# Confidence buckets
# ---------------------------------------------------------------------------


def test_confidence_all_rules_fired():
    r = classify("21", "Conclusions: The drug significantly reduced the risk of death.")
    assert r.confidence == "ALL_RULES_FIRED"


def test_confidence_partial_direction_only():
    # Direction resolves (benefit) but no significance signal.
    r = classify("22", "Conclusions: The treatment improved quality of life.")
    assert r.direction == "benefit"
    assert r.significance == AMBIGUOUS
    assert r.confidence == "PARTIAL"


def test_confidence_partial_significance_only():
    r = classify("23", "Conclusions: The between-group difference was not statistically significant.")
    # nonsig resolves; direction has a null phrase too, so check confidence is
    # at least PARTIAL (both dims may resolve here).
    assert r.significance == "nonsig"
    assert r.confidence in {"PARTIAL", "ALL_RULES_FIRED"}


def test_confidence_ambiguous_when_nothing_fires():
    r = classify("24", "Conclusions: The evidence base is described below.")
    assert r.confidence == AMBIGUOUS


# ---------------------------------------------------------------------------
# Edge cases and input validation
# ---------------------------------------------------------------------------


def test_empty_abstract_is_fully_ambiguous():
    r = classify("25", "")
    assert r.direction == AMBIGUOUS
    assert r.significance == AMBIGUOUS
    assert r.confidence == AMBIGUOUS
    assert r.conclusion_span == ""


def test_whitespace_abstract_is_fully_ambiguous():
    r = classify("26", "   \n\t  ")
    assert r.confidence == AMBIGUOUS


def test_non_str_abstract_raises_type_error():
    with pytest.raises(TypeError, match="abstract must be str"):
        classify("27", None)  # type: ignore[arg-type]


def test_non_str_pmid_raises_type_error():
    with pytest.raises(TypeError, match="pmid must be str"):
        classify(27, "Conclusions: no difference.")  # type: ignore[arg-type]


def test_output_is_dataclass_with_pmid_echoed():
    r = classify("PMID-XYZ", "Conclusions: no significant difference.")
    assert isinstance(r, ConclusionClassification)
    assert r.pmid == "PMID-XYZ"


def test_rules_fired_is_traceable():
    r = classify("28", "Conclusions: The drug significantly reduced mortality.")
    assert r.rules_fired  # non-empty
    assert all(isinstance(x, str) for x in r.rules_fired)


def test_determinism_same_input_same_output():
    text = "Conclusions: Treatment reduced the risk of stroke (p < 0.001)."
    a = classify("29", text)
    b = classify("29", text)
    assert a == b


def test_lexicon_version_pinned():
    assert LEXICON_VERSION == "v1"


# ---------------------------------------------------------------------------
# Conclusion-span extraction
# ---------------------------------------------------------------------------


def test_extract_structured_conclusion():
    abstract = (
        "BACKGROUND: Heart failure is common. METHODS: We searched databases. "
        "RESULTS: 12 trials were included. CONCLUSIONS: The drug reduced mortality."
    )
    span = extract_conclusion_span(abstract)
    assert "reduced mortality" in span.lower()
    assert "background" not in span.lower()


def test_extract_conclusion_cue_sentence():
    abstract = (
        "We included 8 trials. In conclusion, the intervention lowered the "
        "risk of hospitalization."
    )
    span = extract_conclusion_span(abstract)
    assert "lowered the risk" in span.lower()


def test_extract_falls_back_to_last_sentences():
    abstract = "First sentence here. Second sentence here. The drug reduced events."
    span = extract_conclusion_span(abstract)
    assert "reduced events" in span.lower()


def test_extract_empty_returns_empty():
    assert extract_conclusion_span("") == ""
    assert extract_conclusion_span("   ") == ""


def test_classifier_uses_conclusion_not_background():
    # A "harm" phrase in the BACKGROUND must not flip a benefit conclusion.
    abstract = (
        "BACKGROUND: Untreated disease increases the risk of death. "
        "CONCLUSIONS: The drug significantly reduced mortality."
    )
    r = classify("30", abstract)
    assert r.direction == "benefit"
