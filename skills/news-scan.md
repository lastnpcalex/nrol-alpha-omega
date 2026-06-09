# Skill: News Scan

Multi-round, multi-subagent news sweep. Per topic, parallel search
subagents (one per hypothesis + one wildcard) surface articles in
isolated contexts. Mutation rounds expand search vocabulary based on
what earlier rounds found. Every article still flows through the same
indicator-or-park gate.

## When to use

- Periodic scan for new developments (manual or scheduled)
- User invokes `/news-scan`
- Beginning of a new session to catch up on overnight developments

## Steps

DO NOT manually edit JSON files. You MUST use the framework pipeline.

### 1. Load active topics

Load all topic JSONs from `temp-repo/topics/` where `meta.status === "ACTIVE"`.

Build an explicit coverage checklist — one row per active topic — and
carry it through the scan. Every topic on this list must be accounted
for in the final report (scanned, or explicitly skipped with a reason).
A partial sweep that silently drops CALIBRATION topics is not an
acceptable `/news-scan` result.

### 2. Set per-topic budget

The scan runs in rounds. Round 1 uses the topic's pre-committed
`searchQueries`; rounds 2+ mutate based on what round 1 surfaced. Budget
controls how many rounds.

```python
from framework.news_mutation import budget_for_scan
max_rounds = budget_for_scan(num_topics)
# 1 topic     -> 3 rounds (deep mutation pays off)
# 2-5 topics  -> 2 rounds (one mutation pass)
# >5 topics   -> 1 round  (pre-committed only; cost-bounded)
```

Time window per topic — **adaptive, not fixed**. The window is computed
as `max(tempo_floor, time_since_last_scan + buffer)`, capped at 30
days. This prevents both wasted compute (re-searching the last 6h when
you scan every 6h) and silent gaps (missing 12h of news when scans run
24h apart).

```python
from framework.news_mutation import compute_time_window
from engine import load_topic

# Tempo floor per topic class — the LOWER bound, not the actual window
TEMPO_FLOORS = {
    "ALERT":       12,        # Hormuz, Ukraine, Houthi, fed-rate, midterms
    "CALIBRATION": 7 * 24,    # 2027-2030 questions, slow-moving trends
    "ROUTINE":     72,        # default catch-all
}

topic = load_topic(slug)
floor = TEMPO_FLOORS[topic_class]   # e.g. 12 for ALERT
window = compute_time_window(topic, tempo_floor_hours=floor)
# window["label"]  -> "last 18 hours" or "last 5 days" — embed in prompts
# window["reason"] -> log in the report
# window["capped"] -> True if hit max_lookback_days (30 by default)

# Pass window["label"] to build_hypothesis_search_prompt(... time_window=window["label"])
```

Why floors stay:
- **ALERT 12h**: even if you scanned 30 minutes ago, look back 12h —
  fast-moving events get re-checked because something might have flipped.
- **CALIBRATION 7d**: hourly news doesn't exist for 2030 questions; a
  12h window almost always returns nothing.
- **ROUTINE 72h**: middle ground for default topics.

**Capped at 30 days**: a topic last scanned 6 months ago doesn't pull
6 months of news — older items are usually already irrelevant or
already-processed. Set `max_lookback_days` if you need different.

### 3. Per-topic multi-round scan

For each topic, run rounds until the stopping criterion fires.

#### Stage 1: Parallel search subagents

Each round spawns N+1 isolated subagents in parallel (where N = number
of hypotheses): one per hypothesis, plus one wildcard. Each gets a
fresh context — no narrative bleed across hypotheses, no anchoring.

