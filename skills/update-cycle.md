# Skill: Update Cycle

Fire an indicator and update posteriors through the Bayesian pipeline.

## When to use

- Triage returned INDICATOR_MATCH and you've verified the observable criteria
- A pre-registered indicator's threshold has been met
- You need to apply a Bayesian update with likelihood ratios

## Pipeline call

DO NOT manually edit JSON files. Use the pipeline.

```python
from framework.pipeline import process_evidence, log_activity

# Fire an indicator — likelihoods derived automatically from pre-committed effect
result = process_evidence(
    slug="topic-slug",
    entry={
        "text": "Observable criteria met: [what was observed]",
        "source": "Source Name",
        "tag": "EVENT",
        "tags": ["EVENT"],
    },
    fired_indicator_id="t2_indicator_slug",
    reason="Indicator t2_indicator_slug FIRED: [description]."
)

log_activity(result, platform="update-cycle")

# Check results
print(f"Posteriors before: {result['posteriors_before']}")
print(f"Posteriors after:  {result['posteriors_after']}")
print(f"Governance: {result['governance']['health']}")
```

### Bayesian update without an indicator

If no pre-registered indicator fires but the evidence is informative,
supply likelihoods directly:

```python
result = process_evidence(
    slug="topic-slug",
    entry={
        "text": "Factual description of what happened.",
        "source": "Source Name",
        "tag": "DIPLO",
        "tags": ["DIPLO", "POLICY"],
    },
    likelihoods={"H1": 0.15, "H2": 0.45, "H3": 0.30, "H4": 0.10},
    reason="[Why this evidence is informative and directional assessment]"
)
```

## LR reference table — pick calibrated likelihoods

When supplying `likelihoods` or specifying `lr_range` on an indicator,
resist the urge to pick round numbers. Anchor to this table. These are
posterior-odds multipliers: LR=2 means the evidence doubles the prior
odds for that hypothesis; LR=10 means a ten-fold update.

### Single-value LRs

| LR value | Odds change | Interpretation |
|----------|-------------|----------------|
| 0.1  | divide odds 10× | evidence strongly argues *against* this H — near-falsifying |
| 0.3  | divide odds ~3×  | evidence clearly tilts away from this H |
| 0.7  | divide odds ~1.5× | mild tilt away — barely distinguishable from neutral |
| **1.0**  | **no change**   | **neutral — do not use this indicator to update H** |
| 1.5  | multiply ~1.5×  | mild tilt toward this H |
| 2.0  | double the odds | moderate evidence — "twice as likely if H is true" |
| 4.0  | multiply 4×     | strong evidence — consistent with H being true |
| 10.0 | multiply 10×    | very strong — rarely occurs absent H |
| 20.0 | multiply 20×    | **engine cap (phantom_precision)** — anything higher is blocked |

Rule of thumb: if you'd say *"this evidence is a tiebreaker"*, you mean LR ≈ 1.5–3.
*"This evidence is clear"* → LR ≈ 3–7. *"This is decisive absent other evidence"* → LR ≥ 10.
Claims of LR > 20 need a referenced base rate; you almost never have one.

### LR ranges (for `lr_range` on indicators)

A range `[lo, hi]` represents genuine uncertainty about evidence strength.
Width convention from the spec:

| Range width (ratio hi/lo) | Confidence | When to use |
|---------------------------|------------|-------------|
| hi/lo ≤ 1.3 (narrow)      | HIGH       | reference-class data with known P(E\|H) — set `lr_basis: "reference_class"` and cite `lr_source` |
| hi/lo ~ 2                 | MEDIUM     | literature-derived midpoint, rough interval — set `lr_basis: "literature"` |
| hi/lo ≥ 2× midpoint       | LOW        | expert estimate, no data — `lr_basis: "expert_estimate"`, `lr_source: null` |

**Hard rule**: `lr_confidence: "HIGH"` requires a non-null `lr_source`. The
lint fires `unsupported_lr` otherwise — high confidence with no documented
source is phantom precision.

**Another hard rule**: an `lr_range` with width = 0 (lo == hi) is a point
estimate masquerading as a range. The sensitivity analysis returns
dominance_stable=True trivially. Only use zero-width ranges for
migrated-from-pp indicators, flagged for regrounding.

### Calibration anchors by evidence type

| Evidence example | Typical LR |
|------------------|-----------|
| single anonymous source asserts X | 1.2–1.8 |
| two independent named sources corroborate X | 2.5–4.0 |
| confirmed official statement / primary-source document | 4.0–8.0 |
| physical/observable event (satellite imagery, market data, verified release) | 6.0–15.0 |
| reference-class base rate with n > 30 comparable events | cite, don't guess |

If you catch yourself assigning LR=5 to everything, you're not
calibrating — you're picking a number. Use this table to locate what
the evidence is actually worth, then write the matching LR.

## Prerequisites

- The indicator's observable criteria must be **verified**, not just plausible
- The indicator must be in NOT_FIRED status (the pipeline checks this)
- You must know which indicator ID to fire

## What the pipeline does automatically

1. `add_evidence()` — enrichment, dedup, contradiction detection
2. `fire_indicator()` — marks indicator as FIRED with date and note
3. `suggest_likelihoods()` — derives likelihoods from pre-committed posteriorEffect
4. `bayesian_update()` — mechanical Bayes: P(H|E) = P(E|H)P(H) / sum
5. `snapshot_posteriors()` — records for Brier scoring
6. `auto_calibrate()` — resolves claims, updates source trust
7. `governance_report()` — full epistemic health check
8. `save_topic()` — embeds governance snapshot
9. `propagate_alert()` — checks downstream dependencies

## Constraints

1. **Posteriors computed by Bayes** — the engine computes them, you don't.
2. **Pre-committed effects only** — for indicator fires, the posteriorEffect
   in the indicator definition determines the likelihoods. Do not override.
3. **Evidence coupling** — every update references evidence. The pipeline enforces this.
4. **Governor pre-check** — `check_update_proposal()` runs 14 failure modes.
   If it fails, the update is blocked. Fix the issue, don't bypass.
