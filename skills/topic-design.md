# Skill: Topic Design (v2 — adversarial throughout)

Create a new calibration topic, or modify an existing one, with red/blue
team adversarial review baked into the design phases — not just at a
final gate. Designed so that topics produced by this skill don't need
later cleanup to fix their priors or indicators.

## When to use

- A new "Current Thing" needs tracking
- An existing topic needs hypothesis restructuring
- You need to scaffold a topic from a question

## v1 → v2 changes (why this skill is different now)

The earlier topic-design skill ran adversarial review only at the FINAL
design gate, after all phases were drafted. This let operator-anchored
priors and miscalibrated indicators slip through and required cleanup
later. v2 puts adversarial review at every phase where operator judgment
leaks in:

| Phase | Mechanical | Adversarial subagents | Operator gate |
|---|---|---|---|
| 1 — Question/scope | sanity | none (creative) | approve |
| 2 — Priors + hypotheses | `run_mechanical_checks` partial | **red/blue per topic on priors** | approve / revise / kill |
| 3 — Indicators | `propose_indicators_lint` (full) | **shape review per indicator** + **red/blue per topic at SET level** | approve / revise / kill |
| 4 — Actor model | basic | none (operator inspection) | approve |
| 5 — Data feeds | indicator↔feed coverage | none (operational) | approve |
| 6 — Cold storage scan | — | — | — |
| Final | full `run_mechanical_checks` | `run_design_gate` adversarial subagent (whole-topic integration) | verdict review |

Adversarial review is targeted at the failure modes that actually exist:
- **Priors**: operator anchoring is the documented failure mode
- **Indicator set**: compound projection, cluster suspicion, causal-event overlap — these are SET-level patterns, not per-indicator, so one batched debate per topic catches them

Phases 4-5 don't get red/blue because adversarial framing on actor lists
or data feeds produces list-padding rather than insight.

## Multi-topic parallel

Phases 2-5 are designed to run on N topics in parallel. The flow:

1. Operator-driven Phase 1 (sequential or batched conversation; one
   question per topic at a time, since framing is creative work)
2. AI drafts Phase 2 for all N topics in one pass
3. Mechanical checks run locally per topic (parallel, fast)
4. Subagent dispatches: N × 2 (red+blue) for priors, all in one parallel
   batch via the Agent tool
5. Debate envelopes presented to operator as a batch table
6. Operator gate: approve all / revise specific topics (re-loop) / kill specific topics
7. Approved-and-not-revising topics advance to Phase 3
8. Repeat structure for Phase 3 with chunking (Phase 3 dispatches more
   subagents per topic so chunks of 3 topics per batch keep dispatch
   counts manageable)

Revision is bounded: max 3 iterations per phase per topic via
`framework.design_workflow.can_revise`. After 3, operator must accept
current draft or kill the topic.

## Python imports

```python
from engine import scaffold_topic, create_topic, save_topic
from framework.lint_indicators import propose_indicators_lint
from framework.lint_indicator_shape import (
    build_shape_review_prompt, parse_shape_review_decision, record_shape_review,
)
from framework.design_workflow import (
    build_priors_red_team_prompt, build_priors_blue_team_prompt,
    build_indicator_set_red_team_prompt, build_indicator_set_blue_team_prompt,
    parse_review_response, format_debate_envelope,
    record_phase_envelope, can_revise, get_revision_count,
)
from framework.topic_design_gate import (
    run_mechanical_checks, run_design_gate,
    generate_review_prompt, parse_review_response as parse_design_gate_response,
)
```

## Phase 1 — Question / scope (operator-driven)

Operator and AI converge on:
- `meta.question`: specific, measurable, time-bounded
- `meta.resolution`: observable criterion that closes the question
- `meta.resolutionDate`: when the topic is assessed and a winning
  hypothesis recorded
- `meta.slug`: `calibration-{descriptive-name}`
- `meta.classification`: ALERT / CALIBRATION / ROUTINE

Operator gate: approve question per topic before any drafting begins on
Phases 2+. This is where you avoid wasting cycles drafting hypotheses
for an ill-formed question.

### Phase 1 checklist

- [ ] Specific (not "what happens with X")
- [ ] Measurable (has observable resolution criterion)
- [ ] Time-bounded (explicit deadline or tracking horizon)
- [ ] `meta.resolutionDate` set (past `resolutionDate` surfaces as
  CRITICAL in `governance_report` until topic marked RESOLVED)

