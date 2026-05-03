# Skill: Cleanup Indicator Sweep

Operator-driven cleanup. AI orchestrates, operator approves. The only
authorized path for adding indicators, firing indicators against parked
evidence, OR rewriting historical freeform-LR entries.

Two modes:

- **Forward-parked mode** (Mode A) — applies when a topic has
  `governance.flagged_for_indicator_review` populated by post-Phase-1
  pipeline parking. Routine cleanup of evidence that was parked because
  no indicator matched at scan time.
- **Historical-rewrite mode** (Mode B) — applies to topics whose
  `posteriorHistory` carries pre-gate `bayesian_update_legacy` entries
  without indicator binding. These updates already moved posteriors
  (potentially to clamp ceilings) and need rigorous re-evaluation against
  the indicator schema as it currently exists. This is the migration
  pattern used to clean up the 17 originally-saturated topics.

Pick the mode by what state the topic is in. If both are present
(unlikely), do Mode B first since rewriting history affects what should
have been parked vs fired.

## When to use

- **Mode A:** Topic shows `flagged_for_indicator_review` non-empty;
  operator triggers cleanup from the canvas.
- **Mode B:** Topic has saturated/dishonest posteriors derived from
  historical freeform updates (typically diagnosed via
  `framework.meta_health` or per-topic inspection); operator triggers
  cleanup manually in conversation.

Neither mode is for routine evidence processing. Routine processing goes
through `news-scan.md` / `triage.md`, which now park unmatched evidence.

## Rules of engagement (hard)

1. **One active session per topic.** Engine refuses concurrent sessions.
2. **AI does not approve.** AI prepares the proposal envelope; operator
   clicks "Approve & commit" on the canvas to write the receipt; engine
   reads the receipt and applies.
3. **Lint must pass.** Any blocker in `propose_indicators_lint` halts
   commit. Warnings can be acknowledged.
4. **Red and blue team subagents are spawned via Agent tool.** Fresh
   context each. Do not run the team analysis in your own context — that's
   exactly the cross-context anchoring failure mode we're preventing.
5. **Match before authoring.** Most parked evidence has an existing
   indicator match. Use `indicator_match.match_evidence_to_indicators` to
   suggest candidates. Author new indicators only when no existing fits.

## Mode A workflow (forward-parked cleanup)

### Step 1: Open session

```python
from engine import start_indicator_cleanup_session, load_topic
topic = start_indicator_cleanup_session(slug, reason="<why now>")
```

### Step 2: Load parked evidence + existing indicators

```python
flagged_ids = topic["governance"]["flagged_for_indicator_review"]
ev_log = topic["evidenceLog"]
parked = [e for e in ev_log if e.get("id") in flagged_ids]

from framework.indicator_match import collect_topic_indicators
existing_indicators = collect_topic_indicators(topic)
```

Show the operator: count, recent dates, sample notes.

### Step 3: Match each parked entry to existing indicators

```python
from framework.indicator_match import match_evidence_to_indicators

for ev in parked:
    matches = match_evidence_to_indicators(
        evidence_text=(ev.get("text") or "") + " " + (ev.get("note") or ""),
        indicators=existing_indicators,
        evidence_likelihoods=ev.get("operator_likelihoods"),  # if any
        top_n=5,
        score_threshold=0.3,
    )
    # Present matches to operator with score, direction_agreement, indicator_status
```

For each parked entry, the operator decides:
- **Match existing X** → record `(ev_id, indicator_id)` for the firings list
- **Need new indicator** → enter the authoring sub-flow (Step 4)
- **Discard / no impact** → just clear from flagged queue, log reason

### Step 4: Author new indicators (only if no existing match)

For each new indicator the operator wants:

a) Operator describes the observable threshold and direction.
b) AI drafts a candidate: `id`, `tier`, `desc`, `posteriorEffect`,
   `likelihoods` (per hypothesis, max ≤ 0.95).
   MUST also include shape declaration:
   - `shape: "single_observation"`
   - `shape: "per_event_member"` (requires `causal_event_id`)
   - `shape: "ladder_rung"` (requires `ladder_group` and `ladder_step`)
   Do not author "resolution-disguised" indicators (if firing ends the question, it's a resolution trigger, not marginal evidence).

c) Run shape review (Resolution-Disguise Check):
```python
from framework.lint_indicator_shape import build_shape_review_prompt, parse_shape_review_decision, record_shape_review
prompt = build_shape_review_prompt(topic, draft_indicator)
```
Dispatch 2 independent subagents with this prompt.
```python
decisions = [parse_shape_review_decision(r) for r in [resp1, resp2]]
record_shape_review(topic, draft_indicator["id"], decisions)
# Must pass (NOT_RESOLUTION) before adding!
```

