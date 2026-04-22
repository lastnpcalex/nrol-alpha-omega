# Spec: LR Ranges + Sensitivity Analysis
**Status:** DRAFT — pending red-team  
**Scope:** `engine.py`, `governor.py`, `framework/lint.py`, topic JSON schema,
`skills/topic-design.md`, `skills/governance.md`  
**Builds on:** `lr-migration-spec.md` (point LRs → range LRs)

---

## Problem

Point LRs (`"H3": 4.0`) assert a precise likelihood ratio without grounding.
For novel geopolitical topics with no close reference class, this is phantom
precision — a number that *looks* calibrated but is actually a guess.
The framework should make epistemic uncertainty about evidence strength
explicit, propagate it through the update, and flag conclusions that depend
on that uncertainty.

---

## Core concept: credal LR range

Instead of a single LR per hypothesis per indicator, specify an interval:

```json
"lr_range": {
  "H1": [0.10, 0.30],
  "H2": [0.60, 1.00],
  "H3": [1.50, 4.00],
  "H4": [1.20, 3.00]
}
```

Each `[lo, hi]` represents genuine uncertainty about the evidence strength
under that hypothesis. A range of [1.0, 1.0] means "neutral — no update."
A range of [0.1, 4.0] means "directional but highly uncertain magnitude."

Every indicator additionally carries:
```json
"lr_basis": "reference_class" | "literature" | "expert_estimate" | "converted_pp",
"lr_source": "SIPRI 2024 naval blockade dataset",  // or null
"lr_confidence": "HIGH" | "MEDIUM" | "LOW"
```

---

## Change 1 — Topic design workflow (new mandatory steps)

The `skills/topic-design.md` skill gains a Phase 0 before indicator design:

**Phase 0A — Reference class search**  
Find N analogous historical events. Document:
- What fraction reached each hypothesis outcome?
- What was the base rate for each indicator observable?
- Source and year of data.

**Phase 0B — Literature LR derivation**  
For each indicator: does published data support P(E|H_i)?
If yes: compute LR from frequency data, set `lr_basis: "reference_class"` or
`"literature"`, record `lr_source`.

**Phase 0C — Expert estimate ranges**  
For indicators with no data: elicit a range from first principles.
Rule: range width must be at least 2× the midpoint for `lr_confidence: LOW`.
(e.g., midpoint LR=3.0 → range must span at least [1.5, 6.0]).
Set `lr_basis: "expert_estimate"`, `lr_source: null`.

**Phase 0D — Sensitivity pre-check**  
Before finalizing indicators, run a sensitivity pre-check:
does the conclusion (which H dominates) change across the full LR range?
If yes: either narrow the ranges (get better data) or flag the topic as
`sensitivity_class: HIGH` — meaning it should not be cited as confident.

---

## Change 2 — Engine: dual-pass bayesian_update

`bayesian_update()` gains an optional `lr_range` parameter. When supplied:

1. **Lower-bound pass:** use `lo` values for each hypothesis, normalized to max=1.0
2. **Upper-bound pass:** use `hi` values, normalized to max=1.0
3. **Point pass:** use geometric mean `sqrt(lo * hi)` per hypothesis — this
   is the "live" posterior stored on the topic
4. Return all three in the result dict

```python
bayesian_update(topic, likelihoods=None, lr_range=None, ...)
# If lr_range supplied: runs 3 passes, stores point posterior,
#   records [lo_posteriors, hi_posteriors] in posteriorHistory entry
# If likelihoods supplied (legacy/point): single pass as before
```

**Stored posterior:** geometric mean (point estimate). Not the midpoint —
geometric mean is more appropriate for multiplicative LR quantities.

**posteriorHistory entry gains:**
```json
{
  "posteriors": {"H1": 0.05, "H2": 0.34, "H3": 0.38, "H4": 0.23},
  "posteriorRangeLo": {"H1": 0.02, "H2": 0.28, "H3": 0.31, "H4": 0.19},
  "posteriorRangeHi": {"H1": 0.09, "H2": 0.41, "H3": 0.48, "H4": 0.28},
  "sensitivityFlag": false,
  "dominantHypothesisStable": true
}
```

---

## Change 3 — Governor sensitivity check

`check_update_proposal()` gains a `sensitivity_analysis` block run whenever
`lr_range` data is available in the update:

**Check A — Dominant hypothesis stability**  
Does the same hypothesis have the highest posterior at both lo and hi bounds?
- Yes → `dominant_hypothesis_stable: true` — conclusion is robust
- No → `conclusion_sensitive_to_lr_uncertainty` → WARNING (not hard block,
  since sensitive conclusions are informative, just flagged)

**Check B — Range width**  
For the dominant hypothesis: `posterior_hi - posterior_lo > 0.20`?
- Yes → `wide_uncertainty` WARNING — "conclusion depends on LR estimates,
  width exceeds 20pp"

**Check C — Sensitivity class escalation**  
If `dominant_hypothesis_stable: false` AND topic is ALERT classification:
→ hard block (`passed: False`). An ALERT-level topic with a conclusion that
flips across the LR range cannot be used to drive decisions.

**New lint failure mode:**  
`unsupported_lr`: any indicator with `lr_basis: "converted_pp"` that fires
with a range width < 0.5 (i.e., was converted to a near-point estimate)
→ WARNING. These should be regrounded.

