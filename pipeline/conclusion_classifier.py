"""Deterministic rule-based conclusion classifier (spec Section 5.5).

Maps a systematic-review abstract to a (direction, significance) label pair
plus a confidence bucket, using a version-pinned lexicon of phrase patterns.
No machine learning, no network, no randomness: the same abstract always
produces the same classification, which is the property the concordance
estimand relies on.

Design contract (mirrors spec Section 5.5):

    direction    in {benefit, null, harm, AMBIGUOUS}
    significance in {sig, nonsig, unclear, AMBIGUOUS}
    confidence   in {ALL_RULES_FIRED, PARTIAL, AMBIGUOUS}

Hardening borrowed from the portfolio (spec Section 11):

  * Negated-counts / negated-phrase guard (DossierGap 2026-04-15 lesson):
    a benefit/harm phrase that is directly negated ("no reduction in
    mortality", "did not reduce") must NOT fire the un-negated class. We
    scan a short window before each phrase match for negation cues.
  * Never coerce AMBIGUOUS away: conflicting signals resolve to AMBIGUOUS,
    they are not silently collapsed to a majority guess.
  * Fail-closed on non-str input at the public entry point.

The lexicon is intentionally conservative. It is meant to fire cleanly on
the ~60-70% of abstracts whose conclusion sentence uses standard synthesis
language, and to abstain (AMBIGUOUS) rather than guess on the rest. The
AMBIGUOUS rate is a reported quantity, not a failure — see spec Section 15.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# Bump this (and add a sensitivity analysis) if any lexicon below changes,
# per the pre-registration commitment in spec Section 10. Kept in code rather
# than an external JSON so the default classifier is self-contained; a future
# plan may externalise it to data/reference/classifier_rules_v2.json.
LEXICON_VERSION = "v1"

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------
# Each entry is (rule_name, compiled_regex). Rule names are emitted in
# ConclusionClassification.rules_fired for calibration traceability.

# An optional short noun phrase (up to two lowercase words) allowed between a
# verb like "reduced" and its outcome noun, so "reduced infection rates" and
# "reduced hospitalization risk" fire, while a long clause does not.
_NP = r"(?:\s+[a-z-]+){0,2}\s+"
_OUTCOME_NOUN = r"(?:risk|rate|rates|incidence|odds|hazard|event|events|mortalit)"

_BENEFIT_PATTERNS: list[tuple[str, str]] = [
    ("benefit.reduced_outcome", rf"reduc\w*(?:\s+the)?{_NP}{_OUTCOME_NOUN}"),
    ("benefit.reduced_mortality", r"reduc\w*\s+(?:all-cause\s+)?mortalit"),
    ("benefit.lower_outcome", rf"lower\w*(?:\s+the)?{_NP}(?:{_OUTCOME_NOUN}|mortalit)"),
    ("benefit.improved", r"improv\w+\s+(?:outcome|survival|function|symptom|quality)"),
    ("benefit.beneficial", r"\bbeneficial\b"),
    ("benefit.effective", r"\b(?:was|were|is|are)\s+effective\b"),
    ("benefit.superior", r"\bsuperior\s+to\b"),
    ("benefit.favou", r"\bfavou?r(?:ed|s|ing)?\s+(?:the\s+)?(?:intervention|treatment|drug)"),
    ("benefit.associated_lower", r"associat\w+\s+with\s+(?:a\s+)?(?:lower|reduced|decreased)"),
    ("benefit.decreased_outcome", rf"decreas\w*(?:\s+the)?{_NP}(?:{_OUTCOME_NOUN}|mortalit)"),
    ("benefit.less_frequent", rf"{_OUTCOME_NOUN}\s+(?:were|was)\s+less\s+(?:frequent|common)"),
]

_HARM_PATTERNS: list[tuple[str, str]] = [
    ("harm.increased_outcome", rf"increas\w*(?:\s+the)?{_NP}(?:{_OUTCOME_NOUN}|mortalit)"),
    ("harm.higher_outcome", rf"higher{_NP}(?:{_OUTCOME_NOUN}|mortalit)"),
    ("harm.associated_higher", r"associat\w+\s+with\s+(?:a\s+)?(?:higher|increased|greater)\s+(?:risk|rate|odds|mortalit)"),
    ("harm.harmful", r"\bharmful\b"),
    ("harm.worse", r"\bworse(?:ned)?\s+(?:outcome|survival|prognosis)"),
    ("harm.inferior", r"\binferior\s+to\b"),
    ("harm.adverse_increase", r"(?:more|greater|increased)\s+adverse\s+events"),
    ("harm.more_frequent", rf"(?:adverse\s+events?|{_OUTCOME_NOUN})\s+(?:were|was)\s+more\s+(?:frequent|common)"),
]

_NULL_PATTERNS: list[tuple[str, str]] = [
    ("null.no_significant_difference", r"no\s+(?:statistically\s+)?significant\s+difference"),
    ("null.no_difference", r"no\s+(?:clear\s+|apparent\s+|meaningful\s+)?difference"),
    ("null.no_effect", r"no\s+(?:significant\s+|clear\s+|meaningful\s+)?(?:effect|benefit|association)"),
    ("null.did_not_improve", r"did\s+not\s+(?:significantly\s+)?(?:improve|reduce|increase|affect|change)"),
    ("null.similar", r"\bsimilar\s+(?:between|across|in\s+both)"),
    ("null.no_evidence", r"no\s+evidence\s+(?:of|for|that)"),
    ("null.comparable", r"\bcomparable\s+(?:between|across|efficacy|outcomes)"),
    ("null.insufficient", r"insufficient\s+evidence"),
]

# Significance dimension --------------------------------------------------

_SIG_PATTERNS: list[tuple[str, str]] = [
    ("sig.significantly", r"\bsignificantly\b"),
    ("sig.statistically_significant", r"\bstatistically\s+significant\b"),
    ("sig.ci_excluded", r"confidence\s+interval\s+(?:excluded|did\s+not\s+(?:cross|include))"),
    # p-values are handled numerically in _pvalue_significance, not by regex,
    # because "p = 0.42" and "p < 0.001" cannot be distinguished by a phrase
    # match alone (see _pvalue_significance).
]

_NONSIG_PATTERNS: list[tuple[str, str]] = [
    ("nonsig.not_significant", r"\bnot\s+(?:statistically\s+)?significant\b"),
    ("nonsig.no_significant", r"\bno\s+(?:statistically\s+)?significant\b"),
    ("nonsig.ci_crossed", r"confidence\s+interval\s+(?:crossed|included|contained)\s+(?:the\s+)?(?:null|unity|1|one)"),
]

# Alpha threshold for significance. Pre-registered at 0.05 (spec Section 10,
# item 10). p < alpha => sig; p >= alpha => nonsig.
_ALPHA = 0.05

# Capture a p-value comparison: the operator and the numeric literal, so we
# can compare the actual number against alpha rather than pattern-guessing.
_PVALUE_RE = re.compile(
    r"\bp\s*(?P<op>[<>=]=?|<|>)\s*(?P<val>0?\.\d+|\d*\.?\d+(?:e-?\d+)?)",
    re.IGNORECASE,
)

_UNCLEAR_PATTERNS: list[tuple[str, str]] = [
    ("unclear.may", r"\bmay\s+(?:reduce|improve|increase|benefit|lower|raise)\b"),
    ("unclear.suggest", r"\bsuggest(?:s|ed|ing)?\s+(?:a\s+)?(?:possible|potential|trend)"),
    ("unclear.trend", r"\btrend\s+(?:toward|towards|for)\b"),
    ("unclear.might", r"\bmight\s+(?:reduce|improve|increase|benefit)\b"),
    ("unclear.uncertain", r"\b(?:uncertain|inconclusive)\b"),
]

# Negation cues scanned in the window BEFORE a benefit/harm phrase. If any
# fires within _NEGATION_WINDOW characters immediately preceding the match,
# the phrase is treated as negated and does not fire its class.
_NEGATION_WINDOW = 30
_NEGATION_CUES = re.compile(
    r"\b(?:no|not|non|never|without|neither|nor|fail(?:ed|s)?\s+to|"
    r"did\s+not|does\s+not|was\s+not|were\s+not|lack(?:ed|ing|s)?\s+of?)\b",
    re.IGNORECASE,
)


def _compile(patterns: list[tuple[str, str]]) -> list[tuple[str, re.Pattern[str]]]:
    return [(name, re.compile(pat, re.IGNORECASE)) for name, pat in patterns]


_BENEFIT = _compile(_BENEFIT_PATTERNS)
_HARM = _compile(_HARM_PATTERNS)
_NULL = _compile(_NULL_PATTERNS)
_SIG = _compile(_SIG_PATTERNS)
_NONSIG = _compile(_NONSIG_PATTERNS)
_UNCLEAR = _compile(_UNCLEAR_PATTERNS)


# ---------------------------------------------------------------------------
# Output dataclass (spec Section 5.5)
# ---------------------------------------------------------------------------


@dataclass
class ConclusionClassification:
    """Classifier output for one SR abstract.

    Field names are a contract with concordance_engine.py; do not rename
    without updating that module and the contract test (MetaReproducer
    P0-1 lesson).
    """

    pmid: str
    direction: str  # benefit | null | harm | AMBIGUOUS
    significance: str  # sig | nonsig | unclear | AMBIGUOUS
    rules_fired: list[str] = field(default_factory=list)
    confidence: str = "AMBIGUOUS"  # ALL_RULES_FIRED | PARTIAL | AMBIGUOUS
    conclusion_span: str = ""


# Sentinel constants so callers/tests never rely on bare strings.
AMBIGUOUS = "AMBIGUOUS"
_DIRECTION_CLASSES = ("benefit", "null", "harm")
_SIGNIFICANCE_CLASSES = ("sig", "nonsig", "unclear")


# ---------------------------------------------------------------------------
# Conclusion-span extraction
# ---------------------------------------------------------------------------

# Prefer the sentence(s) most likely to carry the review's bottom line.
_CONCLUSION_CUE = re.compile(
    r"(?:in\s+conclusion|we\s+conclude|conclusions?\s*[:\-]|"
    r"these\s+(?:results|findings)\s+(?:suggest|indicate|show)|"
    r"our\s+(?:results|findings|meta-analysis)\s+(?:suggest|indicate|show|demonstrate))",
    re.IGNORECASE,
)

# Structured-abstract "CONCLUSIONS:" header (PubMed AbstractText Label).
_STRUCTURED_CONCLUSION = re.compile(
    r"conclusions?\s*[:\-]\s*(.+?)(?=(?:[A-Z][a-z]+(?:\s+[A-Za-z]+){0,3}\s*[:])|$)",
    re.IGNORECASE | re.DOTALL,
)


def _split_sentences(text: str) -> list[str]:
    # Lightweight sentence split; abstracts rarely need a full NLP tokenizer
    # and we deliberately avoid heavyweight deps. Split on ., ?, ! followed
    # by whitespace + capital/paren, keeping it deterministic.
    parts = re.split(r"(?<=[.?!])\s+(?=[A-Z(\"'])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def extract_conclusion_span(abstract: str) -> str:
    """Return the sentence(s) most likely to state the review's conclusion.

    Falls back to the last two sentences of the abstract when no explicit
    conclusion cue is present. Returns "" for empty input.
    """
    if not abstract or not abstract.strip():
        return ""

    m = _STRUCTURED_CONCLUSION.search(abstract)
    if m and m.group(1).strip():
        return m.group(1).strip()

    sentences = _split_sentences(abstract)
    if not sentences:
        return abstract.strip()

    # Find the first sentence containing an explicit conclusion cue and take
    # it plus the remainder (bounded to 3 sentences to stay conclusion-focused).
    for i, sent in enumerate(sentences):
        if _CONCLUSION_CUE.search(sent):
            return " ".join(sentences[i : i + 3]).strip()

    # No cue: heuristically the last two sentences carry the takeaway.
    return " ".join(sentences[-2:]).strip()


# ---------------------------------------------------------------------------
# Firing logic with negation guard
# ---------------------------------------------------------------------------


def _fires_unnegated(
    text: str, patterns: list[tuple[str, re.Pattern[str]]]
) -> list[str]:
    """Return names of patterns that match AND are not directly negated.

    A match is treated as negated if a negation cue appears within
    _NEGATION_WINDOW characters immediately before the match start.
    """
    fired: list[str] = []
    for name, pat in patterns:
        for m in pat.finditer(text):
            window_start = max(0, m.start() - _NEGATION_WINDOW)
            preceding = text[window_start : m.start()]
            if _NEGATION_CUES.search(preceding):
                continue  # negated — skip this occurrence
            fired.append(name)
            break  # one clean fire per pattern is enough
    return fired


def _fires_plain(
    text: str, patterns: list[tuple[str, re.Pattern[str]]]
) -> list[str]:
    """Return names of patterns that match (no negation guard).

    Used for the null and significance lexicons, whose phrases already
    encode negation semantically ("no significant difference").
    """
    fired: list[str] = []
    for name, pat in patterns:
        if pat.search(text):
            fired.append(name)
    return fired


def _resolve_direction(span: str) -> tuple[str, list[str]]:
    benefit_hits = _fires_unnegated(span, _BENEFIT)
    harm_hits = _fires_unnegated(span, _HARM)
    null_hits = _fires_plain(span, _NULL)

    active = []
    if benefit_hits:
        active.append("benefit")
    if harm_hits:
        active.append("harm")
    if null_hits:
        active.append("null")

    fired = benefit_hits + harm_hits + null_hits

    # A null signal co-occurring with a benefit/harm signal is a genuine
    # conflict (e.g. "reduced events but no significant difference in
    # mortality"): abstain rather than guess.
    if len(active) == 1:
        return active[0], fired
    return AMBIGUOUS, fired


def _pvalue_significance(span: str) -> tuple[list[str], list[str]]:
    """Classify explicit p-value tokens numerically against alpha.

    Returns (sig_rule_names, nonsig_rule_names). A "p < 0.03" contributes a
    sig rule; "p = 0.42" or "p > 0.05" contributes a nonsig rule. Equality
    exactly at alpha ("p = 0.05") is treated as non-significant (>= alpha).
    Malformed or unparseable numbers are ignored (no rule fired), so a stray
    token never crashes the classifier.
    """
    sig: list[str] = []
    nonsig: list[str] = []
    for m in _PVALUE_RE.finditer(span):
        op = m.group("op")
        try:
            val = float(m.group("val"))
        except ValueError:
            continue
        # "p < X" or "p <= X": significant only if X <= alpha (the ceiling is
        # at or below alpha). "p > X"/"p >= X": non-significant if X >= alpha.
        # "p = X": compare X directly.
        if op in ("<", "<="):
            (sig if val <= _ALPHA else nonsig).append("sig.p_value_lt" if val <= _ALPHA else "nonsig.p_value_upper")
        elif op in (">", ">="):
            (nonsig if val >= _ALPHA else sig).append("nonsig.p_value_ge" if val >= _ALPHA else "sig.p_value_lower")
        else:  # "=" or "=="
            (sig if val < _ALPHA else nonsig).append("sig.p_value_eq" if val < _ALPHA else "nonsig.p_value_eq")
    return sig, nonsig


def _resolve_significance(span: str) -> tuple[str, list[str]]:
    sig_hits = _fires_plain(span, _SIG)
    nonsig_hits = _fires_plain(span, _NONSIG)
    unclear_hits = _fires_plain(span, _UNCLEAR)

    p_sig, p_nonsig = _pvalue_significance(span)
    sig_hits = sig_hits + p_sig
    nonsig_hits = nonsig_hits + p_nonsig

    # "not significant" / "no significant" are matched by _NONSIG but the bare
    # token "significant" inside them must not also count as sig. Guard: if a
    # nonsig phrase fired, drop any sig hit that is the generic "significantly"
    # / "statistically significant" token (those are the ones that overlap).
    if nonsig_hits:
        sig_hits = [
            h
            for h in sig_hits
            if h not in ("sig.significantly", "sig.statistically_significant")
        ]

    active = []
    if sig_hits:
        active.append("sig")
    if nonsig_hits:
        active.append("nonsig")

    fired = sig_hits + nonsig_hits + unclear_hits

    if len(active) == 1:
        return active[0], fired
    if not active and unclear_hits:
        return "unclear", fired
    # Both sig and nonsig, or nothing decisive -> AMBIGUOUS.
    return AMBIGUOUS, fired


def _resolve_confidence(direction: str, significance: str) -> str:
    d_ok = direction in _DIRECTION_CLASSES
    s_ok = significance in _SIGNIFICANCE_CLASSES
    if d_ok and s_ok:
        return "ALL_RULES_FIRED"
    if d_ok or s_ok:
        return "PARTIAL"
    return AMBIGUOUS


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify(pmid: str, abstract: str) -> ConclusionClassification:
    """Classify one SR abstract into (direction, significance) + confidence.

    Args:
        pmid: the record identifier (echoed into the output for traceability).
        abstract: the SR abstract text. May be a full structured abstract;
            the conclusion span is extracted internally.

    Returns:
        ConclusionClassification. Empty/whitespace abstracts return a fully
        AMBIGUOUS result rather than raising, so a missing-abstract SR does
        not crash a batch — it is counted in the AMBIGUOUS rate.

    Raises:
        TypeError: if pmid or abstract is not a str (fail closed at the public
            boundary rather than silently coercing None into "None").
    """
    if not isinstance(pmid, str):
        raise TypeError(f"pmid must be str, got {type(pmid).__name__}")
    if not isinstance(abstract, str):
        raise TypeError(f"abstract must be str, got {type(abstract).__name__}")

    span = extract_conclusion_span(abstract)
    if not span:
        return ConclusionClassification(
            pmid=pmid,
            direction=AMBIGUOUS,
            significance=AMBIGUOUS,
            rules_fired=[],
            confidence=AMBIGUOUS,
            conclusion_span="",
        )

    direction, dir_rules = _resolve_direction(span)
    significance, sig_rules = _resolve_significance(span)
    confidence = _resolve_confidence(direction, significance)

    return ConclusionClassification(
        pmid=pmid,
        direction=direction,
        significance=significance,
        rules_fired=dir_rules + sig_rules,
        confidence=confidence,
        conclusion_span=span,
    )
