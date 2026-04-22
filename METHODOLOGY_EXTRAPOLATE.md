# Methodology: The Extrapolate Skill

A generator-critic agent pipeline for producing **conditional predictions** over an
existing portfolio of Bayesian topics. This document describes what the pipeline
does, what it does *not* do, where it touches the core Bayesian machinery, and
where it is doing something else entirely.

---

## 1. What problem this solves

Each topic in NROL-AO carries a set of mutually exclusive hypotheses with posteriors
that are updated via likelihood ratios when indicators fire. The engine answers:
"which hypothesis is likely?"

It does **not** answer: "conditional on each hypothesis being true, what other
observable events should follow?" That second question is the forecast portfolio
— a net of auxiliary predictions, each keyed to a conditioning hypothesis.

The extrapolate skill generates that net.

```
            ┌─────────────────────────────────────┐
            │   TOPIC JSON                        │
            │                                     │
            │   hypotheses: { H1, H2, H3, H4 }    │
            │   posteriors: { .35, .30, .25, .10 }│
            │   indicators: [...]                 │
            │   evidenceLog: [...]                │   ← engine updates this
            │                                     │
            │   conditionalPredictions: [...]     │   ← extrapolate writes here
            └─────────────────────────────────────┘
```

A conditional prediction looks like:

```
  {
    "id": "cp_017",
    "conditionTopic": "calibration-fed-rate-2026",
    "conditionHypothesis": "H2",                      ← "IF one cut"
    "predictionText": "10Y Treasury falls to 3.6% by Dec",
    "resolutionCriteria": "10Y close ≤ 3.6% on 2026-12-31",
    "deadline": "2026-12-31",
    "conditionalProbability": 0.74,                   ← P(prediction | H2)
    "lens": "GREEN",
    "criticVerdicts": { "RED": "APPROVE", ... }
  }
```

These are *forecasts*, not evidence. They have no effect on posteriors at
write-time. Their value is realized at resolution — they are later scored, and
those scores feed calibration (Section 5).

---

## 2. Epistemic architecture: generators and critics

The pipeline deliberately does not ask a single model to forecast. It splits
forecasting into **generation** and **adversarial critique**, each handled by
several personas with opinionated priors.

### 2.1 The six ideator personas

| Persona | Prior / lens                          |
|---------|---------------------------------------|
| GREEN   | Midtopia / continuation — "things trend" |
| AMBER   | Phase-shift / regime change — "nonlinear break" |
| BLUE    | Systemic resolution — "institutions reconverge" |
| RED     | Tail risk / pessimist — "the left tail bites" |
| VIOLET  | Actor-centric incentives — "follow the agent" |
| OCHRE   | Structural determinism — "the constraint wins" |

GRAY is a universal shared-assumption skeptic and **is always a critic**, never a
generator.

### 2.2 The pick-2 / critique-5 rule

The operator picks exactly 2 of 6 to generate. The remaining 4, plus GRAY,
automatically become critics.

```
   Generators (2)                  Critics (5)
   ╔══════════╗                    ╔══════════════════╗
   ║  GREEN   ║                    ║ BLUE             ║
   ║  AMBER   ║ ─── pick 2 ──▶     ║ RED              ║
   ╚══════════╝                    ║ VIOLET           ║
                                   ║ OCHRE            ║
   (leftover 4 +                   ║ GRAY (always)    ║
    GRAY)                          ╚══════════════════╝
```

The structural claim: a forecast that survives critique from four lenses it does
not share, plus a lens whose sole job is to name shared assumptions, is more
robust than a forecast produced by a single model talking to itself. This is a
**heuristic ensemble**, not a formal mixture model (Section 6).

---

