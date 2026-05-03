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

# For each novel article, dispatch a fresh match subagent against the
# topic's indicator schema. This is the existing per-(article, topic)
# match flow — see framework.indicator_match_subagent.
from framework.indicator_match import collect_topic_indicators
from framework.indicator_match_subagent import (
    build_match_prompt, parse_match_decision,
)

indicators = collect_topic_indicators(topic)
match_prompts = {
    a["url"] or a["headline"]: build_match_prompt(
        headline=a["headline"], source=a["source"],
        topic_meta={
            "slug": slug,
            "title": topic["meta"].get("title", slug),
            "question": topic["meta"].get("question", ""),
            "hypotheses": topic["model"]["hypotheses"],
        },
        indicators=indicators,
    )
    for a in novel
}

# Dispatch all match subagents in parallel (one Agent call per article).
# Each gets fresh context — no anchoring across articles in the round.
# Collect responses into match_responses[key].
```

#### Stage 3: Apply decisions via process_evidence

```python
from framework.pipeline import process_evidence, log_activity

fires_this_round = 0
parks_this_round = 0
schema_gap_parks = 0  # wildcard-only parked entries

for article in novel:
    key = article["url"] or article["headline"]
    decision = parse_match_decision(match_responses[key])

    entry = article_to_evidence_entry(article, round_num=round_num)
    fired_id = decision.get("indicator_id") if decision.get("action") == "INDICATOR" else None

    result = process_evidence(
        slug=slug,
        entry=entry,
        fired_indicator_id=fired_id,
        reason=decision.get("reason") or entry["queryProvenance"],
    )

    if fired_id:
        fires_this_round += 1
    else:
        parks_this_round += 1
        if article.get("surfaced_via") == ["wildcard"]:
            schema_gap_parks += 1

    log_activity(result, platform="news-scan")
```

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
