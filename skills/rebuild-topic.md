# Skill: Rebuild Topic

Walk a topic's `evidenceLog` from design priors to the present under a chosen
lens, deriving honest LRs through **multiple parallel subagents** so the
stochasticity of the model becomes a feature (distributional LR ranges) rather
than a bug. Saves the result as a replay in `canvas/replays/`.

This is the cleanup tool for topics whose live posteriors saturated under
ad-hoc `likelihoods=` updates that bypassed the indicator system. It's also
the calibration tool that fills the (lens × classification) Brier matrix on
already-resolved topics — we replay them under a chosen lens and score the
trajectory against the known resolution.

## When to use

- Operator clicks "REPLAY UNDER LENS" on a topic detail page in the canvas
- A `rebuild_topic` review-action arrives via Loom.send
- You want to generate counterfactual calibration data on a resolved topic

## Hard constraints (do not loosen)

These follow from the red-team pass on the design — DO NOT bypass:

1. **Counterfactual hygiene.** The orchestrator strips `predictionScoring.outcomes` and `meta.resolvedHypothesis` from the topic state passed to subagents, AND filters `evidenceLog` to entries dated ≤ the entry being processed. The agent generating LRs for evidence at time T must not see anything post-T, including future evidence text.
2. **Temporal-future language is scrubbed from visible evidence.** Phrases like "first peer-reviewed", "ahead of", "later confirmed" leak future state. The orchestrator runs a regex/LLM scrub on the visible evidence text before passing to subagents.
3. **`dominantHypothesisStable=False` is a hard block during replay.** The engine flag `is_replay=True` makes the existing `conclusion_sensitive` check a hard failure regardless of the topic's classification. If the dominant hypothesis flips between the lo and hi LR passes, the entry is skipped — don't paper over with a point estimate.
4. **Range too wide skips the entry.** If `max_width > 0.5` on any hypothesis (computed from the N subagent samples), the model can't agree — the entry is skipped with reason `range_too_wide`. Don't run `bayesian_update` on garbage.
5. **All four existing engine gates remain active.** P=0.99/0.01 hard-blocks, `confidence_inflation`, `repetition_as_validation`, and `saturation_redteam_required` all still fire during replay. Replay should be *stricter* than live, never more permissive.

## Pipeline

```python
import copy
from engine import (
    load_topic, reset_to_design_priors, bayesian_update,
    add_evidence, fire_indicator, GovernanceError,
)
from framework.replay_db import (
    init_replay, save_replay, derive_lr_range_from_samples,
)
from framework.scoring import compute_brier_score
```

## Steps

### Step 1 — Setup

Load the topic. Validate the lens against `engine.VALID_LENSES`. Initialize the
replay record:

```python
topic = load_topic(slug)
assert lens in {"GREEN", "AMBER", "BLUE", "RED", "VIOLET", "OCHRE", "OPERATOR_JUDGMENT"}
N_GENERATORS = 3
N_CRITICS = 1
replay = init_replay(slug, lens, N_GENERATORS, N_CRITICS)
```

### Step 2 — Build the in-memory replay topic

```python
replay_topic = reset_to_design_priors(topic)
```

This gives you a topic at design priors with `posteriorHistory` truncated to a
single seed entry and indicator state cleared.

### Step 3 — Strip counterfactual leaks

```python
# Resolution is forbidden during replay
replay_topic.get("meta", {}).pop("resolvedHypothesis", None)
ps = replay_topic.get("predictionScoring") or {}
ps.pop("outcomes", None)
ps.pop("brierScores", None)
```

Sort `evidenceLog` chronologically by `time` (or `id` order if no time).

### Step 4 — For each evidence entry, in chronological order

Let `current_index` be the position in the sorted evidenceLog.

#### 4a — Build the subagent context