## Phase 2 — Priors + hypotheses

### Step 2a: Draft

For each approved Phase-1 question, draft 3-6 hypotheses with
midpoints, units, priors, and per-prior justifications. Stamp into
`meta.priorJustifications` so the red team can review the reasoning.

### Step 2b: Mechanical checks

```python
result = run_mechanical_checks(topic_draft)
# Filter to phase-2 relevant blockers (skip indicator-coverage which
# fires before Phase 3 is drafted): meta, hypotheses, posteriors-sum,
# label-distinctness checks
```

Phase-2 mechanical blockers stop drafting; fix and re-check.

### Step 2c: Red team / blue team on priors (parallel)

```python
red_prompt = build_priors_red_team_prompt(topic_draft)
# Dispatch via Agent tool — fresh context

# After red returns:
blue_prompt = build_priors_blue_team_prompt(topic_draft, red_response)
# Dispatch via Agent tool — fresh context
```

Multi-topic: dispatch all N red team subagents in one parallel batch,
collect responses, dispatch all N blue teams in next parallel batch.

### Step 2d: Format debate envelope, present to operator

```python
envelope = format_debate_envelope(
    "phase2_priors", slug,
    draft_summary=f"H1={p1}, H2={p2}, ...",
    red_response=red_resp,
    blue_response=blue_resp,
    iteration=get_revision_count(topic, "phase2_priors") + 1,
)
record_phase_envelope(topic, "phase2_priors", envelope)
```

### Step 2e: Operator gate

Three actions:
- **APPROVE** — advance topic to Phase 3
- **REVISE** — operator gives revision notes; AI redrafts priors
  incorporating notes + red-team objections; re-runs steps 2b-2d
  (counts toward revision cap)
- **KILL** — abandon this topic; remove from active set

Cap: `MAX_REVISION_LOOPS = 3` per phase. After 3, only APPROVE or KILL
(no more REVISE). Enforced by `can_revise(topic, "phase2_priors")`.

## Phase 3 — Indicators

### Step 3a: Draft indicators

For each topic, draft ~15-30 indicators across tiers:
- `tier1_critical`: indicators whose firing materially shifts posteriors
- `tier2_strong`: meaningful but not topic-resolving
- `tier3_suggestive`: weak directional evidence
- `anti_indicators`: evidence that REDUCES probability of specific H

Each indicator MUST include:
- `id`, `desc`, `posteriorEffect`, `likelihoods` (per-H, max ≤ 0.95)
- `shape`: `single_observation` | `per_event_member` | `ladder_rung`
- If `per_event_member`: shared `causal_event_id` across siblings
- If `ladder_rung`: `ladder_group` and `ladder_step`
- `observable` block (see Step 3a-2 below) OR explicit `observable: null`
  with operator note explaining why no continuous-evaluation form exists

DO NOT author resolution-disguised indicators. If firing the indicator
would essentially answer the question, that's a resolution event, not
marginal evidence — let `predictionScoring` handle it.

### Step 3a-2: Author observable blocks for continuous evaluation

Each numeric / count / range-band indicator gets an `observable` block
that lets the engine derive partial-strength LRs from sub-threshold
observations. This prevents the "0 fires from 320 articles" failure
mode where indicators fire only on literal threshold matches.

```jsonc
"observable": {
  "metric": "topic-prefix:metric_name",   // controlled vocabulary
  "family": "logistic" | "count_event" | "binary_event",
  "threshold_value": <numeric>,           // value at which committed_LR applies
  "baseline": <numeric>,                  // empirically uninformative reference
  "direction": "higher_strengthens" | "lower_strengthens"
}
```

Rules:
- Numeric metrics (e.g., FRED series, % values, dollar amounts) →
  `family: "logistic"`. Threshold and baseline in the metric's native
  units (NOT decimal fractions of percentages — if the metric is
  "transit %", threshold=25 means 25%, not 0.25).
- Count metrics (events, vessel strikes, retirements) →
  `family: "count_event"`. Threshold = N events; baseline = 0.