d) Run mechanical lint:

```python
from framework.lint_indicators import propose_indicators_lint
lint_result = propose_indicators_lint(topic, [draft_indicator])
```

e) If `lint_result.blockers` non-empty: revise and re-lint.
f) When lint and shape review are clean: continue to red/blue team review (Step 5).

### Step 5: Red/blue team review (per new indicator OR per ambiguous match)

```python
from framework.red_blue_team import (
    get_team_prompts, parse_team_response, format_debate_envelope,
)

prompts = get_team_prompts(
    proposal=draft_indicator,
    topic_meta={"slug": slug, "title": topic["meta"]["title"],
                "question": topic["meta"]["question"],
                "hypotheses": topic["model"]["hypotheses"]},
    budget={"max_turns": 8, "max_web_searches": 5, "max_web_fetches": 3, "max_topic_searches": 10},
)
```

Dispatch each via Claude Code's Agent tool — **fresh context per team**:

```
red_response = Agent(
    description=f"red team for {draft_indicator['id']}",
    prompt=prompts["red"],
    subagent_type="general-purpose",
)
blue_response = Agent(
    description=f"blue team for {draft_indicator['id']}",
    prompt=prompts["blue"],
    subagent_type="general-purpose",
)
```

The subagents have native access to:
- Bash (which can call `python -c "from framework.topic_search import ..."`)
- WebSearch, WebFetch (Claude Code harness tools)
- Read

Parse responses and assemble:

```python
red_report = parse_team_response(red_response)
blue_report = parse_team_response(blue_response)
debate = format_debate_envelope(draft_indicator, red_report, blue_report)
```

Skip Step 5 for high-confidence matches to existing indicators (operator
judgment — but always run for new authorings).

### Step 6: Assemble proposal envelope

```python
envelope = {
    "session_id": topic["governance"]["running_indicator_loop"]["session_id"],
    "topic_slug": slug,
    "created_at": _now_iso(),
    "proposed_indicators": new_indicators,  # only newly authored ones
    "indicator_firings": [
        {"evidence_id": "ev_185", "indicator_id": "iran_reopen_proposal",
         "rationale": "matched via indicator_match (score 0.74, direction-agree)"}
    ],
    "lint_result": lint_result,  # combined from Step 4
    "debate_envelope": debate,   # combined from Step 5
    "operator_notes": "<operator's summary>",
}

# Write to canvas
proposal_path = f"canvas/cleanup-proposals/{slug}__{run_id}.json"
write_json(proposal_path, envelope)
```

### Step 7: Stop. Wait for operator.

The AI ends the conversation here. The operator goes to the canvas, reviews
the proposal envelope (lint output, red/blue debate, planned firings),
clicks "Approve & commit" or "Reject."

Approve writes a receipt at `canvas/cleanup-receipts/<slug>__<run_id>.json`
containing `{timestamp, session_id, approver}`.

### Step 8: Commit (operator-triggered)

When the canvas approval fires, it calls:

```python
from engine import commit_indicator_cleanup
result = commit_indicator_cleanup(
    slug=slug,
    proposal_envelope=envelope,
    canvas_receipt_path=f"canvas/cleanup-receipts/{slug}__{run_id}.json",
)
```

The engine validates:
- Active session (gate from Phase 1)
- `lint_result.passed == True`
- Receipt file exists, fresh (within 10 min by default), session_id matches
- Each firing goes through `apply_indicator_effect` (existing governor checks)
- Flagged evidence ids are removed from the queue as their firings succeed

Session closes automatically on success. Activity logged.

### Step 9: Abort (if operator rejects)

```python
from engine import abort_indicator_cleanup_session
abort_indicator_cleanup_session(slug, reason="<operator rationale>")
```

Clears the session flag without applying changes. Logged in
`governance.indicator_cleanup_history` with `outcome: "aborted"`.

## Mode B workflow (historical-rewrite cleanup)

The `bayesian_update_legacy` entries in `model.posteriorHistory` were
written before the indicator-binding gate landed. Many of them moved
posteriors to clamp ceilings on the strength of LRs that wouldn't pass
today's threshold check. Mode B re-evaluates each legacy entry against
the current indicator schema, reverts the ones that fail, and writes a
single audited correction entry into history.

This is the migration pattern used on `calibration-fed-rate-2026`
(8 entries, all PARK) and `calibration-midterms-2026` (10 entries, all
PARK). Each cleanup of this type writes a `historical_freeform_correction`
entry — grep for that updateMethod to see prior runs.