```python
visible_evidence = sorted_evidence[:current_index + 1]
target_entry = sorted_evidence[current_index]
# Scrub temporal-future language from target_entry["text"]:
SCRUB_PATTERNS = [
    r"\b(first|earliest|next|subsequent|later|eventually|ahead of|ultimately|finally)\b",
    r"\b(would|will|should|may)\s+(later|eventually|in time|in the end)",
]
visible_text = re.sub("|".join(SCRUB_PATTERNS), "[scrubbed]", target_entry["text"], flags=re.I)
```

The subagents see: lens reasoning frame, `current_posterior`, visible (scrubbed)
evidence entry, and prior evidence entries (also scrubbed if needed).

#### 4b — Spawn N=3 generator subagents in ONE message (parallel)

Each generator receives the same prompt: lens reasoning frame, current posterior,
the scrubbed evidence entry. Each independently returns:

```json
{
  "likelihoods": {"H1": 0.18, "H2": 0.55, "H3": 0.20, "H4": 0.07},
  "reasoning": "AMBER frame: this evidence shows X, which is a regime-change precursor because..."
}
```

Lens reasoning frames (operationalized — give these to the generator subagent):

| Lens | What the reasoning trace MUST include |
|------|----------------------------------------|
| GREEN  | An explicit argument for why continuation is more likely than regime change given this evidence; a baseline rate that this evidence type produces real change. |
| AMBER  | An explicit identification of which regime is changing (or not) and the threshold being crossed (or not); evidence weighted as inflection signal. |
| BLUE   | An identification of the load-bearing systemic constraint and whether this evidence indicates the system is holding or breaking under load. |
| RED    | An explicit articulation of the tail risk being weighted; reasoning for why moderate signals may be undercounted; what the worst case implies. |
| VIOLET | An identification of the actor(s) involved and what each gains/loses by the implied outcome; LR weighted by revealed-preference reasoning. |
| OCHRE  | An identification of long-run structural drivers; explicit discount on surface-level news; reasoning for whether this evidence is signal or noise relative to those structures. |
| OPERATOR_JUDGMENT | Plain operator reasoning. No structured frame; tagged this way for honest calibration. |

Reject any LR with values ≥ 0.99 or ≤ 0.01 in the subagent's own response — re-prompt or drop. The engine will reject these anyway, but catching at the subagent layer reduces wasted work.

#### 4c — Spawn 1–2 critic subagents in ONE message (parallel)

The critic receives the lens frame, the visible evidence, and ALL N generator
proposals. Its job: flag any proposal whose reasoning trace is inconsistent
with the named lens, per the operational frame above. Returns:

```json
{
  "verdicts": [
    {"generator_index": 0, "verdict": "CONSISTENT", "note": "..."},
    {"generator_index": 1, "verdict": "INCONSISTENT", "note": "Proposal claims AMBER but reasons from continuation framing..."}
  ]
}
```

#### 4d — Drop critic-flagged proposals; derive lr_range

```python
surviving = [p for i, p in enumerate(proposals)
             if critic_verdicts[i]["verdict"] == "CONSISTENT"]
if len(surviving) < 2:
    # Not enough independent honest samples; skip with reason
    replay["skips"].append({
        "evidence_id": target_entry["id"],
        "reason": "insufficient_lens_consistent_proposals",
        "detail": f"{len(surviving)}/{N_GENERATORS} survived critic review"
    })
    replay["evidence_skipped"] += 1
    continue

stats = derive_lr_range_from_samples([p["likelihoods"] for p in surviving])
if stats["max_width"] > 0.5:
    # Lens cannot converge on this evidence; skip
    replay["skips"].append({
        "evidence_id": target_entry["id"],
        "reason": "range_too_wide",
        "detail": f"max width {stats['max_width']:.2f} > 0.5",
    })
    replay["evidence_skipped"] += 1
    continue
```

#### 4e — Apply the update through `bayesian_update` with `is_replay=True`