- Indicators that genuinely don't admit a continuous form
  (state transitions, compound joint conditions, named on-record
  statements, range-band membership with sustained-N-days requirements
  that don't map to single observations) → `family: "binary_event"`
  OR omit the observable block. These remain FIRE/PARK only.

The `baseline` value is the "empirically uninformative" reference point
where LR = 1.0 across all H. For backtestable topics, derive from the
fixture's non-fire-period median where possible. For un-backtestable
topics, declare based on operator judgment + cited source (treated
the same as authoring `likelihoods` — pre-committed, audited, lint-
checked, not free-form).

Lint via `framework.likelihood_models.lint_observable(ob)` — checks
family, units consistency, direction sanity (threshold > baseline for
higher_strengthens; threshold < baseline for lower_strengthens),
non-empty metric, baseline ≠ threshold (no degenerate function).

Engine evaluation is mechanical (`framework.likelihood_models.evaluate`)
— given observed value, baseline, threshold, and direction, returns
geometric interpolation in log-LR space between baseline (LR=1.0) and
threshold (LR=committed_LR). All existing engine gates apply unchanged
when the derived LR is fed into `bayesian_update`.

### Step 3b: Mechanical lint (full)

```python
lint_result = propose_indicators_lint(topic, draft_indicators)
# Hard-blocks on any blocker. Includes shape declaration, ladder
# coherence, phantom precision, lr_too_certain, compound projection,
# direction drift, cluster suspicion.
```

If blockers: revise, re-lint. Iterate until clean before subagent
dispatches (don't waste subagent compute on lint-rejecting drafts).

### Step 3c: Shape review subagents (per indicator, mandatory)

```python
for ind in indicators:
    prompt = build_shape_review_prompt(topic, ind)
    # Dispatch 2 fresh-context subagents per indicator. Decision:
    # RESOLUTION_DISGUISE | NOT_RESOLUTION | UNCLEAR
    
decisions = [parse_shape_review_decision(r) for r in [resp1, resp2]]
record_shape_review(topic, ind["id"], decisions)
```

Hard gate at `bayesian_update`: indicators without a passed shape review
cannot fire. So this step is non-negotiable.

Multi-topic: dispatch all topics × all indicators × 2 subagents in
chunks (e.g., 3 topics per batch to keep parallel dispatch counts
manageable).

### Step 3d: Indicator-SET red team / blue team (per topic)

Catches set-level patterns that per-indicator review misses:
compound projection, cluster suspicion, causal-event overlap, recycled
intel, coverage gaps.

```python
red_prompt = build_indicator_set_red_team_prompt(topic_draft)
# Dispatch fresh-context subagent

blue_prompt = build_indicator_set_blue_team_prompt(topic_draft, red_response)
# Dispatch fresh-context subagent
```

One red + one blue per topic, NOT per indicator. The set-level review
catches per-indicator quality issues AS WELL AS combinatorial ones,
with 1/N the dispatch cost.

### Step 3e: Format envelope, operator gate

Same pattern as Phase 2. APPROVE / REVISE / KILL with revision cap.

## Phase 3.5 — Empirical calibration + de-correlation + news-flow simulation

Combination of mechanical (data fetch + arithmetic) and adversarial
(red-team review of LR magnitudes and reference-class definitions).
Runs after Phase 3 lint + adversarial review pass. Produces three
artifacts that gate promotion to `topics/`:

- `topic.governance.calibrationReport`
- `topic.governance.decorrelationReport`
- `topic.governance.newsFlowSimulation` (NEW — see Step 3.5c)

Phase 3 lint also includes the directional-coverage check (see
`framework.lint_indicators._check_directional_coverage_news_flow`)
which catches asymmetric observable coverage at lint time. A topic
where any hypothesis has zero observable indicators favoring it gets
blocked before Phase 3.5 even runs — this is the cheap structural
catch. Phase 3.5 news-flow simulation is the operational confirmation
that the schema actually moves under realistic news.

Engine refuses `bayesian_update` on a topic without a valid
`meta.calibrationStatus`. There is no bypass flag. The only way to ship
without empirical validation is to set `meta.calibrationStatus =
"SKIPPED_OPERATOR_JUDGMENT"` with a `reason` field — which is loud and
visible in canvas.

### Step 3.5a-1: Tier-classify every indicator

Before fetching any data, classify every indicator into one of:

- **Tier 1 (quantitative-empirical)**: trigger condition is a numerical
  threshold on a public time series (FRED, BLS, BEA, USBR, etc.). E.g.,
  "U-3 unemployment >=4.7%", "ISM Manufacturing PMI <45 for 2 months",
  "HY OAS breaches 800bp sustained 10 trading days".
- **Tier 2 (event-empirical)**: trigger condition is a discrete observable
  event (counted by occurrence, no judgment). E.g., "FOMC announces 25bp
  cut", "FDIC failed bank list adds >=$50B asset failure", "DOI invokes
  Section 5".
- **Tier 3 (judgment-required)**: trigger condition requires *interpreting*
  qualitative source material. E.g., "Powell uses on-record cut-language",
  "Cook ratings shift D-favorable by >=10 toss-ups", "Fragile ceasefire
  arrangement persists >=60 days". LLM doing this empirically is the same
  hallucination problem we're trying to prevent.

Tier 1 + Tier 2 indicators get backtested empirically. Tier 3 indicators
get per-indicator `calibrationSkipReason` and stay operator-judgment-only.
The classification itself is a judgment call — borderline cases default
to Tier 3 (more conservative).

### Step 3.5a-2: Backtest LR calibration

For BACKTESTABLE topics (direct historical analogs exist) and
UN_BACKTESTABLE topics (no direct analog but reference class exists),
the calibration data path differs but the rest is identical.

**For BACKTESTABLE topics:**

```python
from framework.backtest_harness import run_backtest
report = run_backtest(topic_draft)
```

Harness reads `framework/backtest_data/<slug>.json` fixture. Fixture is
assembled at design time from authoritative public sources (NBER, FRED,
BLS, BEA, USBR, FEC, Cook archives) — never hand-encoded from LLM
memory. Provenance fields (URL, fetch date, scope) recorded inline.

For each Tier 1+2 indicator, computes empirical P(E|H) per hypothesis
with Beta(1,1) Laplace smoothing.

**For UN_BACKTESTABLE topics:**

```python
# Operator first defines reference class on topic.meta.referenceClass
topic_draft["meta"]["referenceClass"] = {
    "definition": "Prior maritime chokepoint closures with confirmed reopen",
    "analog_events": [
        {"name": "Suez 1967-75", "peak": "1967-06", "reopen": "1975-06"},
        {"name": "Gulf shipping war 1984-88", ...},
        ...
    ],
    "exclusions": [
        {"event": "Bab-el-Mandeb 2024", "reason": "no closure threshold reached"},
        ...
    ],
}
report = run_reference_class_backtest(topic_draft)
```

Reference class fetched, validated by adversarial subagent (Step 3.5a-3
below), then empirical LRs computed against analog outcomes with
explicit small-N smoothing.

### Step 3.5a-3: Adversarial review of empirical LRs and reference classes

Even with empirical anchoring, two failure modes remain:

1. **Bad reference class** (UN_BACKTESTABLE topics): which prior events
   count as analogs is itself a judgment call.
2. **Empirical computation errors / sample size issues / analog→new-event
   drift** (both BACKTESTABLE and UN_BACKTESTABLE): small-N empirical
   estimates have wide confidence intervals; a Beta(1,1) smoothed value
   from n=4 isn't a hard fact.

Two adversarial dispatches per topic:

```python
from framework.calibration_review import build_reference_class_red_prompt
from framework.calibration_review import build_lr_magnitude_red_prompt

# UN_BACKTESTABLE topics only:
ref_red = build_reference_class_red_prompt(topic_draft)
# Dispatch fresh-context subagent. Output: SET_NEEDS_REWORK on the class
# definition (excluded events that should be in, included events that
# shouldn't, missing exclusion rationales) or SET_OK.

# Both BACKTESTABLE and UN_BACKTESTABLE topics:
lr_red = build_lr_magnitude_red_prompt(topic_draft, calibration_report)
# Dispatch fresh-context subagent. Output: per-indicator objections on
# LR magnitude given small-N issues, analog drift, smoothing choice.
```

Operator processes objections, revises LRs, may need to reshape the
reference class. Final review = blue-team rebuttal as in Phase 3.

### Status outcomes

- **VALIDATED**: every Tier 1+2 indicator has fixture data; deviation
  between empirical and declared LR < 30%; LR-magnitude red-team finds
  no objections that warrant revision (or all conceded objections have
  been addressed). For BACKTESTABLE topics with no Tier 3 indicators.
- **VALIDATED_WITH_FLAGS**: scoring complete but ≥1 of: (a) Tier 3
  indicators present (per-indicator skip reason recorded), (b) ≥1 Tier
  1+2 indicator's deviation > 30% pending revision, (c) LR-magnitude
  red-team objections accepted but not yet incorporated. Flagged
  indicators visible in canvas.
- **VALIDATED_VIA_REFERENCE_CLASS_REVIEWED**: UN_BACKTESTABLE-style
  topic where reference class was defined, adversarially reviewed, and
  empirically scored against analog events. Reference class definition
  + adversarial review envelopes stamped on `topic.meta.referenceClass`.
- **PENDING_DATA_INGESTION**: backtestable in principle but the
  historical data fixture for the indicator's source isn't wired yet.
  Topic must set this status; `bayesian_update` will reject until
  status flips to VALIDATED or VALIDATED_WITH_FLAGS.
- **SKIPPED_OPERATOR_JUDGMENT**: explicit signed bypass; requires
  `meta.calibrationSkipReason`. The bypass exists but it's signed.

### Step 3.5b: De-correlation simulation

```python
from framework.decorrelation_sim import run_decorrelation_sim
report = run_decorrelation_sim(topic_draft)
# Returns: {status: PASS | FAIL,
#           tests: [{name, expected, actual, deviation}],
#           failures: [...]}
```

Pure code, no LLM, no external data. For each topic:

1. **per_event_member exclusion test.** For every group of indicators
   sharing `causal_event_id` AND `shape="per_event_member"`, construct
   a synthetic scenario firing all members. Verify engine fires exactly
   one (the highest-LR-aligned).
2. **causal_event_id de-correlation test.** For every group of
   indicators sharing `causal_event_id` (without per_event_member),
   fire ≥2 members on the same event_id. Verify joint posterior update
   is discounted relative to firing them under independent event_ids.
3. **lint compound-projection accuracy test.** Run lint's compound
   projection. Run actual engine on the same firing sequence. Assert
   |lint_projection - engine_actual| < 5pp on max-H. Divergence > 5pp
   means lint is mis-modeling the engine — block promotion until
   either lint or engine is fixed.

Failure on any test blocks promotion. Pass writes
`governance.decorrelationReport` with all test outcomes recorded.

### Step 3.5c: News-flow simulation gate

The previous Phase 3.5 steps validate *schema math* (LR magnitudes,
de-correlation arithmetic, no compound projection saturation). This step
validates *operational behavior*: given realistic news under each
hypothesis, does the schema actually move posteriors toward the
generating hypothesis?

This gate exists because Phase 3 lint (including the new directional-
coverage check) catches schemas where any H has zero observable favoring
it — but it doesn't catch schemas where the observables exist but their
metrics are too narrow to fire on actual news vocabulary (the deeper
hormuz failure pattern: H1/H2 indicators existed but required "on-record
bilateral framework agreement EXPLICITLY referencing transit guarantees"
which news never speaks).

```python
from framework.news_flow_simulation import (
    build_synthetic_news_prompt,
    parse_synthetic_corpus,
    to_matcher_article_format,
    simulate_per_hypothesis,
    evaluate_news_flow_responsiveness,
)
from framework.news_observation_pipeline import (
    build_matcher_prompt, parse_matcher_output,
)

per_h_results = []
for h_key in topic["model"]["hypotheses"].keys():
    # Stage 1: dispatch synthesis subagent (fresh context per H)
    synth_prompt = build_synthetic_news_prompt(topic, h_key, n_articles=5)
    synth_response = dispatch_agent(synth_prompt)
    articles = parse_synthetic_corpus(synth_response)
    matcher_input = [to_matcher_article_format(a) for a in articles]

    # Stage 2: dispatch matcher subagent on the synthetic corpus
    match_prompt = build_matcher_prompt(topic, matcher_input)
    match_response = dispatch_agent(match_prompt)
    decisions = parse_matcher_output(match_response)

    # Stage 3: simulate apply on a clone of the topic, evaluate per-H verdict
    result = simulate_per_hypothesis(topic, h_key, decisions, matcher_input)
    per_h_results.append(result)

gate = evaluate_news_flow_responsiveness(per_h_results)
topic.setdefault("governance", {})["newsFlowSimulation"] = gate
```

Gate verdicts:
- **PASS**: every H simulation moved posterior toward the generating H
  by ≥2pp with zero wrong-direction outcomes.
- **VALIDATED_WITH_FLAGS**: at most 1 H has WEAK shift (positive but
  under 2pp, or 1-2 wrong-direction outcomes) — still ships but
  flagged in canvas for operator awareness.
- **FAIL**: any H produced no movement / negative movement, or > 2
  wrong-direction outcomes on a corpus, or >1 H is WEAK. Topic blocked
  from promotion. Operator must extend schema (add observables, broaden
  metric semantics) and re-run.

Cost (per topic): n_hypotheses × ~2 subagent dispatches (synth + match)
≈ 8-10 dispatches per typical topic. Run once at design time; persist
to governance.newsFlowSimulation. Re-run on schema changes (e.g.,
indicator additions via cleanup-session).

### Phase 3.5 checklist

- [ ] Backtest run; status recorded on `governance.calibrationReport`
- [ ] `meta.calibrationStatus` set to one of the valid values
- [ ] De-correlation simulation run; status PASS recorded on
      `governance.decorrelationReport`
- [ ] News-flow simulation run; verdict PASS or VALIDATED_WITH_FLAGS on
      `governance.newsFlowSimulation`
- [ ] All deviation > 30% indicators kicked back for LR revision
- [ ] All FAIL de-correlation or news-flow tests resolved or block
      promotion

Operator gate: approve.

### Why no bypass flag

The whole gate collapses if there's a `--skip-calibration` path. There
isn't one. `bayesian_update` reads `meta.calibrationStatus` and rejects
on missing/null. To ship without empirical validation, set status to
`"SKIPPED_OPERATOR_JUDGMENT"` with a `reason` field — that string lives
in the topic JSON, renders in canvas, and is auditable in any future
review. The bypass exists but it's signed.

## Phase 4 — Actor model

Operator-driven; no adversarial subagent (low leverage here).

### Phase 4 checklist

- [ ] Key decision-makers identified
- [ ] Decision styles documented
- [ ] Biases and constraints noted

Operator gate: approve.

## Phase 5 — Data feeds

### Step 5a: Draft

For each topic, list quantitative metrics + sources + update cadence.

### Step 5b: Mechanical check

Each indicator should reference at least one data feed (loose check —
some indicators are event-triggered with no specific feed).

### Phase 5 checklist

- [ ] Quantitative metrics with sources and update frequency
- [ ] Baseline values recorded
- [ ] Thresholds defined for indicator triggers

Operator gate: approve.

## Phase 6 — Cold storage scan

Before finalizing, scan `canvas/evidence-cold.json` for pre-existing
evidence that matches the new topic.

```
For each cold storage entry:
  1. Compare entry keywords/actors/regions against topic question/
     hypotheses/indicator descriptions
  2. If overlap is significant (≥3 keyword matches or actor + domain match):
     - Log claims as evidence in topic.evidenceLog
     - Set posteriorImpact based on indicator matching (live pipeline rules)
     - Add note "Retroactive from cold_NNN"
     - Do NOT remove from cold storage (may match future topics)
```

Cold storage entries carry their original source trust scores. Don't
re-assess; use values recorded at triage time.

**Limitation**: cold storage only contains evidence that was triaged
through the pipeline. Always conduct independent research when creating
a new topic — cold storage supplements, not substitutes.

## Final design gate

```python
gate = run_design_gate(topic)
# Returns: mechanical (full re-run) + review_prompt (whole-topic
# adversarial)
```

Dispatch the adversarial subagent with `gate["review_prompt"]`. Parse
the response with `parse_design_gate_response`. Per-check verdicts;
FAIL on any blocking check kicks the topic back to the relevant phase.

## Save

```python
save_topic(topic)
```

`save_topic` runs the engine's first-save indicator-shape lint gate.
Any indicator missing `shape` or violating ladder coherence blocks the
save here as a final structural backstop.

## Naming convention

- Topic slugs: `calibration-{descriptive-name}`
- Evidence IDs: `ev_NNN` (sequential within topic)
- Indicator IDs: `t{tier}_{descriptive_slug}` (e.g., `t1_bilateral_deal_announced`)

## Cleanup features integrated into topic-design

The cleanup workflow's adversarial machinery (red/blue team per
indicator, shape review, lint) is folded into Phase 3 here. The
intended consequence: topics designed via this skill should not need
`cleanup-indicator-sweep` to fix their indicators later. `cleanup-
indicator-sweep` becomes the path for adding NEW indicators to existing
topics (mid-life schema additions), not for fixing the topic's original
indicator set.

If a topic is found post-deployment to have miscalibrated priors or a
flawed indicator set, that's evidence the design-gate adversarial
review missed something — log a meta-issue and consider strengthening
the relevant phase's red-team prompt, rather than just patching the
topic.