```python
from framework.news_mutation import (
    build_hypothesis_search_prompt,
    build_wildcard_search_prompt,
    parse_search_response,
    dedupe_articles,
    filter_novel_articles,
    article_to_evidence_entry,
    round_should_continue,
    compute_time_window,
)
from engine import load_topic

topic = load_topic(slug)
hypotheses = list(topic["model"]["hypotheses"].keys())
prior_articles = []  # accumulates across rounds

# Adaptive window — see step 2. Recompute per topic, not per round.
window = compute_time_window(topic, tempo_floor_hours=floor)
time_window = window["label"]   # e.g. "last 18 hours"

for round_num in range(1, max_rounds + 1):
    # Build prompts: one per hypothesis + wildcard
    prompts = {}
    for h in hypotheses:
        prompts[h] = build_hypothesis_search_prompt(
            topic, h, round_num=round_num,
            time_window=time_window,
            prior_articles=prior_articles,
        )
    prompts["wildcard"] = build_wildcard_search_prompt(
        topic, round_num=round_num,
        time_window=time_window,
        prior_articles=prior_articles,
    )

    # Dispatch ALL subagents in PARALLEL — single message, multiple
    # Agent tool calls. This is the core anti-anchoring property:
    # every channel sees only its own mandate, fully isolated.
    #
    #   Agent(description=f"search {slug} H1 R{round_num}",
    #         prompt=prompts["H1"], subagent_type="general-purpose")
    #   Agent(description=f"search {slug} H2 R{round_num}",
    #         prompt=prompts["H2"], subagent_type="general-purpose")
    #   ... (one per hypothesis)
    #   Agent(description=f"search {slug} wildcard R{round_num}",
    #         prompt=prompts["wildcard"], subagent_type="general-purpose")
    #
    # Collect each subagent's response text into responses[channel].
    pass
```

**Channel mandates differ deliberately**:

- Per-hypothesis channels search for evidence that would update
  H{i} **in either direction** — supporting OR contradicting. This
  framing prevents confirmation-prompted search (the failure mode where
  the subagent only finds H{i}-confirming vocabulary).
- Wildcard channel is **hypothesis-agnostic**: news bearing on the
  topic question, no filter. Catches developments that don't fit any
  existing hypothesis frame — these are the highest-value parked
  entries when they don't fire indicators (schema-gap signals).

#### Stage 2: Dedupe + match per article

```python
# Parse each subagent's response into a structured article list.
# Keys are channel names ("H1", "H2", ..., "wildcard").
parsed = {ch: parse_search_response(text) for ch, text in responses.items()}

# Dedupe across channels. Each unique article carries surfaced_via,
# the list of channels that found it.
deduped, _surf = dedupe_articles(parsed)

# Drop anything already processed in earlier rounds.
novel = filter_novel_articles(deduped, prior_articles)
prior_articles.extend(novel)

# Build the OBSERVE-aware matcher prompt. Indicators with `observable`
# blocks accept OBSERVE actions (continuous partial-LR derivation);
# indicators without remain binary (FIRE on literal threshold match).
from framework.news_observation_pipeline import (
    build_matcher_prompt, parse_matcher_output,
    build_advocate_prompt, parse_advocate_output,
    build_rebut_prompt, parse_rebut_output,
    build_jury_prompt, parse_jury_output,
    apply_decisions, get_parks_with_reasons, get_strict_reasons_map,
    filter_advocate_moves,
)

# Single matcher prompt per topic, batched over all novel articles for
# the topic. (Replaces the per-article match dispatch — fewer subagents,
# same fresh-context isolation since each topic gets its own subagent.)
matcher_prompt = build_matcher_prompt(topic, novel)

# Dispatch one matcher subagent per topic in parallel. Each emits
# DECISION blocks for all articles in its topic. Collect into
# matcher_responses[slug] = response_text.
```

#### Stage 3: Apply decisions via the engine

```python
from framework.news_observation_pipeline import (
    parse_matcher_output, apply_decisions,
)

decisions = parse_matcher_output(matcher_responses[slug])

# Apply each decision through the engine. Routing per kind:
#   OBSERVE  -> apply_observation (mechanical likelihood derivation)
#   FIRE     -> process_evidence(fired_indicator_id=...)
#   PARK     -> process_evidence(fired_indicator_id=None) — flagged for review
#   IGNORE   -> skip
# All existing engine gates apply (clamp, decorrelation, evidence_refs,
# calibrationStatus, lens, lr_decay, confidence_inflation).
summary = apply_decisions(slug=slug, articles=novel, decisions=decisions)
fires_this_round = summary["observe"] + summary["fire"]
parks_this_round = summary["park"]
```

#### Stage 2.5 (optional): Debate cycle on parks

If the strict matcher's PARK count is high relative to the surfaced
article count, an adversarial debate cycle can recover signal that the
strict pass missed without re-introducing vibes-LR. Three fresh-context
subagents per topic — advocate, rebut, jury — operate only on the
PARKed articles. The jury's standing instructions enforce the
framework's burden-of-proof discipline (defaults to KEEP_PARK; advocate
must cite from article, sound inference, value in correct units).

