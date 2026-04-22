# Spec: Likelihood Ratio Migration
**Status:** DRAFT — pending final red-team  
**Scope:** `engine.py`, `governor.py`, `framework/lint.py`, all topic JSONs  
**Problem:** Absolute pp-shifts (`H1 +20pp`) apply identically regardless of current
posterior. A topic at H1=0.85 gets the same +20pp as one at H1=0.30. Combined with
no per-indicator firing limits, posteriors converge to 90%+ through normal operation.

---

## Root cause (confirmed)

`_pp_shifts_to_likelihoods()` (`engine.py:1236-1272`) derives implied LRs from a
fixed target posterior (`prior + delta`), not from the actual prior at update time.
As posteriors drift from design priors, the effective LR changes unpredictably.
`bayesian_update()` already accepts a `likelihoods: dict[str, float]` parameter
directly — the pp path is a conversion layer sitting in front of it.

---

## Change 1 — Native LR expressions replace pp-shifts

**What changes:**  
Indicator `posteriorEffect` field migrates from free-text pp strings
(`"H1 +20pp, H2 -10pp"`) to a structured `likelihoods` dict, normalized to max=1.0:

```json
{
  "id": "t2_fed_dovish_pivot",
  "tier": 2,
  "likelihoods": {"H1_HOLD": 0.4, "H2_CUT": 1.0, "H3_HIKE": 0.1},
  "resolution_class": false,
  "n_firings": 0,
  "lr_decay": 0.65
}
```

**How updates work:**  
`bayesian_update(topic, likelihoods=indicator.likelihoods, ...)` — the pp conversion
layer (`_pp_shifts_to_likelihoods`) is removed from the live update path.

**Self-dampening property:**  
Same `LR=4.0` on H1=0.30 → H1≈0.63 (+33pp).  
Same `LR=4.0` on H1=0.85 → H1≈0.95 (+10pp).  
Same `LR=4.0` on H1=0.95 → H1≈0.986 (+3.6pp).  
No ceiling games required — the math handles it.

---

## Change 2 — LR decay on repeated firings

**What changes:**  
Each indicator tracks `n_firings`. Effective LR is discounted on each re-fire:

```
LR_effective(k) = LR_base(k) × lr_decay ^ n_firings
```

Default `lr_decay` by tier:

| Tier | Default decay | Rationale |
|------|--------------|-----------|
| T1   | 0.70         | Resolution-class events shouldn't compound |
| T2   | 0.65         | Strong directional — diminishing returns |
| T3   | 0.50         | Informational — rapidly stale |

`lr_decay` is configurable per indicator. After 4 firings at decay=0.65:
`LR_effective = LR_base × 0.179` — effectively noise.

**Where it lives:**  
`engine.py` — in `apply_indicator_effect()` before calling `bayesian_update()`.
`n_firings` incremented atomically after a confirmed update. Reset on topic reset only.

---

## Change 3 — Resolution-class absolute_override

**What changes:**  
Indicators marked `"resolution_class": true` bypass LR math entirely.
They specify target posteriors directly:

```json
{
  "id": "t1_ceasefire_signed",
  "resolution_class": true,
  "target_posteriors": {"H1_SIGNED": 0.97, "H2_FROZEN": 0.02, "H3_COLLAPSED": 0.01},
  "lr_decay": 1.0,
  "n_firings": 0
}
```

**Gate (enforced in `check_update_proposal()`):**
- `tier` must be 1
- Source trust ≥ 0.90
- `provenance` must be set by `update_feed()` pipeline step — rejected in `add_evidence()`
  if set to `OBSERVED` via free text (closes the provenance gaming vector from red-team round 2)
- Can only fire once (`n_firings == 0` check before apply)

**Rationale:** "Ceasefire bilaterally signed" IS near-certainty regardless of prior.
Self-dampening is wrong for resolution events. This is the explicit escape hatch.

---

## Change 4 — Floor guard inside bayesian_update()

**What changes:**  
Add `max(0.005)` floor to each hypothesis *after* multiplication and *before*
normalization in `bayesian_update()`, independent of input format:

```python
raw = {k: max(0.005, priors[k] * likelihoods.get(k, 1.0)) for k in priors}
total = sum(raw.values())
posteriors = {k: v / total for k, v in raw.items()}
```

**Rationale:** LR self-dampening is symmetric — floor pileup mirrors ceiling pileup.
A hypothesis at 0.0067 with LR=0.2 hits machine-epsilon without this guard.

---

## Change 5 — LR sanity cap in governor

**What changes:**  
`check_update_proposal()` adds a pre-commit LR inspection step:

- Any `LR > 20` on any hypothesis → `phantom_precision` lint failure → `passed=False`
- Any `LR < 0.05` on any hypothesis → same (`phantom_precision` in reverse)
- Non-resolution-class indicators with `LR > 10` → `anchoring_bias` warning (soft)

