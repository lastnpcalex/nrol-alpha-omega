# NROL-AO — Agent Instructions

You are operating the NROL-AO Bayesian estimation framework. This is a
governor-gated system where **natural language is a proposal, not an authority**
— only verified evidence moves posteriors.

## Standing orders

These apply automatically whenever you interact with topic data, evidence,
or posteriors. You do not need to be told to follow them.

### On new information (headlines, URLs, data)

1. **Triage first**: `from engine import triage_headline`
   - Match against all active topics in `topics/`
   - Check if any indicator's observable threshold is met
   - Assess source trust via 5-tier chain
   - Route: UPDATE_CYCLE / LOG_EVIDENCE / MONITOR / IGNORE
2. **Never skip triage** to go straight to evidence or posteriors

### On evidence logging

1. Full provenance required: id, time, text, tags, source, claimState, posteriorImpact
2. Lint through 5 failure modes before saving:
   - rhetoric_as_evidence, recycled_intel, anchoring_bias, phantom_precision, stale_evidence
3. posteriorImpact is "NONE" unless an indicator actually fired
4. Source trust resolved, never assumed (unknown = 0.50)

### On posterior updates

1. **Only fired indicators move posteriors.** `bayesian_update` requires
   `indicator_id`. There is no freeform path. If new evidence does not
   match any existing indicator at scan time, **park it** via
   `pipeline.process_evidence` (without `fired_indicator_id`) — the
   evidence is logged with `posteriorImpact: NONE` and queued in
   `governance.flagged_for_indicator_review`. Posteriors do not move on
   parking.
2. Apply pre-committed `posteriorEffect` only — do not invent magnitudes
3. Posteriors must sum to 1.00
4. Run `check_update_proposal()` before applying
5. Run `governance_report()` after applying
6. Check `propagate_alert()` for downstream dependency staleness
7. Append to posteriorHistory with date + justification note

### On parked evidence

Parked evidence accumulates per topic in `governance.flagged_for_indicator_review`.
The canvas surfaces a `flagged_indicator_review:<slug>` alert when this
list is non-empty. Operator triggers cleanup via the canvas; cleanup runs
through `skills/cleanup-indicator-sweep.md` — a cyborgist workflow that:

- Matches parked evidence to existing indicators via
  `framework.indicator_match.match_evidence_to_indicators` (semantic
  similarity, plus mechanical direction-agreement check)
- Authors new indicators only when no existing match fits (rare path);
  lint via `framework.lint_indicators.propose_indicators_lint` catches
  bad LRs (phantom_precision, lr_too_certain, compound_projection peg,
  direction_drift, cluster_suspicion)
- Spawns red-team and blue-team subagents via the `Agent` tool — fresh
  context per team, prompts from `framework.red_blue_team.get_team_prompts`,
  budgeted tool calls (topic_search + WebSearch + WebFetch). Agents
  argue P(E|¬H) and P(E|H) respectively with citations.
- Operator approves via canvas; engine validates the receipt + lint pass
  + active session before applying via `commit_indicator_cleanup`

You do NOT run red/blue team in your own context — that's the
cross-context anchoring failure mode that pegged 17 topics. Always
dispatch via Agent tool.

### Hard gates the engine enforces

`bayesian_update` will raise on any of these:

- **No `indicator_id`.** The freeform path is removed. Park instead.
- **`P(E|H) ≥ 0.99` or `≤ 0.01`** is dishonest as a likelihood from an
  observation. Indicator-bound LRs are auto-capped to 0.95 max
  (proportional scaling, no-op on posterior). Use `0.95` / `0.05` for
  near-certain in indicator definitions. Resolution flows through
  `update_posteriors`, not `bayesian_update`.
- **Shift > 15% with < 2 evidence refs** (`confidence_inflation`).
  Cleanup workflow can group multiple parked evidences into one firing
  to satisfy this.
- **Duplicate text or shared `informationChain`** (`repetition_as_validation`).
- **`max(proposed_posterior) > 0.85` without a `redTeam` entry on
  `posteriorHistory` within 30 days** (`saturation_redteam_required`).

`add_indicator` will raise on:

- **No active cleanup session** (`IndicatorAddNotAllowed`). Mid-life
  schema additions must go through
  `start_indicator_cleanup_session` → propose → lint → red/blue → operator
  approve → `commit_indicator_cleanup`. Topic creation via `create_topic`
  builds indicators inline and bypasses this gate.

`save_topic` will raise on:

- **New indicator IDs added without an active session.** Catches direct-dict
  manipulation that bypasses `add_indicator`.

If a gate fires, the operation did not apply. Fix the cause; do not bypass.
The framework's enforcement code is itself audited — every Edit/Write to
`engine.py`, `governor.py`, `framework/*.py`, `skills/*.md`, or `scripts/*.py`
is logged conspicuously to `canvas/activity-log.json` as
`FRAMEWORK_CODE_EDIT` with severity tag.

### LR provenance via lens

Every `bayesian_update` stamps `lrSource: { lens, lensSetAt, source }` on
the new `posteriorHistory` entry. Lens is required (no silent fallback) —
pass `lens=` explicitly or set `topic.meta.lens` via `set_topic_lens()`.
The engine validates the lens against `VALID_LENSES` (GREEN, AMBER, BLUE,
RED, VIOLET, OCHRE, OPERATOR_JUDGMENT). Apply the lens in your reasoning
trace — see `skills/news-scan.md` for the per-lens table.

### On governance

1. Health = HEALTHY (0 issues) / DEGRADED (1-2) / CRITICAL (3+)
2. R_t regimes: SAFE < 0.1, ELASTIC < 0.3, DANGEROUS < 1.0, RUNAWAY > 1.0
3. Evidence freshness uses per-tag TTLs (RHETORIC=24h, EVENT=72h, DATA=168h, POLICY=720h)
4. Flag CRITICAL health immediately — do not proceed with updates until addressed

## Skill files

Detailed workflow prompts with actual function calls in `skills/`:

- `skills/triage.md` — headline routing
- `skills/update-cycle.md` — indicator fire + posterior update
- `skills/evidence.md` — evidence logging + lint
- `skills/governance.md` — epistemic health audit
- `skills/topic-design.md` — create/modify topics
- `skills/dependencies.md` — cross-topic dependency management
- `skills/source-trust.md` — source registration + calibration
- `skills/red-team.md` — devil's advocate challenges
- `skills/calibration.md` — prediction scoring + Brier scores

**Read the relevant skill file when performing that workflow.**

## Key files

- `engine.py` — core Bayesian engine (load/save topics, update posteriors, fire indicators)
- `governor.py` — epistemic governor (R_t, freshness, admissibility, health)
- `framework/triage.py` — headline triage
- `framework/dependencies.py` — cross-topic staleness detection
- `framework/lint.py` — evidence and topic linting
- `framework/contradictions.py` — contradiction detection
- `framework/red_team.py` — devil's advocate scoring
- `framework/scoring.py` — Brier scores and calibration
- `topics/*.json` — active calibration topics (one file per topic)
- `sources/source-trust.json` — base trust priors (Tier 4)
