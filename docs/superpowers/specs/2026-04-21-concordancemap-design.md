# ConcordanceMap — Design Specification (v1)

> Paper B2 of the "Global Evidence-Synthesis Reliability" trio (Papers A, B, C).
> Hybrid shared-ingest model: this project builds the PubMed SR harvester +
> MeSH/RxNorm taxonomy that Papers A (geographic transportability) and C
> (registry-vs-SR corruption) will inherit.
>
> Status: spec drafted 2026-04-21, awaiting user review. No implementation yet.

## 1. Problem Statement

The meta-analysis literature has been audited for internal methodological quality
(MetaAudit: 29.5% of 6,229 Cochrane pairwise MAs fail >=1 of 11 detectors), for
computational reproducibility (MetaReproducer: only ~17% of 465 Cochrane reviews
have enough OA PDF coverage for pooled re-derivation), and for cross-review
contradictions among MAs with shared primary studies (ContradictionMap: 48.9%
contradict). These measure *within-review* quality or *shared-study* contradictions.

No automated audit exists for a different and arguably more fundamental question:
**when independent research teams conduct systematic reviews on the same clinical
question, do they converge on the same conclusion?** If the evidence-synthesis
ecosystem cannot produce stable answers to identical clinical questions — even
assuming each individual SR is internally valid — then the foundation of
evidence-based medicine is weaker than its practitioners believe. This is the
ecosystem-level instability claim underlying the user's original thesis that
"on a global level, meta-analysis is not accurate."

## 2. Goal

Build a Python pipeline + single-file HTML dashboard that:

1. Ranks the top 1000 drug-indication clinical questions by clinical and
   research importance (AACT trial count x PubMed SR count, geomean ranking).
2. Harvests all post-2010 PubMed-indexed systematic reviews addressing each
   question via NCBI E-utilities (cache-first, API-key rate-limited).
3. Resolves each SR to a canonical PICO cluster (RxNorm drug + MeSH indication
   + outcome class) or REJECTs with typed reason.
4. Classifies each SR's abstract-level conclusion on (direction, significance)
   using a deterministic rule-based classifier with explicit AMBIGUOUS bucket.
5. Measures per-cluster concordance via Fleiss' kappa + pairwise-flip indicators.
6. Calibrates the classifier against a two-labeler human gold sample of 100
   clusters (Rogan-Gladen Bayesian correction).
7. Produces a calibration-corrected primary estimand: fraction of clusters
   with direction-or-significance disagreement, with bootstrap 95% CI.
8. Ships an E156 micro-paper (7 sentences, <=156 words, one primary estimand)
   + interactive dashboard + optional long manuscript.

## 3. Scope

### 3.1 In scope

- Top 1000 drug-indication pairs (AACT x PubMed geomean ranking, pre-registered).
- PubMed-indexed SRs, 2010-2025, English-language, publication type =
  *Systematic Review* OR *Meta-Analysis*.