**Rationale:** LRs have no natural cognitive upper bound. `LR=100` is mathematically
valid but operationally means "this single piece of evidence resolves the question."
That should require a resolution_class flag, not a raw LR.

---

## Change 6 — Migration

**Scope:** All active topic JSONs in `temp-repo/topics/`.

**Conversion formula:**  
For each existing pp-based indicator, derive LR from *current* posterior
(not design prior):

```
LR(H_i) = (current_posterior[H_i] + delta_pp/100) / current_posterior[H_i]
```

Normalized to max=1.0.

**Flag large discrepancies:**  
If design-prior-derived LR differs from current-posterior-derived LR by >30%,
write `"lr_migration_warning": "design prior drift >30%"` to indicator metadata.
Operator must manually review flagged indicators before next firing.

**Schema versioning:**  
Add `"schemaVersion": 2` and `"migratedAt": "<ISO8601>"` to topic JSON root.
`posteriorImpact` in evidenceLog migrates from string enum to structured subfield:

```json
"posteriorImpact": {
  "indicatorId": "t2_fed_dovish_pivot",
  "lrApplied": {"H1_HOLD": 0.4, "H2_CUT": 1.0, "H3_HIKE": 0.1},
  "nFiringsAtTime": 1,
  "lrDecayApplied": 0.65,
  "outcome": "FIRED"
}
```

Historical entries (schemaVersion 1) remain as-is; calibration and resolve
skills check `schemaVersion` before parsing `posteriorImpact`.

---

## What this does NOT change

- `_pp_shifts_to_likelihoods()` is kept (renamed `_pp_shifts_to_likelihoods_legacy()`)
  for backtest replay of schemaVersion 1 history. Not called in live path.
- The 0.98 clamp stays as structural last-resort guard.
- Ceiling hits continue to log CRITICAL. The `pending_overrides/` queue
  (separate workstream) is not in scope for this spec.
- TTL / stale_evidence enforcement is a separate workstream.

---

## Amendments from final red-team

### A1 — `apply_indicator_effect()` must be implemented before merge [CRITICAL]
The spec references `apply_indicator_effect()` as the site for lr_decay and n_firings
logic — this function does not exist in `engine.py`. `lr_decay` and `n_firings` are
not read anywhere in the live codebase. **This spec is incomplete until that function
exists and is tested against topics with n_firings > 0.** Block merge on this.

### A2 — phantom_precision cap must check pre-normalization ratio [CRITICAL]
Likelihoods are normalized to max=1.0 before storage. Checking `LR > 20` on the
normalized vector always sees max=1.0 — the guard is blind to extreme absolute
magnitudes. Fix: cap fires when `max(raw_lrs) / min(raw_lrs) > 20`, computed before
normalization. Governor receives the raw ratio from `apply_indicator_effect()`, not
the normalized dict.

### A3 — lr_decay must be bounded [0.0, 1.0] [SERIOUS]
`lr_decay > 1.0` turns decay into amplification. The spec's own resolution_class
example uses `lr_decay=1.0` (no decay) intentionally — that is valid. But
`lr_decay=1.05` makes each re-firing stronger than the last, reintroducing the
compounding problem. Enforce `0.0 < lr_decay <= 1.0` in indicator schema validation;
reject topic JSONs with out-of-range values at load time.

### A4 — schemaVersion guard must exist in code before migration runs [SERIOUS]
`backfill.py:57` and `resolve.md` parse `posteriorImpact` as a string. No
schemaVersion branch exists anywhere in live code. When a schemaVersion 2 topic
enters the pipeline, `backfill.py` receives a dict and silently fails or throws
TypeError. Add `isinstance(posteriorImpact, dict)` guard to `backfill.py` before
migration; update `resolve.md` to branch on schemaVersion. **Migration is gated on
this guard being merged first.**

### A5 — Resolution-class misfire needs a recovery path [SERIOUS]
`n_firings == 0` gate permanently locks a topic if a resolution_class indicator fires
on a false positive. No correction mechanism exists. Add
`reset_resolution_class(indicator_id, reason)` — operator-gated, requires second
confirmation, clears n_firings, writes a DECISION-ledger evidenceLog entry. Document
in `skills/update-cycle.md`.

---

## Risks not yet resolved

1. **LR decay rate calibration** — default decay values (0.70/0.65/0.50) are
   reasoned estimates, not empirically derived. Need calibration pass against
   historical topic data before applying to production topics.

2. **Operator LR intuition** — `LR=4.0` is less intuitive than `+20pp`. Requires
   updated `skills/update-cycle.md` with LR reference table and worked examples.

3. **Interaction with existing `check_update_proposal()` failures** — governor
   already has `confidence_inflation` at line 935 and `stale_evidence` at line 982
   as hard failures. New LR path must pass through same gate without creating
   redundant or conflicting checks.
