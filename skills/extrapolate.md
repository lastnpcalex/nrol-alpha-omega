# Skill: Extrapolate

Run the conditional-prediction agent pipeline for a chosen dichotomy.

## When to use

- User clicks "UPDATE MODEL" on the operator model canvas page
- User types `/extrapolate dichotomy=<TRAJECTORY|VALENCE|AGENCY> [topics=<all|slug1,slug2>]`
- Auto-triggered by schedule (future)

## Pipeline overview

Read `temp-repo/skills/extrapolation-tuning.md` first — that file contains
the full persona system prompts. Use them verbatim.

```
For each topic in scope:
    For each ACTIVE hypothesis with posterior > 0.05:
        IDEATION (Haiku)  — each generator persona proposes 3-5 conditional predictions
            (generators = the two opposing personas in the chosen dichotomy)
        VETTING (Sonnet)  — per-proposal: falsifiability, deadline, dedup, CPT alignment
            (vetting persona = the generator's own lens, acts as its own critic first)
After all topics processed:
    META-LINT (Opus, Gray persona) — whole-portfolio critique
    Apply approve/drop/modify from Gray's verdict
    Write final predictions via process_conditional_prediction()
Log everything to framework.extrapolation_db
```

## Python workflow

```python
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "temp-repo"))

from engine import load_topic
from framework import extrapolation_db as edb
from framework.pipeline import process_conditional_prediction

# 1. Acquire lock + start run
edb.init_schema()
run_id = edb.acquire_lock()
if run_id is None:
    print("Another sweep is already running. Aborting.")
    sys.exit(1)

started = time.time()
dichotomy = "TRAJECTORY"  # from $ARGUMENTS
generators = {"TRAJECTORY": ["GREEN","AMBER"], "VALENCE": ["BLUE","RED"], "AGENCY": ["VIOLET","OCHRE"]}[dichotomy]
critics = ["GRAY"]
topic_scope = ["all"]  # or explicit list

edb.start_run(run_id, dichotomy, generators, critics, topic_scope)

# 2. Ideation phase — for each topic, for each generator persona
# The model running this skill should invoke Task/Agent calls for Haiku
# with each persona's system prompt from extrapolation-tuning.md
# For each raw proposal, call edb.log_ideation() and save the ideation_id

# 3. Vetting phase — for each ideation, invoke Sonnet with the vetting checklist
# Log verdict via edb.log_vetting()

# 4. Meta-lint phase — invoke Opus with Gray persona + full candidate list
# Log via edb.log_meta_lint()

# 5. Apply Gray's approve/drop/modify verdicts
# For each approved ideation:
approved_idea_ids = [...]  # from Gray's verdict
for iid in approved_idea_ids:
    # Fetch ideation from DB, apply any modifications
    # Call process_conditional_prediction(...)
    # Log approved_prediction linking back
    pass

# 6. Portfolio snapshot
edb.log_portfolio_snapshot(run_id, dichotomy, portfolio_character="...",
                           critic_narrative="...", critic_persona="GRAY")

# 7. Finalize
duration = time.time() - started
edb.finish_run(run_id, status="COMPLETED", duration_sec=duration)
edb.release_lock(run_id)
```

## Steps to execute when this skill fires

1. **Read skills/extrapolation-tuning.md** to get current persona prompts (they may have been edited).

2. **Parse arguments**: dichotomy (required), topics (optional list or "all").

3. **Acquire lock** via `edb.acquire_lock()`. If None, report "sweep already running" and exit.

4. **Enumerate topics**: load all ACTIVE topic JSONs. For each, list hypotheses with `posterior > 0.05`.

5. **Ideation**: For each (topic, hypothesis, generator_persona) triple:
   - Spawn a Haiku Task/Agent with the persona's system prompt
   - Provide: topic title, question, hypothesis label, current posterior, existing predictions (to avoid dupes), CPT implications for downstream topics
   - Ask for 3-5 structured JSON proposals: `{prediction, resolution_criteria, deadline, conditional_probability, linked_topic_slug?, linked_hypothesis?, tags, reasoning}`
   - Log each proposal via `edb.log_ideation(run_id, persona=..., ...)`
   - Keep ideation_ids for the vetting phase.

6. **Vetting**: For each ideation, spawn a Sonnet Task/Agent:
   - Provide the ideation + the full vetting checklist from extrapolation-tuning.md
   - Ask for: `{verdict: APPROVE|REJECT|MODIFY, sub_checks: {falsifiable, deadline_ok, duplicate, cpt_aligned, scope}, reasoning, modified?: {text, criteria, deadline, probability}}`
   - Log via `edb.log_vetting(ideation_id, ...)`.

7. **Assemble candidate list**: all APPROVED + MODIFIED ideations from vetting. Skip REJECTED.

8. **Meta-lint**: Spawn ONE Opus Task/Agent with Gray persona:
   - Provide: the full candidate list grouped by topic, existing topic posteriors, CPT graph, existing predictions
   - Ask for: `{shared_assumptions, blind_spots, approve_ids, drop_ids, modify_suggestions, gap_fill_suggestions, portfolio_narrative}`
   - Log via `edb.log_meta_lint(run_id, "GRAY", ...)`.

9. **Apply**: For each approve_id from Gray's verdict:
   - Fetch the ideation from DB (apply any Sonnet or Opus modifications)
   - Call `process_conditional_prediction(...)` with the final values
   - Log `edb.log_approved_prediction(run_id, ideation_id, prediction_id=pred["id"], ...)`
   - For gap_fill suggestions: create them fresh and call process_conditional_prediction() for each

10. **Portfolio snapshot**: `edb.log_portfolio_snapshot(run_id, dichotomy, portfolio_character=..., critic_narrative=Gray's narrative, critic_persona="GRAY")`.

11. **Finalize**: `edb.finish_run(run_id, status="COMPLETED", duration_sec=..., cost_usd=...)` and `edb.release_lock(run_id)`.

12. **Report** to the user: summary of N predictions added, M rejected, Gray's portfolio narrative, total cost.

## Constraints

- **Do not write topic JSON files directly** — always use `process_conditional_prediction()` from `framework.pipeline`. The governor guard hook blocks direct writes.
- **Do not skip the DB logging** — the audit trail is the whole point of this pipeline. Log ALL ideations, including rejected ones.
- **Lock is mandatory** — never bypass the sweep_lock. If None returned, abort.
- **Cost cap**: if sweep exceeds $10 in tokens, abort and set status=CANCELLED.
- **Error handling**: on any exception, call `edb.finish_run(run_id, status="FAILED", error_text=...)` and `edb.release_lock(run_id)` before re-raising.

## Arguments

$ARGUMENTS
