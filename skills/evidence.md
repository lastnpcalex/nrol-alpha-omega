# Skill: Evidence Management

Add evidence to a topic's evidence log through the Bayesian pipeline.
Evidence is enriched, checked for contradictions, and used to update
posteriors through Bayes' theorem — all mechanically.

## When to use

- Triage returned LOG_EVIDENCE action
- You have new factual information relevant to a topic
- You need to document a development

## Pipeline call

DO NOT manually edit JSON files. Use the pipeline.

```python
from framework.pipeline import process_evidence, log_activity

# Indicator-match path: provide fired_indicator_id; LRs come from indicator
result = process_evidence(
    slug="topic-slug",
    entry={
        "text": "Factual description of what happened. No analysis or speculation.",
        "source": "Reuters",
        "tag": "EVENT",
        "tags": ["EVENT", "DIPLO"],
        "time": "2026-04-15T12:00:00Z",  # when it happened, not when you're logging
        "note": "Optional context",
    },
    fired_indicator_id="t2_specific_indicator",
    reason="Why this indicator's observable threshold is met."
)

# Park path: no matching indicator, evidence parks
result = process_evidence(
    slug="topic-slug",
    entry={...},
    # no fired_indicator_id → parks; posteriors unchanged
)

log_activity(result, platform="evidence")
```

## What the pipeline does automatically

- **Auto-assigns evidence ID** (sequential ev_NNN)
- **Governor enrichment**: ledger classification, claimState, effectiveWeight
- **Deduplication**: skips if identical text exists in last 10 entries
- **Contradiction detection**: checks against recent evidence, contests if found
- **Source trust lookup**: resolves through 5-tier chain
- **Bayesian update**: computes P(H|E) from your likelihoods, attenuated by source trust
- **Brier snapshot**: records posteriors for calibration scoring
- **Source calibration**: resolves claims, updates source trust
- **Governance report**: full epistemic health check
- **Save with snapshot**: embeds governance into the topic JSON
- **Dependency check**: flags stale downstream assumptions

## Likelihoods come from indicators, not from you

Likelihoods are pre-committed at indicator design time and stored in
`indicator.likelihoods`. When you fire an indicator via `fired_indicator_id`,
the engine reads the indicator's pre-committed LRs and applies them
(attenuated by source trust, decayed by prior firings, de-correlated if
same `causal_event_id`).

If no indicator covers the evidence, **park** by calling
`process_evidence` without `fired_indicator_id`. Posteriors do not move.
The operator runs cleanup later via `skills/cleanup-indicator-sweep.md`.

You do NOT supply freeform LRs. The freeform path was removed because
operators (and AI assistants) used it to commit context-anchored LRs that
pegged 17 topics at clamp ceilings.

## Valid tags (from governor.py EVIDENCE_TTL)

| Tag | TTL (hours) | Use for |
|-----|-------------|---------|
| EVENT | 72 | Something happened |
| DATA | 168 | Quantitative measurement |
| RHETORIC | 24 | Someone said something |
| INTEL | 72 | Non-public analysis |
| ANALYSIS | 72 | Expert assessment |
| EDITORIAL | 24 | Opinion piece |
| FORECAST | 72 | Prediction (from institutional source) |
| PREDICTION | 168 | Testable prediction logged for source calibration |
| POLICY | 720 | Policy/regulatory decision |
| KINETIC | 48 | Military action |
| FORCE | 24 | Force positions |
| DIPLO | 168 | Diplomatic development |
| ECON | 168 | Economic data |
| MARKET | 24 | Market prices |
| POLITICAL | 168 | Political development |
| POLL | 168 | Polling data |
| LEGAL/JUDICIAL/REGULATORY | 720 | Legal/court/regulatory |
| SCIENTIFIC | 720 | Papers, studies |

## Claim lifecycle

```
PROPOSED  → New claim, not yet verified. Weight: 0.5
SUPPORTED → Verified by multiple sources or direct observation. Weight: 1.0
CONTESTED → Contradicted by other evidence. Weight: 0.2
INVALIDATED → Definitively disproven. Weight: 0.0
```

Effective weight = claimState weight * source_trust score.
The pipeline computes this automatically via `add_evidence()`.

## Predictions (testable rhetoric)

Some evidence is a **prediction** — a specific, testable, time-bounded claim.
Predictions don't move posteriors at logging time but calibrate source trust
when resolved.

### Prediction filter — all 3 must be true to tag as PREDICTION

1. **Specific**: a concrete claim, not hedged ("will" not "might")
2. **Testable**: there exists an observable outcome that confirms or refutes it
3. **Time-bounded**: explicit deadline ("by April 20", "within 48 hours")

If any filter fails → tag as RHETORIC, no prediction tracking.

### Prediction schema (extra fields on evidence entry)

```json
{
  "tags": ["PREDICTION"],
  "prediction": {
    "claim": "The specific testable statement",
    "resolvesBy": "ISO8601 deadline",
    "resolutionCriteria": "What counts as confirmed vs refuted",
    "resolution": null,
    "resolvedDate": null
  }
}
```

## 5 lint failure modes (checked automatically by the governor)

1. **rhetoric_as_evidence** — opinion disguised as fact
2. **recycled_intel** — duplicate of existing evidence
3. **anchoring_bias** — shift claimed without mechanism
4. **phantom_precision** — more precision than source provides
5. **stale_evidence** — old information treated as current