```python
lr_range = {k: [stats["lo"][k], stats["hi"][k]] for k in stats["lo"]}
try:
    replay_topic = bayesian_update(
        replay_topic,
        lr_range=lr_range,
        evidence_refs=[target_entry["id"]],
        reason=f"REPLAY[{lens}] {target_entry['text'][:80]}",
        lens=lens,
        is_replay=True,
    )
    # Capture the new history entry into the replay trajectory
    new_entry = replay_topic["model"]["posteriorHistory"][-1]
    replay["posteriorTrajectory"].append(new_entry)
    replay["evidence_walked"] += 1
except (GovernanceError, ValueError) as e:
    replay["skips"].append({
        "evidence_id": target_entry["id"],
        "reason": type(e).__name__,
        "detail": str(e)[:200],
    })
    replay["evidence_skipped"] += 1
```

If you also want to fire indicators that the evidence implies, do so on the
replay_topic via `fire_indicator` and capture the result the same way. Indicator
firings produce their own `bayesian_update` call which will also be gated.

#### 4f — Optional: record raw subagent audit trail

```python
replay["subagent_proposals"].append({
    "evidence_id": target_entry["id"],
    "generators": [p["likelihoods"] for p in proposals],
    "critic_flags": critic_verdicts,
    "surviving_count": len(surviving),
    "lr_range": lr_range,
})
```

(Drop this for memory if topics are huge — but it's the audit trail.)

### Step 5 — Finalize

```python
replay["finalPosteriors"] = {
    k: round(h["posterior"], 4)
    for k, h in replay_topic["model"]["hypotheses"].items()
}
replay["status"] = "complete"

# If the original topic is RESOLVED, score the replay against the resolution
original = load_topic(slug)
ps = original.get("predictionScoring") or {}
outcomes = ps.get("outcomes") or []
resolved = next(
    (o["resolved"] for o in reversed(outcomes)
     if o.get("resolved") and o.get("type") != "PARTIAL_EXPIRY"),
    None
)
if resolved:
    per_entry = []
    for entry in replay["posteriorTrajectory"]:
        ps_dict = entry.get("posteriors") or {}
        if not ps_dict or resolved not in ps_dict:
            continue
        if ps_dict.get(resolved, 0) >= 0.995:
            continue  # skip near-resolution snapshots
        per_entry.append(compute_brier_score(ps_dict, resolved)["brier"])
    if per_entry:
        replay["brierAtResolution"] = {
            "resolved": resolved,
            "perEntryBrier": [round(b, 4) for b in per_entry],
            "meanBrier": round(sum(per_entry) / len(per_entry), 4),
        }

save_replay(replay)
```

### Step 6 — Activity log + report

Append an activity-log entry summarizing the replay (lens, evidence_walked vs
skipped, finalPosteriors, brierAtResolution if applicable). Report back in
chat: lens used, what changed, what got skipped and why, link to the replay
file.

**Auto-suppress** the originating alert if `context.alert_signature` is set
(per `review-action.md` auto-suppression rule).

## What rebuild does NOT do

- **Does not modify the live topic state.** The replay is a parallel trajectory; the operator chooses whether to promote it via the separate `promote_replay` action.
- **Does not run all 6 lenses at once.** One run = one chosen lens. The operator triggers separate replays under different lenses to compare.
- **Does not infer indicators that should fire.** It uses what the evidence already records about indicator firings via `fired_indicator_id` if present in the evidence entry; it does not invent new firings during replay.

## Promotion (separate action: `promote_replay`)

When the operator decides a replay represents the honest trajectory, they
trigger `promote_replay` with `{run_id, lens, confirm: true}`. The pipeline:

```python
from framework.replay_db import load_replay, promote_replay_to_live
replay = load_replay(slug, lens, run_id)
topic = load_topic(slug)
promote_replay_to_live(topic, replay)  # snapshots prior live to model._preReplayHistory
save_topic(topic)
```

The prior live history is preserved under `topic.model._preReplayHistory` so
the promotion is reversible.
