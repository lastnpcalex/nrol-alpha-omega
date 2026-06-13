# Future Extensions and Deprecated Prototypes

Status: planning index. These features are not part of the current live operator workflow unless explicitly reimplemented behind the MCP proposal boundary.

The current live system is the core engine plus the Loom NROL-AO MCP workflow:

- topic JSON state
- governor gates
- indicator-bound FIRE/OBSERVE updates
- PARK/SCHEMA_GAP non-moving evidence
- safe news scans that queue posterior-moving proposals

Older experiments and docs are useful design material, but they should not be treated as current operations. This spec records what is worth preserving before those files are removed from version control and kept only as local deprecated artifacts.

## 1. Source Calibration Hardening

Former material:

- `CALIBRATION_SYSTEM.md`
- `skills/source-trust.md`
- `skills/calibration.md`
- `framework/source_ledger.py`
- `framework/source_db.py`
- `framework/calibrate.py`
- `framework/backfill.py`

Useful future idea:

- Validate `sources/source_db.json` before it contributes to evidence weighting.
- Rebuild source profiles from resolved topic-local ledgers.
- Expose source-trust provenance on each evidence weight calculation.
- Keep forecast Brier scores separate from source claim calibration.

Implementation boundary:

- Source calibration must not silently create posterior movement.
- Source trust should be an auditable modifier with provenance, not a hidden magic score.
- If the source DB is missing or malformed, the engine should degrade to explicit neutral/default trust and report that fact.

## 2. Future Cast Mode

Former material:

- `specs/source-calibration-future-casts.md`

Useful future idea:

Ask what would happen if a proposed event, indicator, anti-indicator, or hypothesis-resolution scenario occurred, without mutating topic JSON.

Expected shape:

```text
future_cast(slug, scenario, proposed_transition, indicator_id?, observed_value?)
  -> candidate transitions
  -> dry-run governance result
  -> shadow posterior delta
  -> red-team critique
  -> optional saved future-cast record outside topic JSON
```

Implementation boundary:

- No writes to `topics/*.json`, `posteriorHistory`, or `evidenceLog`.
- Saved casts, if implemented, live in a separate future-cast database.
- The output is advisory, not a commit.

## 3. MCP Red-Team Review of Proposed Actions

Former material:

- `specs/source-calibration-future-casts.md`
- Loom MCP deliberation work

Useful future idea:

Before an operator commits a proposed action, allow them to request a dry red-team review over the action itself.

Example questions:

```text
Should I commit proposal prop-123?
Is this FIRE a duplicate of an old event?
Would this OBSERVE double-count a sustained metric?
Does this schema extension create a same-step update path?
```

Implementation boundary:

- The review must be non-mutating.
- A review can recommend commit, withdraw, park, schema gap, or refile.
- The final authority remains `commit_match` plus human approval.

## 4. Conditional Extrapolation / Lens Forecasts

Former material:

- `METHODOLOGY_EXTRAPOLATE.md`
- `OPERATOR_MODEL_DESIGN.md`
- `extrapolate_green_vet.py`
- `extrapolate_ochre_vet.py`
- `predictions-green.json`
- `skills/extrapolate.md`
- `skills/extrapolation-tuning.md`
- `loom/methodology.html`

Useful future idea:

Generate falsifiable conditional predictions under existing topic hypotheses, using multiple lenses or personas and adversarial critics.

Expected shape:

```text
conditional_prediction = {
  conditionTopic,
  conditionHypothesis,
  predictionText,
  resolutionCriteria,
  deadline,
  conditionalProbability,
  lens,
  criticVerdicts
}
```

Implementation boundary:

- Conditional predictions are not evidence at creation time.
- They must not move topic posteriors.
- They may later be scored for operator/lens calibration after resolution.
- The feature should write to a separate prediction store unless and until topic schema support is explicitly designed.

## 5. LR Migration and Range Sensitivity

Former material:

- `specs/lr-migration-spec.md`
- `specs/lr-range-sensitivity-spec.md`
- `framework/migrate_to_lr.py`
- `framework/replay_indicators.py`
- `test_lr_migration.py`

Useful future idea:

Migrate old point posterior-effect indicators into explicit likelihood-ratio surfaces, then represent uncertain indicator strength as ranges rather than false-precision point estimates.

Implementation boundary:

- This is a migration/admin workflow, not runtime evidence handling.
- Range sensitivity should produce robustness reports and possible governance warnings.
- Runtime commits should still bind to precommitted likelihood surfaces.

## 6. One-Off Matcher Scripts

Former material:

- `matcher_hormuz_may12.py`
- `run_matcher.py`
- `apply_matcher.py`

Useful future idea:

These proved the value of article-to-indicator matching, but the live version belongs behind the MCP scan path.

Implementation boundary:

- Do not reintroduce one-off scripts that can bypass proposal review.
- Matching output should become proposals or PARK/SCHEMA_GAP evidence through MCP tools.

## Deprecation Rule

A feature should stay out of the tracked repo unless it satisfies one of these:

1. It is part of the current safe operator loop.
2. It is a core engine/governor/framework dependency.
3. It is a concise future spec with implementation boundaries.
4. It is a tracked fixture required by tests.

Everything else can remain local and ignored until it is rebuilt behind the current authority boundary.