The Mode B path does NOT use the canvas approval receipt — it's a
one-off correction that applies posteriors directly inside the active
session, then commits the session by summary. (`commit_indicator_cleanup`
is for Mode A's parked-evidence firings; Mode B writes its own history
entry.)

### Step B1: Open session

```python
from engine import start_indicator_cleanup_session, load_topic
topic = start_indicator_cleanup_session(
    slug, reason="historical freeform cleanup"
)
```

### Step B2: Identify legacy entries

```python
ph = topic["model"]["posteriorHistory"]
legacy = [
    (i, e) for i, e in enumerate(ph)
    if e.get("updateMethod") == "bayesian_update_legacy"
]
```

Show the operator: count, date range, current posteriors vs design
priors. If the topic has had an `operator_override` in history that
already reset to priors, flag it — those legacy entries after the
override are the ones still load-bearing.

### Step B3: Build one match prompt per legacy entry

```python
from framework.indicator_match import collect_topic_indicators
from framework.indicator_match_subagent import build_match_prompt

existing_indicators = collect_topic_indicators(topic)
topic_meta = {
    "slug": slug,
    "title": topic["meta"]["title"],
    "question": topic["meta"]["question"],
    "hypotheses": topic["model"]["hypotheses"],
}

prompts = []
for idx, entry in legacy:
    prompt = build_match_prompt(
        headline=entry.get("note", ""),
        source="historical freeform legacy",
        topic_meta=topic_meta,
        indicators=existing_indicators,
        extra_context=f"Legacy entry from {entry.get('date')}; "
                      f"this update moved posteriors to {entry.get('posteriors')}.",
    )
    prompts.append((idx, prompt))
```

### Step B4: Dispatch subagents in parallel

This is the anti-anchoring step. Each entry gets a fresh-context
subagent that judges, against the current indicator schema, whether
the legacy LR shift was actually justified. Spawn all subagents in
**a single message** (multiple Agent tool calls in parallel):

```
Agent(description=f"legacy entry {idx} match", prompt=prompt,
      subagent_type="general-purpose")  # one per (idx, prompt) tuple
```

Do NOT iterate sequentially — that re-introduces context anchoring
across entries and defeats the purpose.

### Step B5: Parse responses

```python
from framework.indicator_match_subagent import parse_match_decision

decisions = {}  # idx -> {"action": "fire"|"park", "indicator_id"|"reason"}
for idx, response_text in collected_responses:
    decisions[idx] = parse_match_decision(response_text)

fires = {i: d for i, d in decisions.items() if d["action"] == "fire"}
parks = {i: d for i, d in decisions.items() if d["action"] == "park"}
errors = {i: d for i, d in decisions.items() if d["action"] == "error"}
```

If any subagent errored, do NOT proceed — re-dispatch those entries
and parse again. Errors must not silently become PARKs.

### Step B6: Compute corrected trajectory

The simplest case (and what fed-rate + midterms both hit): all legacy
entries PARK, so the correction is to reset to design priors (or to
the last `operator_override` if one exists between legacy entries).

```python
ph = topic["model"]["posteriorHistory"]

# Design priors are entry 0 (initialization). Use the latest
# operator_override after that if one exists — that was the operator's
# explicit reset point and should be the replay anchor.
design_priors = dict(ph[0]["posteriors"])
anchor = design_priors
for e in ph:
    if e.get("updateMethod") == "operator_override":
        anchor = dict(e["posteriors"])

# What the topic posteriors are right now (stamped onto the correction
# entry's "priors" field for audit — shows what was overwritten).
current_posteriors = dict(ph[-1]["posteriors"])

if not fires:
    corrected = anchor
else:
    # Replay only the FIRE entries through Bayes from the anchor using
    # each fired indicator's pre-committed likelihoods. This is rare;
    # if you hit it, walk the operator through the math entry-by-entry
    # before applying.
    corrected = _replay_fires_from_anchor(anchor, fires,
                                          existing_indicators)
```

Sanity-check: `corrected` posteriors must sum to 1.00 within rounding.

### Step B7: Present diff to operator

Show:
- Current posteriors (last entry in `posteriorHistory`)
- Corrected posteriors
- Per-entry table: `idx | date | decision (FIRE/PARK) | reason`
- The proposed `note` text for the new history entry

Stop. Wait for explicit "approve" before writing.

### Step B8: Apply correction (on approval)

