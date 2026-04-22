# Skill: Extrapolate

Run the conditional-prediction agent pipeline with **operator-chosen generator lenses** and **complement-as-critics** (v0.4 design).

See `OPERATOR_MODEL_DESIGN.md` at the repo root for the full design rationale.

## When to use

- User clicks "UPDATE MODEL" → picks 2 generator lenses → "Run Sweep" on the operator model canvas page
- User types `/extrapolate generators=GREEN,AMBER [topics=all|slug1,slug2]`
- Auto-triggered by schedule (future)

## Parallelization model — READ THIS FIRST

**Ideation phase**: spawn exactly **2 Agent sub-agents in a single message** (one per generator). They run concurrently. Each returns a structured JSON list of proposals. Do NOT loop or call them sequentially.

**Vetting phase**: parent agent vets each proposal with Sonnet, sequentially. This is cheap (one Sonnet call per proposal) and avoids spawning dozens of micro-agents.

**Meta-critique phase**: spawn exactly **5 Agent sub-agents in a single message** (one per critic persona). They run concurrently. Each gets the full vetted-candidate portfolio and returns per-prediction verdicts + a portfolio narrative. Do NOT call them sequentially.

**DB writes**: the parent agent does ALL writes to `extrapolation_db` after collecting sub-agent results. Sub-agents return structured JSON only — they do NOT write to any DB. This prevents SQLite lock contention and keeps the audit trail linear.

## Pipeline overview

```
Operator picks 2 of 6 ideator personas as generators.
The 4 remaining ideators + GRAY automatically become critics (5 total).

Parent: acquire lock, start_run, enumerate topics + hypotheses.

IDEATION — send ONE message with TWO Agent tool calls (parallel Haiku sub-agents):
    Generator A sub-agent → returns JSON proposals[]
    Generator B sub-agent → returns JSON proposals[]

Parent: collect both results. For each proposal, run Sonnet vetting.
        Log all ideations + vetting results to DB.

META-CRITIQUE — send ONE message with FIVE Agent tool calls (parallel Opus sub-agents):
    Critic 1 sub-agent → returns JSON { per_prediction: [...], narrative: "..." }
    Critic 2 sub-agent → returns JSON { per_prediction: [...], narrative: "..." }
    Critic 3 sub-agent → returns JSON { per_prediction: [...], narrative: "..." }
    Critic 4 sub-agent → returns JSON { per_prediction: [...], narrative: "..." }
    Critic 5 sub-agent → returns JSON { per_prediction: [...], narrative: "..." }

Parent: collect all 5. Log critic_verdicts to DB.

Apply consensus rule:
    Prediction is written iff:
        Sonnet verdict is APPROVE or MODIFY, AND
        ≤ 1 of 5 critics DROPped it

Write approved predictions via process_conditional_prediction().
Log portfolio snapshots, finish_run, release_lock.
```

## Ideator personas (operator picks 2)

| Persona | Role | System prompt |
|---------|------|---------------|
| GREEN   | Midtopia / continuation | See `extrapolation-tuning.md` |
| AMBER   | Phase-shift / regime change | See `extrapolation-tuning.md` |
| BLUE    | Systemic resolution | See `extrapolation-tuning.md` |
| RED     | Tail-risk / pessimist | See `extrapolation-tuning.md` |
| VIOLET  | Actor-centric incentives | See `extrapolation-tuning.md` |
| OCHRE   | Structural determinism | See `extrapolation-tuning.md` |

## Critic assignment (automatic)

- The 4 ideators **not picked** as generators automatically become critics
- **GRAY** is always a critic (universal shared-assumption skeptic)
- Total: **5 critics per sweep**

**Examples:**
- Generators `GREEN + BLUE` → Critics: `AMBER, RED, VIOLET, OCHRE, GRAY`
- Generators `GREEN + AMBER` → Critics: `BLUE, RED, VIOLET, OCHRE, GRAY`
- Generators `RED + OCHRE` → Critics: `GREEN, AMBER, BLUE, VIOLET, GRAY`

