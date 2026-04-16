# Skill: Triage

Route a new headline or piece of evidence to the correct topic(s) and determine
the appropriate action. Then process it through the Bayesian pipeline.

## When to use

- A news headline appears that might be relevant to active topics
- A URL or social media post is submitted for processing
- You need to decide whether new information warrants an update cycle

## Pipeline call

DO NOT manually edit JSON files. Use the pipeline.

```python
from framework.pipeline import process_headline, log_activity

# process_headline does triage + evidence + Bayes + governance in one call.
# You MUST supply likelihoods for each matched topic to get a Bayesian update.
# If an indicator fires, likelihoods are derived automatically from its pre-committed effect.
results = process_headline(
    headline="headline text here",
    source="Reuters",
    likelihoods_by_slug={
        "topic-slug": {"H1": 0.15, "H2": 0.45, "H3": 0.30, "H4": 0.10}
    }
)

for res in results:
    log_activity(res, platform="triage")
```

If you need triage results WITHOUT processing (read-only), use the engine directly:

```python
from engine import triage_headline
result = triage_headline("headline text here", source="Reuters")
# Returns matches with relevance, action, matched_indicators, watchpoints, etc.
```

## Likelihood guidance

When supplying `likelihoods_by_slug`, ask: "How likely is this evidence if H_i is true?"

- P(E|H) close to 1.0 = this evidence is exactly what you'd expect under H
- P(E|H) close to 0.0 = this evidence would be very surprising under H
- All likelihoods must be in (0, 1]. They do NOT need to sum to 1.
- The pipeline attenuates likelihoods by source trust and claim state weight automatically.

Example: "3,000 vessels backed up, months to restore traffic"
- P(E|H1 <6wk) = 0.02 — almost impossible if closure is short
- P(E|H2 6wk-4mo) = 0.15 — unlikely but possible with fast resolution
- P(E|H3 4-12mo) = 0.60 — very consistent with extended closure
- P(E|H4 >12mo) = 0.50 — consistent but not more likely than H3

## Action routing (for reference)

| Relevance | Action | Pipeline call |
|-----------|--------|---------------|
| INDICATOR_MATCH | UPDATE_CYCLE | `process_evidence(slug, entry, fired_indicator_id="t2_xxx")` |
| TOPIC_RELEVANT | LOG_EVIDENCE | `process_evidence(slug, entry, likelihoods={...})` |
| TOPIC_RELEVANT | MONITOR | Note it — no pipeline call needed |
| IRRELEVANT | IGNORE | No action needed |

## Constraints

- Triage is the first step — do not skip it and go straight to evidence.
- Source trust assessment happens automatically in the pipeline via the 5-tier chain.
- If triage returns INDICATOR_MATCH, verify the indicator's observable criteria
  are actually met before passing `fired_indicator_id` to process_evidence.
  Triage matches on keywords, not on verified observations.