```python
parks = get_parks_with_reasons(decisions)
strict_reasons = get_strict_reasons_map(decisions)

# Round 1 — advocate
advocate_prompt = build_advocate_prompt(topic, novel, parks)
# Dispatch advocate subagent → advocate_response

advocate_blocks = parse_advocate_output(advocate_response)
moves = filter_advocate_moves(advocate_blocks)

# Round 2 — rebut (only if any ARGUE_MOVE)
if moves:
    rebut_prompt = build_rebut_prompt(topic, novel, moves, strict_reasons)
    # Dispatch rebut subagent → rebut_response
    rebuts = parse_rebut_output(rebut_response)

    # Round 3 — jury
    jury_prompt = build_jury_prompt(topic, novel, moves, rebuts)
    # Dispatch jury subagent → jury_response
    jury_verdicts = parse_jury_output(jury_response)

    # Build override map for apply_decisions: only verdicts that MOVE_TO
    # OBSERVE/FIRE supersede the original PARK
    jury_overrides = {
        idx: v["action"]
        for idx, v in jury_verdicts.items()
        if v["action"]["kind"] in ("OBSERVE", "FIRE")
    }

    # Re-apply with overrides: jury-validated OBSERVEs go through the
    # engine; KEEP_PARK verdicts stay parked.
    summary = apply_decisions(
        slug=slug, articles=novel, decisions=decisions,
        jury_overrides=jury_overrides,
    )
```

The debate cycle adds 1-3 dispatches per topic (advocate, rebut, jury)
on top of the matcher. Skip when PARK count is low (e.g., < 5) or for
cost-sensitive scans. The engine's confidence_inflation gate still
applies — debate-validated OBSERVEs at full strength still need ≥ 2
evidence_refs for shifts > 15pp; otherwise the engine refuses, and the
parked entry waits for corroboration in a future scan.

#### Stage 4 (auto-trigger): schema-gap closing loop

After Stage 3 apply, the summary returned by `apply_decisions` includes:

```python
summary["resolver_should_dispatch"]  # bool
summary["resolver_reason"]           # str
```

If True, accumulated `flagged_schema_gaps` on the topic exceeds the
auto-dispatch threshold (default 3). Without intervention, the same
gaps keep recurring scan after scan.

Run the resolver to convert gaps into actionable proposals:

```python
from framework.schema_gap_resolver import (
    cluster_gaps, build_resolver_prompt,
    parse_resolver_proposals, persist_proposals,
)

if summary["resolver_should_dispatch"]:
    topic = load_topic(slug)
    clusters = cluster_gaps(topic)
    if clusters:
        prompt = build_resolver_prompt(topic, clusters)
        # Dispatch fresh-context subagent via Agent tool — the prompt
        # includes per-H coverage and explicitly forbids one-direction
        # bias amplification.
        # response = Agent(description=f"schema-gap resolver {slug}",
        #                  prompt=prompt, subagent_type="general-purpose")
        proposals = parse_resolver_proposals(response)
        # persist with mechanical balance validation; asymmetric_warning
        # proposals get flagged status that requires explicit operator
        # override to apply.
        persist_proposals(slug, proposals, validated=True)
```

Output goes to `topic.governance.proposed_schema_extensions` for
operator review. Approved proposals get applied via the
cleanup-indicator-sweep workflow (cleanup-session opens, indicators
added or observables extended, lint+shape-review run, session closes).

Auto-dispatch threshold (default 3) is configurable per scan. The
intent: known recurring gaps converge over multiple scans rather than
accumulating indefinitely. New unanticipated patterns will always
emerge — that's the world, not the framework — but the framework's
job is to converge on known ones.

**Engine gates still apply**: `bayesian_update` refuses LR ≤ 0.01 / ≥ 0.99,
shifts > 15% need ≥ 2 evidence refs, duplicate text/informationChain
rejected, saturation > 0.85 requires red-team within 30 days. Mutation
expands search coverage, not update authority — there's no bypass.

#### Stage 4: Stopping check

```python
should_continue, reason = round_should_continue(
    round_num=round_num,
    max_rounds=max_rounds,
    novel_article_count=len(novel),
    indicator_fire_count=fires_this_round,
)
if not should_continue:
    break  # round-end log includes `reason`
```

Stops when:
- Hit `max_rounds`, OR
- This round produced 0 novel articles (search exhausted), OR
- After round 1, this round produced 0 indicator fires (diminishing returns)

#### Stage 5: Stamp lastScanned (always)

After all rounds complete for a topic — **even if zero articles were
found, even if every article parked** — stamp the scan completion time
so the next scan has an accurate lower bound for its adaptive window.
Skipping this on "no news" rounds causes the next scan to look back to
whenever you LAST found something, which can be days or weeks, blowing
up the search.