## Steps to execute when this skill fires

### Step 1 — Setup

Read `temp-repo/skills/extrapolation-tuning.md` to get the current persona system prompts verbatim.

Parse `$ARGUMENTS`:
- `generators=X,Y` — required, exactly 2 from {GREEN, AMBER, BLUE, RED, VIOLET, OCHRE}
- `topics=all` or `topics=slug1,slug2` — optional, defaults to all ACTIVE topics

Abort with clear message if: generators count ≠ 2, any generator not in the set, generators are identical.

```python
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "temp-repo"))

from engine import load_topic
from framework import extrapolation_db as edb
from framework.pipeline import process_conditional_prediction

ALL_IDEATORS = {"GREEN","AMBER","BLUE","RED","VIOLET","OCHRE"}
generators = ["GREEN", "AMBER"]   # from $ARGUMENTS
critics = sorted((ALL_IDEATORS - set(generators)) | {"GRAY"})

edb.init_schema()
run_id = edb.acquire_lock()
if run_id is None:
    print("Another sweep is already running. Aborting.")
    sys.exit(1)

started = time.time()
edb.start_run(run_id, "PICK2", generators, critics, topic_scope)
```

### Step 2 — Enumerate topics and hypotheses

```python
def enumerate_scope(topic_scope):
    """Returns list of {slug, topic_json, active_hypotheses: [{hKey, hyp, posterior}]}"""
    scope = []
    for slug in get_topic_slugs(topic_scope):
        topic = load_topic(slug)
        hyps = topic.get("model", {}).get("hypotheses", {})
        active = [
            {"hKey": k, "hyp": v, "posterior": v["posterior"]}
            for k, v in hyps.items()
            if v.get("posterior", 0) > 0.05 and topic.get("meta", {}).get("status") == "ACTIVE"
        ]
        if active:
            scope.append({"slug": slug, "topic": topic, "hypotheses": active})
    return scope

topic_scope = enumerate_scope(["all"])
existing_preds_by_topic = {
    s["slug"]: s["topic"].get("conditionalPredictions", [])
    for s in topic_scope
}
```

### Step 3 — Parallel ideation (TWO sub-agents in ONE message)

Construct one prompt per generator persona. Each prompt must be fully self-contained — sub-agents see nothing from the parent conversation.

**Send both Agent tool calls in a single message so they run in parallel.**

#### Ideation sub-agent prompt template

```
You are the [PERSONA] lens in the NROL-αΩ conditional-prediction pipeline.

SYSTEM PROMPT:
[paste the persona's system prompt verbatim from extrapolation-tuning.md]

YOUR TASK:
For each (topic, hypothesis) pair below, propose 3–5 conditional predictions.
Each prediction must be falsifiable, have a clear deadline, and a calibrated probability.
Do not duplicate any existing prediction listed under that topic.

TOPICS AND HYPOTHESES:
[paste topic_scope as JSON: slug, title, hypotheses with hKey + label + posterior]

EXISTING PREDICTIONS TO AVOID DUPLICATING:
[paste existing_preds_by_topic as JSON]

OUTPUT FORMAT — return ONLY valid JSON, no other text:
{
  "persona": "[PERSONA]",
  "proposals": [
    {
      "condition_topic_slug": "slug",
      "condition_hypothesis": "H1",
      "prediction_text": "IF H1 holds, THEN ...",
      "resolution_criteria": "Observable threshold: ...",
      "deadline": "YYYY-MM-DD",
      "conditional_probability": 0.72,
      "tags": ["tag1", "tag2"]
    }
  ]
}
```

After BOTH sub-agents return, merge their proposal lists into `all_proposals`.

### Step 4 — Vetting (parent Sonnet loop, sequential)

For each proposal, run Sonnet vetting. Log every ideation and verdict to DB regardless of outcome.

