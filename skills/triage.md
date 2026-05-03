# Skill: Triage

Route a new headline or piece of evidence to the correct topic(s) and determine
the appropriate action. Then process it through the Bayesian pipeline.

## When to use

- A news headline appears that might be relevant to active topics
- A URL or social media post is submitted for processing
- You need to decide whether new information warrants an update cycle

## Pipeline call

DO NOT manually edit JSON files. Use the pipeline.

There are exactly two outcomes per (evidence, topic) pair:

1. **Indicator match** — fire the matching indicator with its pre-committed LRs
2. **No match** — park the evidence; operator resolves later via `cleanup-indicator-sweep.md`

There is no freeform-LR path. `bayesian_update` requires `indicator_id`.

```python
from framework.pipeline import process_evidence, log_activity

# Indicator-match path: triage identified an indicator's observable threshold met
result = process_evidence(
    slug="topic-slug",
    entry={"text": "...", "source": "Reuters", "tag": "EVENT"},
    fired_indicator_id="t2_xxx",  # the matching indicator
)
# Indicator fires with pre-committed LRs. Posteriors update.

# Park path: no indicator matched at scan time
result = process_evidence(
    slug="topic-slug",
    entry={"text": "...", "source": "Reuters", "tag": "EVENT"},
    # no fired_indicator_id → evidence parks
)
# Evidence logged with posteriorImpact: NONE — flagged for indicator review.
# Posteriors do NOT update. Topic accumulates parked entries until cleanup.

log_activity(result, platform="triage")
```

### Verifying an indicator match before firing

Triage matches on keywords. The indicator's *observable threshold* is a
specific testable condition (e.g., "core CPI YoY ≥ 3.0% for 2 consecutive
months"). Before passing `fired_indicator_id`, confirm the threshold is
actually met. If you're not sure, park instead — the cleanup workflow can
re-evaluate later with operator judgment.

### Subagent-mediated matching (forward-pipeline anti-anchoring)

When processing news headlines across multiple topics in one scan, **do not
generate match decisions in your own context across all topics in sequence**.
That's the cross-context anchoring failure mode that pegged 17 topics. Use
the `Agent` tool to spawn a fresh subagent per (headline, topic) match
decision, or per topic. The subagent has fresh context, no narrative
inheritance from prior decisions in the sweep.

For multi-topic sweeps, see `skills/news-scan.md` for the orchestration
pattern.

## Action routing

| Relevance | Action | Pipeline call |
|-----------|--------|---------------|
| INDICATOR_MATCH | UPDATE_CYCLE | `process_evidence(slug, entry, fired_indicator_id="t2_xxx")` |
| TOPIC_RELEVANT | PARK | `process_evidence(slug, entry)` — no fired_indicator_id, evidence parks |
| TOPIC_RELEVANT | MONITOR | Note it — no pipeline call needed |
| IRRELEVANT | IGNORE | No action needed (cold storage logged automatically) |

## Constraints

- Triage is the first step — do not skip it and go straight to evidence.
- Source trust assessment happens automatically in the pipeline via the 5-tier chain.
- If triage returns INDICATOR_MATCH, verify the indicator's observable criteria
  are actually met before passing `fired_indicator_id` to process_evidence.
  Triage matches on keywords, not on verified observations.
- **Never invent likelihoods.** If no indicator covers the evidence, the
  correct action is to park, not to commit operator-imagined LRs. Operator
  resolves the parked queue via `cleanup-indicator-sweep.md`.