Append a single `historical_freeform_correction` entry. Do NOT call
`bayesian_update` — that path requires a fired indicator and a single
shift, and we're reverting a chain of past updates.

```python
from datetime import datetime, timezone
from engine import save_topic

ts = datetime.now(timezone.utc).isoformat()
session_id = topic["governance"]["running_indicator_loop"]["session_id"]

correction_entry = {
    "date": ts[:10],
    "timestamp": ts,
    "updateMethod": "historical_freeform_correction",
    "posteriors": corrected,
    "priors": current_posteriors,  # what was overwritten, for audit
    "note": (
        f"HISTORICAL FREEFORM CORRECTION: {len(legacy)} legacy freeform "
        f"entries reviewed by subagents (one per entry, fresh context). "
        f"{len(fires)} FIRE, {len(parks)} PARK. "
        f"Cleanup session: {session_id}."
    ),
    "parked_entry_indices": sorted(parks.keys()),
    "parked_decisions": {str(i): d.get("reason", "")
                         for i, d in parks.items()},
    "lrSource": {
        "lens": "OPERATOR_JUDGMENT",
        "lensSetAt": ts,
        "source": "cleanup-indicator-sweep mode B",
    },
}
topic["model"]["posteriorHistory"].append(correction_entry)

# CRITICAL: also sync hypotheses[H].posterior. The history entry alone
# is not enough — `model.hypotheses[H].posterior` is what governance,
# the canvas, and downstream consumers read as "current". Without this
# sync the topic shows the OLD saturated values everywhere except in
# posteriorHistory.
for h_key, p in corrected.items():
    topic["model"]["hypotheses"][h_key]["posterior"] = p

save_topic(topic)
```

### Step B9: Close session

```python
from engine import commit_indicator_cleanup_session
commit_indicator_cleanup_session(
    slug,
    summary=(
        f"Historical freeform cleanup complete. {len(legacy)} entries "
        f"reviewed, {len(fires)} FIRE, {len(parks)} PARK. "
        f"Posteriors {current_posteriors} -> {corrected}."
    ),
)
```

This appends to `governance.indicator_cleanup_history` with
`outcome: "committed"` and clears the session flag.

### Step B10: Activity log

```python
from framework.pipeline import log_activity
log_activity({
    "timestamp": ts,
    "action": "HISTORICAL_FREEFORM_CORRECTION",
    "topic": slug,
    "summary": correction_entry["note"],
    "source": "cleanup-indicator-sweep-mode-b",
    "platform": "framework",
}, platform="framework")
```

## Common failure modes

- **`IndicatorAddNotAllowed: no active session`** — caller forgot to start
  session, or session expired (1hr TTL). Restart.
- **`lint failed`** — proposed LRs trip phantom_precision, lr_too_certain,
  compound_projection, or direction_drift. Revise the LRs.
- **`Bayesian update blocked by governance: confidence_inflation`** — a
  single firing would shift posteriors >15% with only 1 evidence ref.
  Either group multiple parked evidences for the same indicator into one
  firing, or accept that this single firing must be a smaller LR.
- **`Canvas receipt is stale`** — operator approved >10 min ago. Re-approve.
- **`session_id mismatch`** — proposal envelope was created in a previous
  session. Discard, restart cleanup.
- **Mode B: subagent returned `action: "error"`** — response missing the
  `INDICATOR:` / `PARK:` line. Re-dispatch that single entry; do NOT
  treat error as PARK (silent loss of audit signal).
- **Mode B: corrected posteriors don't sum to 1.0** — replay logic bug
  in `_replay_fires_from_anchor`. Stop, fix the math, do not save the
  topic with degenerate posteriors.
- **Mode B: tried to use `bayesian_update` for the correction** — engine
  refuses (no indicator_id, would also trip confidence_inflation). Use
  the direct `posteriorHistory.append` pattern in Step B8.

## Constraints

- **AI does not run the red/blue team in its own context.** Dispatch via
  Agent tool. The whole point of the structured technique is to prevent
  cross-context anchoring.
- **Direction agreement is mechanical, not narrative.** When matching
  evidence to indicator, check `direction_agreement(ev_lrs, ind_lrs)` —
  if `agreement: False`, the match is suspect even at high cosine
  similarity. Surface this to operator.
- **Author new indicators sparingly.** Most parked evidence has existing
  matches. Adding indicators is a schema commitment — done once, applies
  forward.
- **Group correlated indicators with `causal_event_id`.** If multiple new
  indicators trace to one underlying event (cluster_suspicion warning),
  give them a shared `causal_event_id` so de-correlation kicks in when
  they fire together.