```python
vetted_candidates = []

for proposal in all_proposals:
    iid = edb.log_ideation(
        run_id, proposal["persona"], proposal["condition_topic_slug"],
        proposal["condition_hypothesis"], proposal["prediction_text"],
        proposal["resolution_criteria"], proposal["deadline"],
        proposal["conditional_probability"], tags=proposal.get("tags", []),
        model_name="claude-haiku-4-5"
    )

    verdict = sonnet_vet(proposal, existing_preds_by_topic)
    edb.log_vetting(
        iid, run_id, proposal["persona"], verdict["verdict"],
        reasoning=verdict["reasoning"], sub_checks=verdict.get("sub_checks", {}),
        modified_text=verdict.get("modified_text"),
        model_name="claude-sonnet-4-6"
    )

    if verdict["verdict"] in ("APPROVE", "MODIFY"):
        final_text = verdict.get("modified_text") or proposal["prediction_text"]
        vetted_candidates.append({**proposal, "iid": iid, "prediction_text": final_text})
```

Vetting checklist Sonnet must apply per proposal:
1. **Falsifiable** — resolution criteria name a concrete observable metric + threshold
2. **Deadline realistic** — achievable relative to the topic's horizon
3. **Not a duplicate** — <70% semantic overlap with existing predictions and other proposals
4. **CPT alignment** — conditional probability direction consistent with any relevant CPT bounds
5. **In-scope** — falls within the topic's subject-matter domain

Verdict: `APPROVE` | `REJECT` | `MODIFY` (include revised text/criteria/deadline/probability).

### Step 5 — Parallel meta-critique (FIVE sub-agents in ONE message)

Build `candidate_portfolio` JSON: all vetted candidates with iid, persona, condition fields, text, criteria, deadline, probability.

**Send all five Agent tool calls in a single message so they run in parallel.**

#### Critic sub-agent prompt template

```
You are the [CRITIC_PERSONA] critic in the NROL-αΩ conditional-prediction pipeline.

SYSTEM PROMPT:
[paste this critic's system prompt verbatim from extrapolation-tuning.md]

For ideator personas acting as critics: attack the portfolio from your own lens.
Ask: "Does this prediction reflect a blind spot of my lens? Does it assume something
my lens would challenge? Is the probability too optimistic or pessimistic from my
perspective? Does it miss the mechanism my lens emphasizes?"

YOUR TASK:
Review the [N] candidate predictions below. For each, give a verdict.
Then write a portfolio-level narrative.

CANDIDATE PORTFOLIO:
[paste candidate_portfolio as JSON — include iid, persona, condition_topic_slug,
condition_hypothesis, prediction_text, resolution_criteria, deadline,
conditional_probability]

OUTPUT FORMAT — return ONLY valid JSON, no other text:
{
  "critic": "[CRITIC_PERSONA]",
  "per_prediction": [
    {
      "iid": 42,
      "verdict": "APPROVE",
      "reasoning": "...",
      "modified_suggestion": null
    }
  ],
  "portfolio_narrative": "2–3 sentence description of what this portfolio implies as a worldview.",
  "shared_assumptions": ["assumption 1", "assumption 2"],
  "blind_spots": ["gap 1", "gap 2"]
}

Verdicts: APPROVE | MODIFY (include modified_suggestion) | DROP | NEUTRAL
```

All 5 critics use `claude-opus-4-7`.

### Step 6 — Collect critic results and write to DB (parent, sequential)

```python
for critic_result in all_critic_results:
    critic = critic_result["critic"]
    for pv in critic_result["per_prediction"]:
        edb.log_critic_verdict(
            run_id=run_id,
            ideation_id=pv["iid"],
            critic_persona=critic,
            verdict=pv["verdict"],
            reasoning=pv["reasoning"],
            modified_suggestion=pv.get("modified_suggestion"),
            model_name="claude-opus-4-7"
        )
    edb.log_meta_lint(
        run_id, critic,
        portfolio_narrative=critic_result["portfolio_narrative"],
        shared_assumptions=critic_result.get("shared_assumptions", []),
        blind_spots=critic_result.get("blind_spots", []),
        model_name="claude-opus-4-7"
    )
```