- Abstract-level conclusion classification (no full-text extraction).
- Deterministic rule-based classifier with AMBIGUOUS bucket.
- Two-labeler human gold sample of 100 clusters.
- Bayesian Rogan-Gladen calibration correction with cluster-level bootstrap 95% CI.
- Per-cluster concordance metrics (Fleiss' kappa + pairwise-flip indicators).
- Drug-specific clustering (primary) and drug-class ATC-4 clustering (sensitivity).
- Single-file HTML dashboard, fully offline, GitHub Pages-deployable.
- E156 micro-paper (7 sentences, <=156 words, single primary estimand).
- TruthCert SHA-256 provenance chain, HMAC key from env var only.
- OSF pre-registration + git tag before full-corpus run.

### 3.2 Out of scope

- Full-text re-extraction or forest-plot extraction (impossible without
  study-level data; SRDR+ sunset blocked this path in preflight).
- Study-level effect-size pooling (that was Paper B1's original scope,
  pivoted away from in brainstorming).
- Grey literature, preprints, non-English SRs (boundary condition in S7
  of E156 body).
- Causal investigation of *why* ecosystems disagree (follow-up research).
- LLM-based classification in the primary path. LLM is available only as
  sensitivity analysis; the portfolio pattern (MetaReproducer) explicitly
  prefers deterministic extraction.
- Cochrane-only or non-Cochrane-only restrictions (we include both in the
  PubMed SR publication-type filter; Cochrane vs non-Cochrane venue is a
  covariate, not an inclusion criterion).
- Re-audit of individual MA internal methodology (MetaAudit's scope).
- Re-audit of cross-MA contradictions requiring shared primary studies
  (ContradictionMap's scope). ConcordanceMap requires only shared PICO.

## 4. Architecture

```
C:\Models\ConcordanceMap\
|-- pipeline/
|   |-- orchestrator.py          # Main entry: full pipeline or single pair
|   |-- corpus_selector.py       # AACT x PubMed geomean ranking -> top 1000
|   |-- ncbi_client.py           # E-utilities: cache, rate-limit, resumable
|   |-- sr_harvester.py          # Per-pair SR ingestion via ncbi_client
|   |-- pico_resolver.py         # MeSH + RxNorm + outcome-class -> cluster
|   |-- conclusion_classifier.py # Rule-based: direction + significance
|   |-- concordance_engine.py    # Fleiss' kappa + pairwise-flip per cluster
|   |-- calibration.py           # Rogan-Gladen + bootstrap CI
|   `-- truthcert.py             # SHA-256 chain, HMAC via env var
|-- data/
|   |-- cache/                   # NCBI response cache (gitignored)
|   |-- reference/               # RxNorm, MeSH, ATC, outcome tables (versioned)
|   |-- gold/                    # Human-labeled calibration (versioned)
|   `-- results/                 # Pipeline outputs (gitignored)
|-- dashboard/
|   `-- index.html               # Single-file interactive dashboard
|-- paper/
|   |-- E156.md                  # 7-sentence <=156-word micro-paper
|   `-- long_manuscript.md       # Optional BMJ Analysis long paper
|-- scripts/
|   |-- rank_pairs.py            # Standalone: rank drug-indication pairs
|   |-- run_audit.py             # Full pipeline end-to-end
|   |-- validate_calibration.py  # Gold-sample regression check
|   `-- publish_dashboard.py     # Freeze results + deploy to Pages
|-- prereg/
|   |-- prereg_frozen.md         # OSF-style pre-registration document
|   `-- prereg_manifest.json     # Hashes of frozen artifacts
|-- tests/
|   |-- test_ncbi_client.py      # VCR cassettes, no live calls
|   |-- test_corpus_selector.py
|   |-- test_pico_resolver.py
|   |-- test_conclusion_classifier.py   # 100+ abstract fixtures
|   |-- test_concordance_engine.py
|   |-- test_calibration.py
|   |-- test_truthcert.py
|   |-- test_orchestrator.py     # Integration, VCR cassettes
|   `-- test_dashboard.py        # Selenium
|-- .gitignore
|-- pyproject.toml
|-- pytest.ini
|-- CLAUDE.md
|-- README.md
|-- STUCK_FAILURES.md            # Sentinel BLOCK log
|-- sentinel-findings.md         # Sentinel WARN log
`-- E156-PROTOCOL.md             # Standard E156 protocol file
```

**Single-responsibility rule:** each module in `pipeline/` has one purpose,
a typed dataclass output, and a unit test file. No module reads another
module's internal state; all communication is via the dataclass interfaces
defined in Section 5. Per the portfolio's MetaReproducer P0-1 lesson,
field-name drift between modules is the #1 silent-failure source; this is
mitigated by contract tests in Section 12.

## 5. Pipeline Modules

### 5.1 corpus_selector.py

**Purpose:** rank drug-indication pairs; produce the frozen top-1000 list.

**Input:**
- AACT snapshot (trial counts per intervention x condition; lowercase
  intervention types per portfolio convention).
- PubMed SR count per (drug, indication) via ESearch on MeSH co-occurrence.

**Output:**
```python
@dataclass
class DrugIndicationPair:
    rxnorm_concept: str       # Canonical RxNorm concept for the drug
    mesh_indication: str      # Canonical MeSH descriptor for the indication
    aact_trial_count: int     # Completed + terminated interventional trials
    pubmed_sr_count: int      # PubMed SRs (pub type filter) 2010-2025
    aact_rank: int            # Descending rank by trial count
    pubmed_rank: int          # Descending rank by SR count
    geomean_rank: float       # sqrt(aact_rank * pubmed_rank); lower = better
    included: bool
    exclusion_reason: str | None
```

**Exclusion criteria (pre-registered, frozen before run):**
- Non-pharmacological interventions.
- Drug combinations without canonical single-drug RxNorm.
- Indications mapping to >3 MeSH descriptors (too broad).
- (drug, indication) pairs with fewer than 5 distinct SRs in PubMed.

**Freeze artifact:** `data/reference/top_1000_pairs.json`, hashed in
`prereg/prereg_manifest.json`. Any post-freeze change requires new version
and an explicit sensitivity analysis comparing v1 vs v2.

### 5.2 ncbi_client.py

**Purpose:** cache-first, rate-limited, resumable NCBI E-utilities wrapper.

**Key design:**
- Token-bucket rate limiter: 10 req/s with API key (env var `NCBI_API_KEY`),
  3 req/s unauthenticated. API key required for production runs;
  unauthenticated only in unit tests.
- Cache key: SHA-256 of normalized request (URL path + sorted params). Cache
  hit returns JSON from disk; miss fires request, writes response.
- Retries: exponential backoff (2s, 4s, 8s, 16s, 32s) up to 5 attempts on
  429/503; then log to `STUCK_FAILURES.jsonl` and continue.
- Resumability: per-pair checkpoint after each pipeline stage. Interrupted
  runs resume from last completed stage.
- **Fail closed** on malformed XML, non-2xx responses, or empty payloads.
  Never treat an error page as valid data (portfolio rule).
- UTF-8 enforced at I/O boundary:
  `io.TextIOWrapper(stdout, encoding='utf-8', errors='replace')`.
- No `2>&1` redirects on native tools (PowerShell lesson — Windows 5.1
  wraps stderr into ErrorRecord).

**Contract:**
```python
def efetch_pubmed(pmids: list[str], cache_dir: Path) -> list[SRRecord]: ...
def esearch_pubmed(query: str, cache_dir: Path) -> list[str]: ...
```

### 5.3 sr_harvester.py

**Purpose:** fetch all PubMed SRs for a given (drug, indication) pair.

**Query pattern:**
```
(MeSH:<drug_descriptor> OR Title/Abstract:<drug_synonyms>)
  AND (MeSH:<indication_descriptor> OR Title/Abstract:<indication_synonyms>)
  AND (PublicationType:"Systematic Review" OR "Meta-Analysis")
  AND ("2010"[DP] : "2025"[DP])
  AND "english"[Language]
```

**Output:**
```python
@dataclass
class SRRecord:
    pmid: str
    title: str
    abstract: str                 # UTF-8 normalized
    mesh_descriptors: list[str]
    publication_year: int
    publication_type: list[str]
    authors: list[str]            # For later duplicate-team detection
    journal: str
    doi: str | None
    fetched_at: str               # ISO8601 UTC
```

Writes to `data/cache/pairs/<pair_id>/srs.jsonl` (one record per line).

### 5.4 pico_resolver.py

**Purpose:** map each SR to a canonical PICO cluster, or REJECT with typed reason.

**Algorithm:**
1. **Drug resolution:** check SR's MeSH descriptors against `rxnorm_mapping.json`.
   - Exactly 1 drug resolves -> use.
   - >=2 distinct drugs, all same ATC-4 class -> retain for drug-class sensitivity
     only; REJECT from primary with reason `multi_drug_class_level`.
   - >=2 distinct drugs, different ATC-4 classes -> REJECT with reason
     `multi_drug_different_classes`.
2. **Indication resolution:** check against `indication_mesh.json`.
   - Exactly 1 indication -> use.
   - >=2 -> REJECT with reason `multi_indication` (typically narrative reviews).
3. **Outcome-class resolution:** parse abstract "outcomes" / "main outcome"
   phrases against `outcome_class_rules.json`.
   - Categories: mortality / hospitalization / MACE / PRO / safety / composite.
   - Unclear -> REJECT with reason `outcome_unmatched`.
4. **Cluster assignment:** `cluster_id = f"{drug}__{indication}__{outcome_class}"`.
   Drug-class cluster id: `f"{atc4}__{indication}__{outcome_class}"`.

**Output:**
```python
@dataclass
class PICOResolution:
    pmid: str
    drug: str | None
    indication: str | None
    outcome_class: str | None
    cluster_id: str | None
    cluster_id_drug_class: str | None  # For ATC-4 sensitivity
    reject_reason: str | None
    mesh_used: list[str]                # Traceability
```

**Reference tables** (pre-registered, versioned in `data/reference/`):
- `rxnorm_mapping.json` — MeSH descriptor -> RxNorm concept
- `indication_mesh.json` — pre-approved indication MeSH descriptors
- `outcome_class_rules.json` — phrase patterns -> outcome class
- `outcome_polarity.json` — outcome class -> {reduce_is_benefit, increase_is_benefit}
- `atc4_mapping.json` — RxNorm concept -> ATC-4 class

**Negated-counts guard** (DossierGap 2026-04-15 lesson): every keyword-preceded
number regex must check preceding 30 chars for negation (`not`, `non`, `never`).
Applied to outcome-phrase regexes so "Not a systematic review" does not match.

**REJECT rate is itself a reportable finding** in Section 16 success criteria
and on the dashboard Overview panel.

### 5.5 conclusion_classifier.py

**Purpose:** deterministic rule-based classifier mapping SR abstract ->
(direction, significance). The hardest module; where most of the review
risk lives.

**Direction classifier** (3 classes + AMBIGUOUS):
- `benefit`: conclusion sentences contain phrases from `benefit_lexicon`
  (e.g., "reduced mortality", "improved outcomes", "significantly lower")
  AND outcome polarity is consistent via `outcome_polarity.json`.
- `harm`: conclusion contains `harm_lexicon` phrases AND outcome polarity
  is consistent.
- `null`: conclusion contains `null_lexicon` phrases (e.g., "no significant
  difference", "did not improve", "similar between groups").
- `AMBIGUOUS`: multiple conflicting classes fire, or no class fires, or
  outcome polarity cannot be resolved.

**Significance classifier** (3 classes + AMBIGUOUS):
- `sig`: "significantly", "P<0.05", "CI excluded null", "P=0.0X".
- `nonsig`: "no significant", "CI crossed null", "P>0.05",
  "not statistically significant".
- `unclear`: hedging ("may", "suggest", "trend toward") without explicit
  significance claim.
- `AMBIGUOUS`: conflicting signals.

**Confidence field:**
- `ALL_RULES_FIRED`: direction + significance + polarity all resolved cleanly.
- `PARTIAL`: one dimension resolved, other AMBIGUOUS.
- `AMBIGUOUS`: both AMBIGUOUS or contradictory.

**Output:**
```python
@dataclass
class ConclusionClassification:
    pmid: str
    direction: str                # benefit | null | harm | AMBIGUOUS
    significance: str             # sig | nonsig | unclear | AMBIGUOUS
    rules_fired: list[str]        # For calibration traceability
    confidence: str               # ALL_RULES_FIRED | PARTIAL | AMBIGUOUS
    conclusion_span: str          # The abstract sentence(s) that matched
```

**Rule lexicon:** version-pinned in `data/reference/classifier_rules_v1.json`,
hashed in prereg_manifest. Any mid-project change -> `_v2.json` + explicit
sensitivity analysis comparing classifications v1 vs v2 on full corpus.

**Gold-sample regression protection** (portfolio rule): any commit to this
file triggers the gold-sample regression check (Section 12); >2%
precision/recall regression blocks merge via Sentinel.

### 5.6 concordance_engine.py

**Purpose:** compute per-cluster concordance metrics.

**Input:** list of (PMID, ConclusionClassification, PICOResolution) for
one cluster.

**Algorithm:**
1. Filter to SRs with `confidence != AMBIGUOUS` for primary; AMBIGUOUS-rate
   computed separately and reported.
2. **Direction concordance:**
   - Fleiss' kappa over direction labels (3 categories: benefit/null/harm),
     validated against `R::irr::kappam.fleiss()` to 1e-6.
   - Pairwise direction flip: true if any pair of SRs has benefit-harm (polar
     opposite).
3. **Significance concordance:**
   - Fleiss' kappa over significance labels (sig/nonsig/unclear).
   - Pairwise significance flip: true if any pair has sig-nonsig.
4. **Discordance flag:**
   `(direction_kappa < 0.6) OR pairwise_direction_flip OR pairwise_significance_flip`.

**Output:**
```python
@dataclass
class ClusterConcordance:
    cluster_id: str
    k_total: int
    k_classifiable: int           # Excluding AMBIGUOUS
    direction_kappa: float | None
    direction_pairwise_flip: bool
    significance_kappa: float | None
    significance_pairwise_flip: bool
    ambiguous_rate: float
    discordant: bool              # Primary-estimand input
```

**Empty-DataFrame guard** (Sentinel P1 rule, 2026-04-15 lesson): all
positional accesses (`.iloc[0]`, `.values[0]`) must follow an explicit
`len() > 0` or `.empty` check.

### 5.7 calibration.py

**Purpose:** apply Rogan-Gladen Bayesian correction using gold-sample-
measured classifier sensitivity and specificity.

**Inputs:**
- Per-class (direction, significance) sensitivity and specificity from
  gold sample.
- Raw delta from `concordance_engine.py`.

**Algorithm:**
1. Compute raw delta from full corpus.
2. For each dimension (direction, significance), compute sens/spec from
   gold vs classifier outputs.
3. Rogan-Gladen correction for proportion with mismeasured binary classifier:

   delta_adj = (delta_raw - (1 - spec)) / (sens + spec - 1)

4. Clamp delta_adj to [0, 1] if the correction pushes outside the
   probability simplex; log the clamp event.
5. Cluster-level bootstrap (1000 iterations, seed=42, resample with
   replacement): recompute delta_adj each iteration, report 95% percentile CI.
6. Sensitivity check: same-polarity-only subset (eliminates polarity-
   lookup errors as a confound).

**Output:**
```python
@dataclass
class CalibratedEstimand:
    delta_raw: float
    delta_adjusted: float
    ci_lower: float
    ci_upper: float
    sensitivity_direction: float
    specificity_direction: float
    sensitivity_significance: float
    specificity_significance: float
    cohen_kappa_labelers: float
    bootstrap_iterations: int
    rng_seed: int
    clamp_events: int             # Count of iterations that hit [0,1] clamp
```

**R validation:** bootstrap CIs compared against `R::boot::boot.ci()` to 1e-6
on 5 synthetic calibration scenarios.

### 5.8 truthcert.py

**Purpose:** SHA-256 provenance chain for every cluster.

**Implementation:**
- HMAC key from env var `CONCORDANCEMAP_HMAC_KEY`. **Never** from the bundle
  itself (2026-04-14 crypto lesson: `cert_id` used as HMAC key = forgeable).
- Fails closed if env var missing. No silent default.
- Constant-time MAC comparison (`hmac.compare_digest`, never `==`).
- Per-cluster chain: hashes (sorted PMID list, PICO resolution dict,
  classifier output dict, concordance result dict).
- Bundle output: `data/results/truthcert/<cluster_id>.json`.
- Delete any legacy weak-key `*_cert.json` artifacts before first real run.

### 5.9 orchestrator.py

```python
def run_audit(
    prereg_manifest: Path,
    top_1000_pairs: Path,
    gold_sample_ids: Path,
    output_dir: Path,
    resumable: bool = True,
) -> AuditReport:
    # 1. Verify prereg manifest hashes match frozen artifacts on disk.
    #    Any hash mismatch halts with PREREG_INTEGRITY_FAIL.
    verify_prereg_integrity(prereg_manifest)

    # 2. For each pair in top_1000_pairs (checkpoint after each):
    #    - harvest SRs via sr_harvester
    #    - resolve PICO via pico_resolver
    #    - classify conclusions via conclusion_classifier
    #    - TruthCert per cluster
    for pair in load_pairs(top_1000_pairs):
        if resumable and is_complete(pair):
            continue
        srs = sr_harvester.harvest(pair)
        picos = [pico_resolver.resolve(sr) for sr in srs]
        clfs  = [conclusion_classifier.classify(sr) for sr in srs]
        truthcert.stamp(pair, srs, picos, clfs)

    # 3. Compute concordance per cluster via concordance_engine.
    clusters = concordance_engine.compute_all(load_all_stamped())

    # 4. Apply calibration from gold sample.
    calibrated = calibration.apply(clusters, gold_sample_ids)

    # 5. Emit results.
    write_summary_json(calibrated, output_dir / "summary.json")
    write_clusters_jsonl(clusters, output_dir / "clusters.jsonl")
    write_pairs_csv(clusters, output_dir / "pairs.csv")

    # 6. Generate dashboard data bundle.
    generate_dashboard_data(calibrated, clusters, output_dir / "dashboard.json")

    return AuditReport(calibrated=calibrated, clusters=clusters)
```

**Resumability:** per-pair checkpoint persists to `data/cache/pairs/<pair_id>/`.
Interrupted runs resume from last completed pair. Target 180-minute wall-clock
for full top-1000 run on warm cache; subprocess timeout 180 min per the
E156-PROTOCOL timeout bump pattern (commit f705ca4).

## 6. Dashboard Design

Single-file HTML (`dashboard/index.html`). No external CDN. All JS (Plotly.js)
and CSS bundled inline or via local `vendor/` directory. CSS vars for
dark/light. Unique localStorage key `concordancemap_v1` (collision-safe
per portfolio rule on variant-specific keys).

**Panels:**

1. **Overview** — headline estimand (delta_adjusted + 95% CI), cluster
   counts by stratum, AMBIGUOUS rate, REJECT rate with breakdown by reason.

2. **Cluster explorer** — searchable/sortable/filterable table: cluster_id,
   drug, indication, outcome class, k_total, k_classifiable, discordant
   flag, direction kappa, significance kappa, AMBIGUOUS rate. Filters:
   discordant-only, therapeutic area (from MeSH ancestor), cluster-size
   band, publication-year range.

3. **Drill-down (lazy-rendered)** — per-cluster: list of SRs with PMID
   links to PubMed, extracted conclusion spans, classifications, rules
   fired per SR. Side-by-side visualization of the cluster's labels.

4. **Calibration panel** — sens/spec per class (direction/significance),
   Cohen's kappa between labelers, bootstrap CI visualization with the
   1000-iteration distribution.

5. **Sensitivity analyses** — toggles for: drug-class-level delta
   (ATC-4), cluster-size strata (3-4, 5-9, 10-19, 20+), publication-year
   windows, therapeutic-area strata, same-polarity-only subset.

**Safety checks** (portfolio HTML rules, Section 7 of C:\Users\user\.claude\rules\rules.md):
- Div balance: `<div[\s>]` vs `</div>` balanced (exclude JS regex from count).
- Script integrity: no literal `</script>` in template literals; use `${'<'}/script>`.
- Function/ID uniqueness across file.
- Event listener cleanup on modal close.
- No unpopulated template tokens (`{{...}}`, `REPLACE_ME`, `__PLACEHOLDER__`).
- No BOM or hardcoded local paths in shipped assets.
- Blob URL revocation after CSV export.

**Standards:** GitHub Pages-ready (index.html in root or /docs), Open Graph
meta tags, dark/light toggle, keyboard-accessible, print stylesheet (A4,
hides UI chrome).

## 7. Data Flow

```
AACT snapshot + PubMed esearch (MeSH co-occurrence)
        |
        v
   corpus_selector.py
        |
        v
data/reference/top_1000_pairs.json  [FROZEN, hashed in prereg_manifest]
        |
        v
   sr_harvester.py (via ncbi_client)
        |
        v
data/cache/pairs/<pair_id>/srs.jsonl
        |
        v
   pico_resolver.py
        |
        v
data/cache/pairs/<pair_id>/pico.jsonl
        |
        v
   conclusion_classifier.py
        |
        v
data/cache/pairs/<pair_id>/classifications.jsonl
        |
        v
   concordance_engine.py
        |
        v
data/results/clusters.jsonl
        |
        v
   calibration.py (uses data/gold/gold_clusters_v1.json)
        |
        v
data/results/summary.json + pairs.csv
        |
        v
   dashboard/index.html (loads summary.json)
        +
   paper/E156.md (placeholders filled from summary.json)
```

## 8. Primary Estimand (formal)

Population: drug-indication PICO clusters `c = (drug, indication, outcome_class)`
where:
- drug is an RxNorm concept pre-registered in top 1000
- indication is a MeSH descriptor pre-registered in top 1000
- outcome_class in {mortality, hospitalization, MACE, PRO, safety, composite}
- The cluster has >=5 PubMed-indexed SRs (pub type = Systematic Review OR
  Meta-Analysis) published 2010-2025, with classifier `confidence != AMBIGUOUS`

Discordance indicator for cluster `c`:

```
D(c) = 1 if (kappa_direction(c) < 0.6)
         OR pairwise_direction_flip(c)
         OR pairwise_significance_flip(c)
     else 0
```

Primary estimand:

```
delta = E_C[D(c)]
```

**Point estimate:** `delta_adj` via Rogan-Gladen correction using gold-sample-
measured (sensitivity, specificity) of the deterministic classifier on
direction and significance dimensions.

**Uncertainty:** 1000-iteration cluster-bootstrap 95% percentile CI
(seed=42, deterministic).

**Reported alongside:**
- Raw delta (uncorrected).
- AMBIGUOUS rate.
- REJECT rate.
- Stratified deltas: drug-class-level, cluster-size bands, year windows,
  therapeutic area, same-polarity-only.

## 9. Calibration Methodology

### 9.1 Gold sample construction

- 100 clusters, stratified by size:
  - 30 clusters with k=3-4 (below primary threshold; included for
    boundary behavior)
  - 40 clusters with k=5-9
  - 20 clusters with k=10-19
  - 10 clusters with k>=20
- Total SRs in gold sample: ~500-1500 (depends on actual k distribution).
- **Labelers:** user + one additional labeler. If a second labeler is
  unavailable, user labels each SR twice with >=2-week washout between
  passes (portfolio pattern).
- **Labels per SR:** (direction, significance, confidence, rationale).
  Rationale is a free-text note naming the abstract sentence that drove
  the label, for later reconciliation.
- **Inter-labeler agreement:** Cohen's kappa reported per dimension. If
  kappa < 0.7 on any dimension, that dimension is re-labeled after a
  calibration discussion between labelers. If disagreement persists after
  re-label, a third labeler breaks ties (or flag as AMBIGUOUS in gold).
- **Freeze:** `data/gold/gold_clusters_v1.json` (cluster IDs + PMIDs,
  no labels) frozen before full-corpus run; labels frozen before
  calibration correction is applied.

### 9.2 Correction

- Sensitivity and specificity computed per class (benefit/null/harm and
  sig/nonsig/unclear) separately.
- Rogan-Gladen:
  `delta_adj = (delta_raw - (1 - specificity)) / (sensitivity + specificity - 1)`
- If `(sensitivity + specificity - 1)` < 0.1 on either dimension, flag
  classifier as insufficient for correction and report raw delta with
  "classifier_unreliable" caveat. (This is a termination condition for
  the paper — the estimand cannot be trusted below that threshold.)
- Clamp to [0, 1] if necessary; count and report clamp events.
- Cluster-level bootstrap (1000 iter, seed=42, resample with replacement,
  recompute delta_adj, get 2.5th/97.5th percentile).

## 10. Pre-registration Commitments

Frozen before full-corpus run; SHA-256 hashes recorded in
`prereg/prereg_manifest.json`; git tag `prereg-v1` marks the frozen commit.

1. AACT snapshot date + PubMed search date + NCBI API version.
2. `data/reference/top_1000_pairs.json`.
3. `data/reference/rxnorm_mapping.json`.
4. `data/reference/indication_mesh.json`.
5. `data/reference/outcome_class_rules.json`.
6. `data/reference/outcome_polarity.json`.
7. `data/reference/atc4_mapping.json`.
8. `data/reference/classifier_rules_v1.json`.
9. `data/gold/gold_clusters_v1.json` (cluster IDs + PMIDs, labels frozen
   after labelers agree but before calibration applied).
10. Concordance thresholds: kappa < 0.6, alpha = 0.05, primary k-threshold = 5.
11. Inclusion filters: pub type, year window (2010-2025), English-language.
12. OSF pre-registration document `prereg/prereg_frozen.md` with hash
    references and submission timestamp.

Any post-freeze change requires:
- New version suffix (`classifier_rules_v2.json`, etc.).
- Explicit sensitivity analysis v1 vs v2 on full corpus.
- Updated prereg_manifest with both hashes.
- Disclosure in paper Methods.

## 11. Error Handling

Six failure classes with explicit policies:

| Class | Policy |
|---|---|
| NCBI rate limit / 429 / 503 | Token-bucket 10 req/s with API key, exponential backoff 2/4/8/16/32s, max 5 retries -> `STUCK_FAILURES.jsonl` |
| NCBI malformed / partial XML / error page | **Fail closed.** PMID excluded, logged with reason. Never treat error page as valid data (portfolio rule). |
| PICO resolution miss | REJECT with typed reason (`drug_unmatched`, `indication_unmatched`, `multi_drug_class_level`, `multi_drug_different_classes`, `multi_indication`, `outcome_unmatched`). REJECT-rate reported. |
| Classifier AMBIGUOUS / contradictory | Explicit AMBIGUOUS bucket, never coerced. Cluster excluded from primary if AMBIGUOUS-rate >= 40%. AMBIGUOUS rate reported per cluster. |
| Cluster too small (k<5 after resolution + classification) | Excluded from primary; reported in sensitivity (k=3-4 stratum). |
| Windows encoding / cp1252 mojibake | UTF-8 enforced at I/O boundary (`io.TextIOWrapper(..., errors='replace')`, encoding lesson). |

**Hardening patterns borrowed from portfolio** (non-negotiable):

- **Negated-counts guard** (DossierGap 2026-04-15): all keyword-preceded
  number regexes check preceding 30 chars for negation (`not`, `non`,
  `never`). Applied to `pico_resolver.py` and `conclusion_classifier.py`.
- **Field-name contract tests** (MetaReproducer P0-1): between
  `pico_resolver.py` -> `conclusion_classifier.py` -> `concordance_engine.py`.
  One test per boundary: build minimal production-shaped input, call
  entrypoint, assert output is NOT a silent-failure sentinel (None, empty
  dict, "unknown_*"). See Section 12.
- **TruthCert HMAC key from env var only** (2026-04-14 crypto lesson).
- **Empty-DataFrame guards** (Sentinel P1 rule, 2026-04-15): all
  `.iloc[0]` / `.values[0]` / `.iloc[-1]` must follow `len() > 0` check.
- **Constant-time MAC compare** (`hmac.compare_digest`).
- **No hardcoded local paths** in shipped assets (dashboard HTML/CSS/JS,
  paper Markdown). Use relative paths or config.

## 12. Testing Strategy

Six tiers:

### 12.1 Unit tests

Per module. Target ~80-120 tests total. Heavy fixtures for
`test_conclusion_classifier.py` (100+ hand-labeled abstract fixtures
covering every rule + edge cases: contradictory signals, partial fires,
outcome-polarity flips, negated counts, hedged language).

- `test_ncbi_client.py`: VCR cassettes only; zero live NCBI calls in CI.
  Tests: rate-limiter enforcement, 429 backoff, malformed-XML rejection,
  cache hit/miss, resumability after interrupt.
- `test_corpus_selector.py`: synthetic AACT + PubMed counts, verify
  geomean ranking deterministic, verify top-N cutoff, verify exclusion
  rules fire.
- `test_pico_resolver.py`: 50+ hand-picked SR abstracts with known PICO.
  Covers cardiology, oncology, infectious disease. Verifies REJECT reasons
  fire correctly. Edges: multi-drug same-class, multi-drug different-
  classes, multi-indication, ambiguous outcome, negated-counts.
- `test_conclusion_classifier.py`: 100+ fixtures per rule, edge cases, and
  gold-sample regression suite.
- `test_concordance_engine.py`: synthetic cluster of known-disagreement
  SRs, verify Fleiss' kappa + flip indicator math. Edges: k=3 boundary,
  all-same, all-different, one-AMBIGUOUS-dropped.
- `test_calibration.py`: synthetic classifier outputs + ground truth,
  verify Rogan-Gladen correction and bootstrap CI. Edge: clamp at [0,1].
- `test_truthcert.py`: SHA-256 chain integrity, determinism across runs,
  missing-env-var failure mode, constant-time compare.

### 12.2 Integration

- `test_orchestrator.py`: end-to-end on 10 hand-picked pairs with frozen
  expected outputs. Runs against **cached NCBI fixtures** (VCR). No live
  calls in CI. Expected runtime <60s.
- `test_smoke.py`: 3 small fixtures, target runtime <120s (portfolio
  preflight rule).

### 12.3 Contract tests (module boundaries)

Per MetaReproducer P0-1 lesson. One contract test per inter-module boundary:

- `test_contract_pico_to_classifier.py`: build minimal `PICOResolution`,
  call `conclusion_classifier.classify()`, assert output is a
  `ConclusionClassification` with expected fields populated.
- `test_contract_classifier_to_concordance.py`: analogous for that boundary.
- `test_contract_concordance_to_calibration.py`: analogous.

Each contract test asserts output is **NOT** a silent-failure sentinel
(None, `{}`, `"unknown_*"`, empty list). Silent failure is worse than
raising.

### 12.4 Gold-set regression

`scripts/validate_calibration.py`: compares current classifier output vs
frozen gold labels. Outputs precision/recall per class. >2%
precision/recall regression on any class blocks merge. Hooked into
Sentinel pre-push.

### 12.5 R validation

Per portfolio's advanced-stats R-validation rule:

- Fleiss' kappa: `R::irr::kappam.fleiss()` to 1e-6 tolerance on 5
  synthetic clusters.
- Bootstrap CIs: `R::boot::boot.ci()` to 1e-6 tolerance on 5 synthetic
  calibration scenarios.

### 12.6 Monte Carlo / stochastic

Per portfolio's Monte Carlo tolerance rule (`atol=0.05` for coverage
simulations, NOT the 1e-6 deterministic tolerance):

- Bootstrap coverage: 1000 synthetic scenarios with known-true discordance
  rate; verify 95% CI achieves 95% +/- 2% coverage (atol=0.05).
- Seed pinned for reproducibility; variance accepted as inherent.

### 12.7 Dashboard tests

Selenium on `dashboard/index.html`:
- Load: summary.json renders overview numbers correctly.
- Filter: discordant-only filter updates table correctly.
- Drill-down: per-cluster panel renders lazy.
- Dark/light toggle, CSV export, print layout.
- Revoke Blob URL after CSV export.
- No console errors on load.

Browser priority: Chrome `--headless=new` (portfolio default); Edge or
Firefox as fallback. Sequential execution (no parallel multi-browser
without isolated ports/profiles). 60s timeout per test.

### 12.8 Sentinel integration

Pre-push hook: Sentinel rules check:
- `P0-js-lockfile-present`, `P0-hardcoded-paths` (no `C:\Users\user\...`
  in shipped dashboard).
- `P1-empty-dataframe-access` (for any `.iloc[0]` / `.values[0]`).
- `P1-placeholder-hmac` (no `SIG_RSA_SHA256_...` or similar placeholders).
- `P1-silent-failure-sentinel` (`return "unknown_*"` patterns).
- `P2-committed-claude-config` (no `.claude/` configs committed).

STUCK_FAILURES.jsonl + sentinel-findings.jsonl per portfolio convention.
SENTINEL_BYPASS=1 logged to `~/.sentinel-logs/bypass.log`; bypass only
with explicit justification. Fix the violation, don't bypass.

## 13. E156 Manuscript

### 13.1 Body (7 sentences, 146 words, single paragraph)

> **S1.** Do independently-constructed systematic reviews on identical
> drug-indication clinical questions converge on the same conclusion,
> or does the synthesis ecosystem disagree with itself?
> **S2.** Top 1000 drug-indication pairs (ranked by AACT trials x PubMed
> SRs) yielded X clusters of >=5 PubMed systematic reviews each, 2010-2025.
> **S3.** Deterministic rule-based classifier mapped each SR abstract to
> (direction, significance); calibration via two-labeler gold sample of
> 100 clusters.
> **S4.** The primary estimand — fraction of clusters with direction-or-
> significance disagreement — was Y% (95% bootstrap CI [Y-lo, Y-hi])
> after calibration correction, with AMBIGUOUS classification rate W%.
> **S5.** Results held under drug-class-level clustering, cluster-size
> strata, publication-year windows, therapeutic-area strata, and a
> same-polarity-only sensitivity.
> **S6.** Independently-conducted SRs on identical clinical questions
> disagree at rates incompatible with a single ground truth, suggesting
> ecosystem-level instability beyond individual-review methodological flaws.
> **S7.** Applies to PubMed-indexed English abstracts 2010-2025; does not
> attempt full-text re-extraction, effect-size pooling, or grey-literature
> coverage.

Fits E156 contract: 7 sentences, 146/156 words, single paragraph, one
primary estimand. Placeholders X, Y, Y-lo, Y-hi, W filled post-run from
`data/results/summary.json`.

### 13.2 Workbook entry

File: `C:\E156\rewrite-workbook.txt` (NON-NEGOTIABLE per portfolio rule).

- **CURRENT BODY**: populated with the final post-run body (X/Y/W filled).
  May be updated until `SUBMITTED: [x]`.
- **YOUR REWRITE**: **blank**. Never touched by the assistant.
- **SUBMITTED**: `[ ]`. Only the user toggles.
- Update total count in workbook header.

### 13.3 Editorial disclosure

Per 2026-04-15 editorial-disclosure lesson: if target venue includes
Synthesis, MA (user) must be listed as middle author (not first/last),
with explicit disclosure of editorial-board membership, statement that
MA had no role in editorial decisions on this manuscript, and confirmation
that handling was done by an independent editor. Use the journal's exact
name with macron: `Synthēsis` (not `Synthesis`). Confirm the exact
spelling against the target journal's current masthead before submission.

### 13.4 Optional long manuscript

BMJ Analysis or JAMA as optional long paper. Decision post-run, out of
scope for this spec. If pursued: cover letter + 3000-4000 word
manuscript + supplement with per-cluster table and interactive dashboard
as appendix link.

## 14. Dependencies

- **Python 3.11+** (NOT 3.13 — WMI deadlock risk on Windows per portfolio
  rule; monkey-patch `platform._wmi_query` before scipy import if 3.13
  is unavoidable).
- **numpy >= 1.24, scipy >= 1.10, pandas >= 2.0**.
- **requests, lxml** (NCBI XML parsing).
- **pytest, pytest-vcr** (NCBI cassettes in tests).
- **Selenium + Chrome** (dashboard tests).
- **R + `boot` + `irr`** (validation only, not in main pipeline).
- **No LLM dependencies** in primary path. LLM-sensitivity analysis (if
  pursued) is a separate script with its own dependencies, gated behind
  an explicit flag.
- **No 2>&1 redirects** on native tools (PowerShell 5.1 lesson).
- **`python` on Windows**, not `python3`.

## 15. Key Risks

| Risk | Mitigation |
|---|---|
| AMBIGUOUS rate too high (>30%) makes headline meaningless | Gold sample probes AMBIGUOUS class specifically; calibration accounts for it; AMBIGUOUS rate is reported not buried. |
| PICO resolution rejects too many SRs (>50%) | Top-1000 filters out pairs with <5 SRs; REJECT-rate reported as finding; sensitivity at drug-class level widens the net. |
| Classifier rule drift during development | Gold-sample regression; >2% precision/recall regression blocks merge; rules version-pinned and hashed in prereg. |
| Temporal structure (2024 "correcting" 2011) confounds discordance | Sensitivity stratifies by publication-year window; reported alongside primary. |
| Cochrane vs non-Cochrane methodological differences confound | Include pub-venue as covariate in sensitivity; reported. |
| PubMed search query misses relevant SRs | MeSH + Title/Abstract synonyms; hand-verified recall on pilot clusters. |
| NCBI rate limits / outages | Cache-first, resumable, API key; STUCK_FAILURES log; exponential backoff. |
| Bootstrap CI narrow due to cluster-level resampling ignoring within-cluster variance | Cluster-level is correct for the estimand (which is over clusters, not SRs); documented. |
| Labeler disagreement high (Cohen's kappa < 0.7) | Re-label after calibration discussion; third labeler for persistent disagreements. |
| Classifier sens+spec-1 < 0.1 on either dimension | Termination condition: paper cannot proceed; report raw delta with "classifier_unreliable" caveat and re-design classifier. |
| SRDR+ being sunset already killed B1 pivot; any future SR corpus change | Pivot playbook documented in this spec's brainstorming record. |

## 16. Success Criteria

Minimum to declare project shippable:

- [ ] Top 1000 pairs frozen + pre-registered before full-corpus run
      (git tag `prereg-v1`, OSF submission timestamp recorded).
- [ ] PICO REJECT rate < 30% on full corpus.
- [ ] AMBIGUOUS rate < 25% on pilot (3 hand-picked clusters).
- [ ] Gold sample: 100 clusters, two-labeler Cohen's kappa >= 0.7 on
      both dimensions.
- [ ] All tests pass: unit + integration + contract + gold-regression +
      R validation + Monte Carlo coverage + dashboard.
- [ ] Calibration-corrected delta + bootstrap 95% CI computed; clamp
      events reported.
- [ ] E156 body written, 7 sentences, <= 156 words, placeholders filled.
- [ ] Dashboard deployed to GitHub Pages (offline, no external CDN).
- [ ] Workbook entry added: `CURRENT BODY` populated, `YOUR REWRITE`
      blank, `SUBMITTED: [ ]`, total count updated.
- [ ] INDEX.md entry added under correct tier.
- [ ] `python C:\ProjectIndex\reconcile_counts.py` passes.
- [ ] TruthCert chain verifiable for every cluster in the full-corpus run.
- [ ] Sentinel pre-push hook passes on final commit.
- [ ] No hardcoded local paths in shipped assets.

## 17. Relation to other portfolio projects

| Project | Relation |
|---|---|
| **MetaAudit** | Detector-based within-MA quality. Distinct from ConcordanceMap (cross-SR concordance). No data overlap. |
| **MetaReproducer** | PDF re-extraction reproducibility. Cochrane-only. Distinct question. Provides architectural template (typed dataclasses, TruthCert pattern). |
| **ContradictionMap** | Cross-MA contradictions among MAs with shared primary studies. ConcordanceMap tests cross-SR concordance without shared-study constraint. Paired nomenclature (Contradiction/Concordance); complementary questions. |
| **FragilityAtlas** | Study-level fragility; within-MA. Distinct. |
| **EvidenceOracle** | ML prediction of MA overturn. Downstream consumer of MetaAudit output; ConcordanceMap output could feed a future version. |
| **Paper A (geographic transportability)** | Shares this project's PubMed SR harvester + PICO taxonomy; adds per-trial geographic data from registry. Planned after Paper B2 ships. |
| **Paper C (registry-vs-SR corruption)** | Shares this project's PubMed SR harvester + PICO taxonomy; adds cross-registry diff and retraction tracker. Planned after Paper B2 ships. |

## 18. Non-goals and Boundary Conditions

Explicit non-goals documented in Section 3.2. The following are
**boundary conditions** noted in S7 of the E156 body and in the paper
Limitations section:

- Coverage is limited to **PubMed-indexed** SRs. Grey literature,
  unindexed preprints, and non-English SRs are outside scope.
- Classification is **abstract-level**. Full-text re-reading might
  reclassify some SRs; the abstract is what most downstream consumers
  (guidelines, clinicians) actually read.
- The estimand is descriptive (how much do SRs disagree), not causal
  (why do SRs disagree). Causal decomposition is follow-up research.
- The primary estimand is over **clusters**, not SRs. A cluster with one
  outlier SR is "discordant" under this definition; this is intentional
  (one outlier is enough to undermine the ecosystem's single-ground-truth
  claim), but the weighting is transparent and a per-SR secondary estimand
  can be reported.
- The classifier is **rule-based and deterministic**. LLM-based
  classification is excluded from the primary path per portfolio pattern.
  An LLM sensitivity analysis is optional post-primary; it is not part of
  the primary estimand.

## 19. Appendix: Estimated timeline (non-binding)

Non-binding sizing for planning discussion only. The implementation plan
(next skill) will produce the authoritative timeline.

| Phase | Modules | Rough effort |
|---|---|---|
| 1. Infrastructure | ncbi_client, truthcert, test harness, CI | 2-3 sessions |
| 2. Corpus + PICO | corpus_selector, sr_harvester, pico_resolver, reference tables | 3-4 sessions |
| 3. Classifier | conclusion_classifier, rule lexicon, 100+ unit fixtures | 4-5 sessions |
| 4. Gold sample | manual labeling (user), inter-labeler reconciliation | 2-3 sessions (human-gated) |
| 5. Concordance + calibration | concordance_engine, calibration, R validation | 2-3 sessions |
| 6. Pre-registration + freeze | OSF doc, prereg_manifest, git tag | 1 session |
| 7. Full-corpus run + dashboard + E156 | orchestrator, dashboard, paper | 2-3 sessions |
| 8. Workbook + INDEX.md + push | Standard portfolio integration | 1 session |

Human-gated items (gold sample labeling, OSF submission) are not
accelerated by parallel agents. The rest are reasonable candidates for
subagent-driven development per the superpowers:subagent-driven-development
skill, but only after the Phase-1 infrastructure is stable.