---

## Change 4 — Red-team integration

`generate_red_team()` in `framework/red_team.py` gains an adversarial
sensitivity pass:

For each hypothesis being elevated by the update, run the update at the
adversarial end of the LR range — the bound that minimizes the winning
hypothesis's posterior. Report:
- "At adversarial LR bound, H3 drops from 0.38 to 0.19 — H2 becomes dominant"
- Devil's advocate score now incorporates range width: wider = higher DA score

---

## Change 5 — Migration path

Existing topics migrated by `framework/migrate_to_lr.py` get:
```json
"lr_range": {"H1": [lr, lr], "H2": [lr, lr], ...},  // lo=hi=point (zero width)
"lr_basis": "converted_pp",
"lr_confidence": "LOW"
```

Zero-width ranges produce `sensitivityFlag: false` trivially (a point has no
sensitivity). These are explicitly marked for regrounding. The `unsupported_lr`
lint warning fires on every firing of a `converted_pp` indicator, nudging
operators toward proper grounding.

---

## Amendments from red-team

### A1 — apply_indicator_effect must read lr_range [CRITICAL]
The spec omitted describing how `apply_indicator_effect()` passes `lr_range`
to `bayesian_update()`. Without this, lr_range on indicators is never used
in the live update path. Required change (Change 2b):

In `apply_indicator_effect()` Path 2, when indicator has `lr_range`:
1. Apply `lr_decay^n_firings` independently to lo and hi bounds of each
   hypothesis's range.
2. Normalize lo and hi independently (each to their own max=1.0).
3. Pass the decayed, normalized `lr_range` dict to `bayesian_update()`.
4. Point estimate uses post-Bayes geometric mean (see A2).

### A2 — Point estimate is post-Bayes geometric mean, not pre-Bayes [CRITICAL]
Using `sqrt(lo * hi)` as a pre-Bayes LR point is not equivalent to
`sqrt(Bayes(lo) * Bayes(hi))`. Due to normalization, the two diverge on
asymmetric ranges with competing hypotheses near parity. The correct
procedure:
1. Run Bayes at lo bounds → `posteriors_lo`
2. Run Bayes at hi bounds → `posteriors_hi`
3. Point estimate: `sqrt(posteriors_lo[k] * posteriors_hi[k])` per hypothesis,
   renormalized to sum to 1.0.
The engine runs exactly 2 Bayes passes; the point estimate is derived from
their outputs, not from the LR inputs.

### A3 — Range explosion cap [SERIOUS]
Independent-bounds multiplication across N firings produces [lo^N, hi^N]
before normalization — guaranteed interval explosion. Mitigation: after
computing the combined `[lo, hi]` for a firing, if
`(hi - lo) / geometric_mean(lo, hi) > MAX_RELATIVE_WIDTH` (default 2.0),
clamp by shrinking symmetrically in log-space toward the geometric mean.
Log-space width is capped at `log(MAX_LR_RATIO)` per firing (same as the
pre-normalization phantom_precision cap, LR ratio ≤ 20). This prevents
intervals from becoming uninformative noise after multiple firings.

### A4 — lr_confidence wired to governor [SERIOUS]
`lr_confidence: LOW` must escalate behavior, not just label it:
- `LOW` + `wide_uncertainty` (width > 0.20) → hard WARNING, surfaces in
  governance health score
- `LOW` + `dominant_hypothesis_stable: false` → escalate to CRITICAL
  regardless of topic classification (not just ALERT topics)
- `HIGH` + `lr_source: null` → `unsupported_lr` lint failure (claiming
  high confidence with no documented source is a contradiction)

### A5 — Sensitivity instability propagates to dependency graph [SERIOUS]
When `dominantHypothesisStable: false` after an update:
1. Call `propagate_alert()` to all downstream dependent topics, flagging
   the edge as `stale_edge` with reason `upstream_sensitivity_unstable`.
2. Include `sensitivityFlag: true` in the governance health score's issue
   list — it contributes to DEGRADED health regardless of classification.
Point estimate still flows to consumers (blocking all dependent reads would
be too disruptive), but instability is visible at every consumption point.

### A6 — expert_estimate with null source flagged by lint [MINOR]
Add lint check: `lr_basis: "expert_estimate"` + `lr_source: null` +
`lr_confidence: "HIGH"` → `unsupported_lr` WARNING. Claiming high-confidence
expert estimates with no documented source is a phantom-precision smell.

---

## What does NOT change

- The 0.98 ceiling clamp and floor guard remain
- `lr_decay` per-indicator and `n_firings` logic remain
- Deadline elimination remains
- `backfill.py` calibration scoring uses point posteriors only
- Canvas display uses point posteriors (interval display is out of scope)

---

## Risks not yet resolved

1. **Operator LR range calibration** — specifying a range [1.5, 4.0] is
   still a guess. Needs a reference table in `skills/update-cycle.md`:
   "LR=2 doubles the odds; LR=10 means strong discrimination; width=2×
   midpoint means genuinely uncertain."

2. **Joint vs marginal LR ranges across indicators** — the range explosion
   cap (A3) is a pragmatic bound, not a theoretically correct treatment of
   correlated evidence. For topics where multiple indicators are expected to
   fire together (correlated by the same underlying event), the effective
   combined range should be narrower than independent multiplication.
   Documented limitation; no fix in scope.