### Step 7 — Apply consensus rule + write approved predictions

```python
def find_converged_lenses(iid, all_vetted):
    this = next(c for c in all_vetted if c["iid"] == iid)
    converged = [this["persona"]]
    for other in all_vetted:
        if other["iid"] == iid or other["persona"] == this["persona"]:
            continue
        if (other["condition_hypothesis"] == this["condition_hypothesis"]
                and other["condition_topic_slug"] == this["condition_topic_slug"]
                and word_overlap(other["prediction_text"], this["prediction_text"]) >= 0.25):
            converged.append(other["persona"])
    return list(set(converged)) if len(converged) > 1 else []

for candidate in vetted_candidates:
    iid = candidate["iid"]
    drops = edb.count_drops_for_ideation(iid)
    if drops > 1:
        continue  # ≥2 critics dropped — consensus failed

    critic_verdicts = edb.get_critic_verdicts_for_ideation(iid)
    lens_agreement = find_converged_lenses(iid, vetted_candidates)

    result = process_conditional_prediction(
        topic_slug=candidate["condition_topic_slug"],
        condition_topic_slug=candidate["condition_topic_slug"],
        condition_hypothesis=candidate["condition_hypothesis"],
        prediction_text=candidate["prediction_text"],
        resolution_criteria=candidate["resolution_criteria"],
        deadline=candidate["deadline"],
        conditional_probability=candidate["conditional_probability"],
        tags=candidate.get("tags", []),
        lens=candidate["persona"],
        critic_verdicts=critic_verdicts,
        lens_agreement=lens_agreement,
        source=f"agent-{candidate['persona'].lower()}",
    )
    edb.log_approved_prediction(
        run_id, iid, result["prediction"]["id"],
        candidate["condition_topic_slug"], "HAIKU_VETTED_PASSED_CRITICS",
        candidate["persona"], lens_agreement=lens_agreement
    )
```

### Step 8 — Portfolio snapshots and finalize

```python
for critic in critics:
    edb.log_portfolio_snapshot(
        run_id, "PICK2",
        critic_narrative=edb.get_meta_lint_narrative(run_id, critic),
        critic_persona=critic
    )

duration = time.time() - started
edb.finish_run(run_id, status="COMPLETED", duration_sec=duration)
edb.release_lock(run_id)
```

### Step 9 — Report to user

After finalization, report:
- Generators used + critics used
- N topics processed, M hypotheses evaluated
- K proposals generated (per generator split), J passed Sonnet vetting, L approved after critic consensus
- Convergence count: predictions where both generators converged (lensAgreement contains both)
- Each critic's `portfolio_narrative` (verbatim, 1–2 sentences each)
- Estimated API cost and total duration

## Hard constraints

- **Never write topic JSON files directly** — always use `process_conditional_prediction()`. Governor guard hook blocks direct writes.
- **Never skip DB logging** — log ALL ideations, ALL Sonnet verdicts, ALL critic verdicts. The audit trail is the whole point.
- **Lock is mandatory** — abort immediately if `acquire_lock()` returns None.
- **Sub-agents return JSON only** — they do NOT write to the DB, do NOT call framework functions, do NOT read local files. All writes happen in the parent after all sub-agents complete.
- **Parallelism is mandatory** — ideation MUST be 2 simultaneous sub-agents in one message. Critique MUST be 5 simultaneous sub-agents in one message. Sequential execution defeats the purpose.
- **Cost cap**: if estimated token cost exceeds $10 before meta-critique, abort with `status=CANCELLED`.
- **Error handling**: on any exception → `edb.finish_run(run_id, status="FAILED", error_text=str(e))` → `edb.release_lock(run_id)` → re-raise.
- **Exactly 2 generators** — abort if 0, 1, or 3+ passed.

## Arguments

$ARGUMENTS