## 3. The pipeline

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 1. SETUP                                                            │
│   - acquire_lock() in extrapolation.db (single-writer)                   │
│   - start_run(run_id, dichotomy="PICK2", generators, critics, scope)     │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 2. ENUMERATE SCOPE                                                  │
│   load_topic(slug) for each ACTIVE topic; keep hypotheses with           │
│   posterior > 0.05. Pass existing conditionalPredictions to ideators     │
│   as "avoid duplicating."                                                │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 3. PARALLEL IDEATION   (2 Haiku sub-agents, 1 message, concurrent)  │
│                                                                          │
│        ┌───────────────┐       ┌───────────────┐                         │
│        │  GEN A (Haiku)│       │  GEN B (Haiku)│                         │
│        │  persona X    │       │  persona Y    │                         │
│        │  → proposals[]│       │  → proposals[]│                         │
│        └───────┬───────┘       └───────┬───────┘                         │
│                └────────┬──────────────┘                                 │
│                         ▼                                                │
│                 all_proposals (merged)                                   │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 4. VETTING   (parent runs Sonnet sequentially, one proposal at a    │
│                    time — cheap, avoids micro-agents)                    │
│                                                                          │
│   For each proposal, Sonnet checks 5 criteria:                           │
│     1. Falsifiable (observable metric + threshold)                       │
│     2. Deadline realistic vs topic horizon                               │
│     3. Not a duplicate (<70% semantic overlap)                           │
│     4. Probability direction consistent with any CPT                     │
│     5. In scope                                                          │
│                                                                          │
│   Verdict ∈ { APPROVE, REJECT, MODIFY }                                  │
│   All ideations + verdicts logged to DB, regardless of outcome.          │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 5. PARALLEL CRITIQUE   (5 Opus sub-agents, 1 message, concurrent)   │
│                                                                          │
│   candidate_portfolio (all APPROVE/MODIFY from Step 4)                   │
│                                                                          │
│        ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐                  │
│        │ C1   │  │ C2   │  │ C3   │  │ C4   │  │ GRAY │                  │
│        │Opus  │  │Opus  │  │Opus  │  │Opus  │  │Opus  │                  │
│        └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘                  │
│           │         │         │         │         │                      │
│           ▼         ▼         ▼         ▼         ▼                      │
│   per_prediction verdicts + portfolio_narrative from each critic          │
│                                                                          │
│   Verdict per (prediction, critic) ∈                                     │
│       { APPROVE, MODIFY, DROP, NEUTRAL }                                 │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 6. CONSENSUS RULE                                                   │
│                                                                          │
│   For each vetted candidate:                                             │
│                                                                          │
│     Sonnet verdict ∈ {APPROVE, MODIFY}     AND                           │
│     count(critics with verdict=DROP) ≤ 1                                 │
│     ───────────────────────────────────                                  │
│     ⇒ write to topic JSON                                                │
│                                                                          │
│   This is an anti-veto rule, not strict consensus: any single critic     │
│   can object without killing the prediction. Two objections do.          │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 7. WRITE                                                            │
│                                                                          │
│   process_conditional_prediction(...)                                    │
│     → add_conditional_prediction()  (scoring.py)                         │
│     → save_topic()                                                       │
│                                                                          │
│   Predictions are appended with a new sequential ID (cp_NNN).            │
│   No replacement of existing predictions ever occurs.                    │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Step 8. FINALIZE                                                         │
│   - log_portfolio_snapshot() for each critic's narrative                 │
│   - finish_run(run_id, status="COMPLETED", duration_sec, tokens, cost)   │
│   - release_lock(run_id)                                                 │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Data flow: source of truth vs. audit trail

Two stores are touched. They have different roles.

```
                  ┌─────────────────────────────┐
                  │   topic JSON                │
                  │   (source of truth)         │
                  │   conditionalPredictions[]  │◀──── process_conditional_prediction()
                  └─────────────────────────────┘     (only write path)
                                                        │
                                                        │ also writes
                                                        ▼
                  ┌─────────────────────────────────────────┐
                  │   extrapolation.db (SQLite, WAL mode)   │
                  │   audit + analytics only                │
                  │                                         │
                  │   agent_runs                            │
                  │   ideations       (EVERY proposal)      │
                  │   vetting         (EVERY verdict)       │
                  │   critic_verdicts (5 × every proposal)  │
                  │   meta_lint       (portfolio narratives)│
                  │   approved_predictions (links → cp_NNN) │
                  │   portfolio_snapshots                   │
                  │   sweep_lock      (single-writer mutex) │
                  └─────────────────────────────────────────┘
```

Important invariants:

- **Topic JSON is canonical.** If the DB and the JSON disagree, the JSON wins.
- **Sub-agents never write.** They return structured JSON. The parent does all DB
  and all topic writes. This prevents SQLite lock contention and keeps the audit
  trail linear.
- **Everything is logged, including rejections.** The ratio of ideations to
  approvals is itself a signal — a generator producing 80% rejects is telling
  you something.

---

## 5. Where this touches Bayes, and where it doesn't

This is the section worth being careful about.

### 5.1 What the extrapolate skill is

A calibrated forecast generator. It elicits `P(prediction | condition)` from
language models under multiple lenses, vets for falsifiability and scope, and
subjects each forecast to critique from lenses that do not share the generator's
prior.

### 5.2 What it is not

- **It is not a Bayesian update.** No posteriors change when a prediction is
  written. No likelihood is computed. The condition-hypothesis is *referenced*,
  not updated.
- **It is not Bayesian model averaging.** The six lenses are not formally
  weighted by model evidence. They are picked by the operator, and the two
  generators enter with equal standing. "Convergence" between lenses (both
  proposing the same prediction) is tracked in `lens_agreement` but does not
  combine their probabilities.
