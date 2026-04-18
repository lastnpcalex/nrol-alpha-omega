# Skill: Extrapolate

Run the conditional-prediction agent pipeline with **operator-chosen generator lenses** and **complement-as-critics** (v0.3 design).

See `OPERATOR_MODEL_DESIGN.md` at the repo root for the full design rationale.

## When to use

- User clicks "UPDATE MODEL" → picks 2 generator lenses → "Run Sweep" on the operator model canvas page
- User types `/extrapolate generators=GREEN,AMBER [topics=all|slug1,slug2]`
- Auto-triggered by schedule (future)

## Pipeline overview

Read `temp-repo/skills/extrapolation-tuning.md` first — it contains the full
persona system prompts. Use them verbatim.

```
Operator picks 2 of 6 ideator personas as generators.
The 4 remaining ideators + GRAY automatically become critics (5 total).

For each topic in scope:
    For each ACTIVE hypothesis with posterior > 0.05:
        IDEATION (Haiku) — each generator persona proposes 3-5 conditional predictions
        VETTING (Sonnet) — per-proposal: falsifiability, dedup, CPT alignment
After all topics processed:
    META-CRITIQUE — 5 Opus calls, one per critic persona, each attacks the
    full candidate portfolio from that critic's lens. Returns per-prediction
    verdicts (APPROVE/MODIFY/DROP/NEUTRAL) + portfolio-level narrative.
Apply consensus rule:
    Prediction is written iff:
        Sonnet verdict is APPROVE or MODIFY, AND
        ≤ 1 of 5 critics DROPPED it (i.e., ≥ 4/5 critics did not drop)
Write approved predictions via process_conditional_prediction() with:
    lens=generator_persona
    critic_verdicts=<full per-critic verdict dict>
    lens_agreement=<list of generators that converged on this prediction>
Log everything to framework.extrapolation_db
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

- The 4 ideators **not picked** by the operator act as critics — each attacks the portfolio from its own lens
- **GRAY** is always a critic (universal shared-assumption skeptic)
- Total: **5 critics per sweep**

**Examples:**
- Operator picks `GREEN + BLUE` → Critics: `AMBER + RED + VIOLET + OCHRE + GRAY`
- Operator picks `GREEN + AMBER` (trajectory opposites) → Critics: `BLUE + RED + VIOLET + OCHRE + GRAY`
- Operator picks `RED + OCHRE` (pessimistic-structural) → Critics: `GREEN + AMBER + BLUE + VIOLET + GRAY`

## Python workflow

```python
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "temp-repo"))

from engine import load_topic
from framework import extrapolation_db as edb
from framework.pipeline import process_conditional_prediction

# --- Setup ---
edb.init_schema()
run_id = edb.acquire_lock()
if run_id is None:
    print("Another sweep is already running. Aborting.")
    sys.exit(1)

started = time.time()
ALL_IDEATORS = {"GREEN","AMBER","BLUE","RED","VIOLET","OCHRE"}
generators = ["GREEN", "AMBER"]  # from $ARGUMENTS, must be exactly 2
assert len(set(generators)) == 2 and set(generators) <= ALL_IDEATORS
critics = sorted((ALL_IDEATORS - set(generators)) | {"GRAY"})  # 5 personas
topic_scope = ["all"]  # or explicit list

edb.start_run(run_id, "PICK2", generators, critics, topic_scope)

# --- Ideation + vetting ---
for topic_slug in enumerate_topics(topic_scope):
    topic = load_topic(topic_slug)
    for h_key in hypotheses_above_threshold(topic, 0.05):
        for gen in generators:
            # Invoke Haiku with gen persona's system prompt
            proposals = haiku_ideate(topic, h_key, persona=gen)
            for prop in proposals:
                iid = edb.log_ideation(run_id, gen, topic_slug, h_key,
                    prop.text, prop.criteria, prop.deadline, prop.prob,
                    tags=prop.tags, model_name="claude-haiku-4-5")
                # Sonnet self-vets (same lens)
                verdict = sonnet_vet(prop, topic, existing_preds=topic.get("conditionalPredictions", []))
                edb.log_vetting(iid, run_id, gen, verdict.verdict,
                    reasoning=verdict.reasoning, sub_checks=verdict.sub_checks,
                    modified_text=verdict.modified_text if verdict.verdict == "MODIFY" else None,
                    model_name="claude-sonnet-4-6")

# --- 5-critic meta-critique (parallel if possible) ---
candidate_ideations = [i for i in edb.get_run_detail(run_id)["ideations"]
                       if sonnet_approved_or_modified(i)]

for critic in critics:
    # One Opus call per critic. Give full candidate portfolio.
    critic_response = opus_critique(candidate_ideations, persona=critic,
                                    full_portfolio_context=True)
    for prediction_critique in critic_response.per_prediction:
        edb.log_critic_verdict(
            run_id=run_id,
            ideation_id=prediction_critique.ideation_id,
            critic_persona=critic,
            verdict=prediction_critique.verdict,
            reasoning=prediction_critique.reasoning,
            modified_suggestion=prediction_critique.modified if prediction_critique.verdict == "MODIFY" else None,
            model_name="claude-opus-4-7"
        )
    # Also log the critic's portfolio-level narrative
    edb.log_meta_lint(run_id, critic, portfolio_narrative=critic_response.narrative,
                      shared_assumptions=critic_response.shared_assumptions,
                      blind_spots=critic_response.blind_spots,
                      model_name="claude-opus-4-7")