```python
from framework.news_mutation import stamp_last_scanned
stamp_last_scanned(slug)  # writes meta.lastScanned = now
```

### 4. Apply the topic's lens during indicator-match reasoning

Each match subagent reasons about whether an article fires a specific
indicator. The topic's lens shapes how marginal evidence is weighted.
The match subagent should be told the lens; it's already part of
`build_match_prompt` if `topic_meta` carries it, but if not, include
it in `extra_context`.

| Lens   | Reasoning frame applied during indicator match |
|--------|------------------------------------------------|
| GREEN  | Continuation / status quo. Treat regime change as low-likelihood; weight evidence toward existing trends persisting. |
| AMBER  | Phase shift / regime change. Weight evidence as a possible inflection signal; consider whether thresholds are crossed. |
| BLUE   | Systemic resolution. Weight evidence on whether load-bearing systems hold or break under stress. |
| RED    | Tail risk. Weight evidence for downside / catastrophe; treat moderate signals as potentially under-counted. |
| VIOLET | Actor-centric incentives. Weight evidence based on what each actor *gains* by the outcome it implies. |
| OCHRE  | Structural determinism. Weight evidence on long-run structural drivers; discount surface-level news as noise. |
| OPERATOR_JUDGMENT | No structured lens — direct intuition. Tagged this way for honest calibration. |

The engine reads `topic.meta.lens` automatically and stamps `lrSource`
onto each `posteriorHistory` entry. The match decision itself is binary
(INDICATOR/PARK) but the *threshold judgment* on marginal evidence is
where lens matters.

### 5. Report

Coverage table — **one row per active topic**, no omissions. Columns:

| Column | Meaning |
|--------|---------|
| Topic | slug |
| Window | actual window used (`window["label"]`); flag with ⚠ if `window["capped"]` |
| Rounds run | 1, 2, or 3 |
| Stop reason | `max_rounds` / `no_novel` / `no_fires` / `skipped:<reason>` |
| Articles surfaced | total unique across all rounds & channels |
| Channels that fired | which of `{H1..Hn, wildcard}` produced articles |
| Evidence logged | how many entries hit `evidenceLog` |
| Indicators fired | how many indicators fired |
| Schema-gap parks | wildcard-only articles that parked (operator review priority) |
| Posterior shift | per-hypothesis pp delta |
| Governance health | from `governance_report` |

A topic with `SKIPPED` must include the reason. A topic that scanned
but yielded nothing uses `NO_NEW`. An empty row is a bug — surface it.

Cross-check the table's topic count against the active-topic list from
step 1. If they don't match, the scan is incomplete — go back and
finish it.

### 6. After the scan

If `Schema-gap parks` is non-zero on any topic, that's a signal the
indicator schema doesn't cover vocabulary that's actually appearing in
news coverage of the topic question. Surface it to the operator with a
recommendation to run `cleanup-indicator-sweep.md` on those entries —
they'll inform new indicator authoring.

If `Channels that fired` is heavily skewed (e.g. wildcard found 8
articles, hypothesis channels collectively found 2), that's a signal
the hypothesis frame is too narrow — consider whether the topic needs
schema review.

## Why this design

- **Multi-channel breadth**: one search query (the old design) under-
  surfaces; per-hypothesis + wildcard catches more of the relevant
  newsfeed without re-introducing freeform-LR risk.
- **Mutation safety**: creative round-2+ queries are safe because
  posterior changes still require pre-committed indicator fires.
  `causal_event_id`, duplicate-text rejection, and saturation gates
  prevent any path from same-event news → posterior runaway.
- **Bidirectional hypothesis mandate**: "evidence that would update
  H{i} either direction" instead of "evidence supporting H{i}" prevents
  confirmation-prompted search.
- **Wildcard channel**: hypothesis-agnostic breadth catches schema-gap
  developments that the per-hypothesis channels structurally miss.
- **Full subagent isolation**: every search is fresh-context, every
  match is fresh-context. The cross-context anchoring failure mode that
  pegged 17 topics is structurally absent.
- **Bounded cost**: budget scales inversely with scan breadth (3 rounds
  for single-topic deep, 1 round for >5-topic sweep).

## Fallback (no Agent tool available)

If running in a non-conversation context (script, scheduled job),
`process_headline()` without explicit decisions falls back to
embedding-based matching. Use this only for testing or unattended
automation — accuracy is meaningfully lower and the multi-channel
search design doesn't apply.