- **The consensus rule is not a likelihood ratio.** "≤1 critic DROPs" is a
  heuristic veto threshold, not a posterior over prediction validity. A
  prediction that survives with 4 APPROVE and 1 DROP is treated identically to
  one with 5 APPROVE, even though the information content differs.
- **The elicited probabilities are subjective.** `conditional_probability=0.74`
  is whatever the language model reports. It is constrained to the open
  interval (0.10, 0.90) by the vetting checklist (to avoid phantom precision),
  but it is not derived from data, frequency, or a reference class. It is a
  judgment.

### 5.3 Where the Bayesian coupling actually happens

The coupling is downstream, at resolution time, not at write time.

```
   write-time (extrapolate skill)               resolution-time (scoring)
   ──────────────────────────────               ─────────────────────────

   elicit P(pred | H_cond) from LLM   ───▶     observe outcome at deadline

                                               score: Brier((P, outcome))
                                                 ↓
                                               update source trust
                                                 (lens calibration ledger)
                                                 ↓
                                               feed into topic's
                                                 conditional_calibration_report
                                                 ↓
                                               operators weight future
                                                 forecasts from that lens
                                                 accordingly
```

The skill generates forecasts. `sweep_conditional_predictions()` resolves them
as deadlines pass. `conditional_calibration_report()` produces the Brier /
calibration metrics per lens, per topic. Over many runs, a lens that is
systematically overconfident or systematically biased gets detected here — not
by introspection in the skill itself.

This is the honest Bayesian story: the pipeline produces *forecasts to be
scored*, and the scoring is what updates beliefs about lens reliability. The
"Bayesian-ness" of NROL-AO lives in the engine's `apply_indicator_effect()` and
`bayesian_update()`, not here.

### 5.4 What is genuinely epistemic about this pipeline

Three things, stated plainly:

1. **Adversarial decomposition.** Separating generation and critique, and
   requiring that generators cannot critique their own output, removes a specific
   failure mode: a single model endorsing its own proposals because they follow
   its prior. It does not remove shared-prior failures across all six personas
   — which is exactly why GRAY exists as a universal skeptic.

2. **Blind-spot surfacing.** Each critic returns not only per-prediction
   verdicts but a portfolio-level `blind_spots[]` list. These are structural
   observations — "this portfolio assumes institutional continuity" — that an
   operator can read and use, independent of whether any individual prediction
   passed.

3. **Audit completeness.** Every proposal, every verdict, every reasoning
   string is persisted. A skeptical operator can reconstruct, months later, why
   a particular prediction was written and what the critics said. This is the
   single strongest epistemic property of the system: it is inspectable.

---

## 6. Known limitations

| Limitation                            | Consequence                                   |
|---------------------------------------|-----------------------------------------------|
| Lenses are not statistically independent | Multi-lens agreement overcounts evidence   |
| Elicited probabilities are subjective | No frequentist guarantees on calibration      |
| Dedup is semantic overlap, not rigorous | Near-duplicates can slip through            |
| Consensus is a veto rule, not a posterior | Information in ratios (APPROVE vs MODIFY) is discarded |
| Persona prompts are authored, not learned | Generator behavior drifts with prompt changes |
| Critique is single-round              | Critics do not see each other's objections    |
| Sub-agents see no parent context      | Cannot use conversation state for continuity  |

These are not bugs. They are the price paid for a pipeline that is cheap,
parallelizable, auditable, and deterministic at the DB layer.

---

## 7. Operator guarantees

Because of how the pipeline is structured, a few things are guaranteed.

- **No silent overwrites.** Predictions are append-only (new `cp_NNN` IDs). A
  prior extrapolation cannot be clobbered by a later one.
- **No posterior contamination.** The skill cannot update topic posteriors. It
  writes only to `conditionalPredictions[]` via a dedicated pipeline function.
- **Lock exclusivity.** `sweep_lock` enforces a single active run. Concurrent
  invocations abort immediately.
- **Full reconstruction.** From the DB alone (without any topic JSON), you can
  recover: every proposal ever generated, every verdict ever rendered, every
  critic's narrative for every run, and the link back to the approved
  prediction ID.

---

## 8. Running it

```
/extrapolate generators=GREEN,AMBER
/extrapolate generators=RED,OCHRE topics=hormuz-closure,calibration-fed-rate-2026
```

Arguments:

- `generators=X,Y` — required, exactly 2 from {GREEN, AMBER, BLUE, RED, VIOLET, OCHRE}
- `topics=all` or `topics=slug1,slug2` — optional, defaults to all ACTIVE topics

The pipeline aborts with a clear message if: generator count ≠ 2, any generator
is not in the allowed set, generators are identical, estimated cost exceeds
$10 before meta-critique, or the sweep lock is already held by another run.