# --- Apply consensus rule + write predictions ---
# Consensus: ≤ 1 of 5 critics DROPPED
# Convergence: if another ideation in this run with a DIFFERENT generator
# and similar content was also approved, tag lens_agreement

def find_converged_lenses(iid, all_approved_ideations):
    # Check if any other approved ideation has same condition + similar text
    # from a DIFFERENT generator lens. Return list of lenses that converged.
    # (Simple version: same condition_hypothesis + condition_topic + word overlap ≥ 0.25)
    this_idea = edb.get_ideation(iid)
    this_gen = this_idea["persona"]
    converged = [this_gen]
    for other in all_approved_ideations:
        if other["id"] == iid: continue
        if other["persona"] == this_gen: continue
        if (other["condition_hypothesis"] == this_idea["condition_hypothesis"]
            and other["topic_slug"] == this_idea["topic_slug"]
            and word_overlap(other["prediction_text"], this_idea["prediction_text"]) >= 0.25):
            converged.append(other["persona"])
    return list(set(converged)) if len(converged) > 1 else []

for iid in candidate_ideations:
    drops = edb.count_drops_for_ideation(iid["id"])
    if drops > 1:
        continue  # consensus failed — more than 1 critic dropped

    critic_verdicts = edb.get_critic_verdicts_for_ideation(iid["id"])
    lens_agreement = find_converged_lenses(iid["id"], candidate_ideations)

    result = process_conditional_prediction(
        topic_slug=iid["topic_slug"],
        condition_topic_slug=iid["condition_topic"] if "condition_topic" in iid else iid["topic_slug"],
        condition_hypothesis=iid["condition_hypothesis"],
        prediction_text=iid["prediction_text"],
        resolution_criteria=iid["resolution_criteria"],
        deadline=iid["deadline"],
        conditional_probability=iid["conditional_probability"],
        tags=json.loads(iid["tags"] or "[]"),
        lens=iid["persona"],
        critic_verdicts=critic_verdicts,
        lens_agreement=lens_agreement,
        source=f"agent-{iid['persona'].lower()}",
    )
    edb.log_approved_prediction(run_id, iid["id"], result["prediction"]["id"],
        iid["topic_slug"], "HAIKU_VETTED_PASSED_CRITICS", iid["persona"],
        lens_agreement=lens_agreement)

# --- Portfolio snapshot per critic ---
for critic in critics:
    # Each critic's narrative was logged in meta_lint; pull it for the snapshot
    edb.log_portfolio_snapshot(run_id, "PICK2",
        critic_narrative=edb.get_meta_lint_narrative(run_id, critic),
        critic_persona=critic)

# --- Finalize ---
duration = time.time() - started
edb.finish_run(run_id, status="COMPLETED", duration_sec=duration)
edb.release_lock(run_id)
```

## Steps to execute when this skill fires

1. **Read skills/extrapolation-tuning.md** to get current persona prompts.
2. **Parse arguments**: `generators=X,Y` (required, exactly 2 from the 6 ideators), `topics=...` (optional).
3. **Acquire lock** via `edb.acquire_lock()`. If None, abort with clear message.
4. **Derive critics** = `{all 6 ideators} - {picked generators} | {GRAY}`. Exactly 5 critics.
5. **Enumerate topics**: all ACTIVE topic JSONs, list hypotheses with posterior > 0.05.
6. **Ideation phase**: for each (topic, hypothesis, generator_persona), invoke Haiku with that persona's system prompt. Request 3-5 structured JSON proposals.
7. **Vetting phase**: for each ideation, Sonnet runs the vetting checklist.
8. **Meta-critique phase**: for each of the 5 critics, invoke Opus once with the full candidate portfolio + critic persona's system prompt. Returns per-prediction verdicts + portfolio narrative.
9. **Apply consensus**: prediction kept iff Sonnet approved/modified AND ≤ 1 critic dropped.
10. **Detect convergence**: for each kept prediction, find other kept predictions with different generator lens + same condition + similar text. Tag `lens_agreement`.
11. **Write approved predictions** via `process_conditional_prediction()` with `lens`, `critic_verdicts`, `lens_agreement`, `source=agent-<generator>`.
12. **Log portfolio snapshots** one per critic persona with that critic's narrative.
13. **Finalize**: `edb.finish_run()`, `edb.release_lock()`.
14. **Report** to the user: generators used, critics used, N added / M rejected, each critic's portfolio narrative, total cost.

## Hard constraints

- **Never write topic JSON files directly** — always use `process_conditional_prediction()` from `framework.pipeline`. Governor guard hook blocks direct writes.
- **Never skip DB logging** — log ALL ideations, ALL Sonnet verdicts, ALL critic verdicts. The audit trail is the whole point.
- **Lock is mandatory** — abort if `acquire_lock()` returns None.
- **Cost cap**: abort and `status=CANCELLED` if token cost estimate exceeds $10.
- **Error handling**: on any exception → `edb.finish_run(run_id, status="FAILED", error_text=...)` and `edb.release_lock(run_id)` before re-raising.
- **Exactly 2 generators** must be picked. Abort if 0, 1, or 3+ passed.

## Arguments

$ARGUMENTS
