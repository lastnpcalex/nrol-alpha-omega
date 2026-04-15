# Skill: News Scan

Automated multi-topic news sweep. Searches for recent developments across all
active topics, triages against indicators, logs evidence, fires indicators where
thresholds are met, and runs governance checks.

## When to use

- Periodic scan for new developments (manual or scheduled)
- User invokes `/news-scan`
- Beginning of a new session to catch up on overnight developments

## Steps (do ALL of them, in order)

### 1. Load active topics

Load all topic JSONs from `temp-repo/topics/` where `meta.status === "ACTIVE"`.
Note the last evidence ID in each topic's `evidenceLog` array.

### 2. Search for news

For each active topic, web search for developments in the last 12 hours.
Prioritize topics by classification (ALERT first) and R_t staleness.
Use topic-specific search queries from `searchQueries` if available,
otherwise construct from the topic question and hypothesis labels.

### 3. Triage each result

For each news item found:

1. Match against the topic's indicator definitions (check observable thresholds)
2. Assess source trust (check `temp-repo/sources/source_db.json`, unknown = 0.50)
3. Check for `recycled_intel` — is this the same AP wire story we already logged?
4. Route: UPDATE_CYCLE / LOG_EVIDENCE / MONITOR / IGNORE

### 4. Log evidence (for LOG_EVIDENCE items)

For each item routed to LOG_EVIDENCE:

```python
from engine import load_topic, add_evidence, save_topic

topic = load_topic("topic-slug")
entry = {
    "id": "ev_NNN",                    # sequential within topic
    "time": "2026-04-14T00:00:00Z",    # when the event occurred
    "text": "Factual description.",
    "tags": ["EVENT", "DIPLO"],
    "source": "Source name",
    "claimState": "SUPPORTED",
    "weight": 1.0,
    "posteriorImpact": "NONE. [direction]-directional but no indicator fired. [indicator_id] requires [threshold]. This [falls short].",
    "note": "Additional context"
}
topic = add_evidence(topic, entry)
save_topic(topic)
```

Run the 5 lint failure modes on each entry:
- `rhetoric_as_evidence`: opinion disguised as fact?
- `recycled_intel`: same AP wire as existing evidence?
- `anchoring_bias`: posteriorImpact claims shift without fired indicator?
- `phantom_precision`: qualitative source claiming quantitative precision?
- `stale_evidence`: timestamp vs tag TTL check

### 5. Fire indicators (for UPDATE_CYCLE items)

For each item where an indicator's observable threshold is actually met:

```python
from engine import load_topic, fire_indicator, update_posteriors, save_topic
from governor import governance_report

topic = load_topic("topic-slug")
topic = fire_indicator(topic, indicator_id="t2_indicator_id",
    note="Observable criteria met: [describe]",
    firedDate="2026-04-14T00:00:00Z")

# Apply ONLY pre-committed posteriorEffect
topic = update_posteriors(topic,
    new_posteriors={"H1": 0.xx, "H2": 0.xx, "H3": 0.xx, "H4": 0.xx},
    reason="Indicator [id] FIRED: [description]. Applied pre-committed effects.",
    evidence_refs=["ev_NNN"])

report = governance_report(topic)
save_topic(topic)
```

### 6. Governance checks

Run governance on every topic that received new evidence or a posterior update:
- Verify posteriors sum to 1.00
- Compute entropy and uncertainty ratio
- Check evidence freshness against tag TTLs
- Flag any CRITICAL health issues

### 7. Dependency checks

For any topic with a posterior change:
- Check `dependencies.upstream` on downstream topics
- If drift exceeds tolerance, flag the stale edge

```python
from framework.dependencies import propagate_alert
propagate_alert("topic-slug")
```

### 8. Update activity log

Append entries to `canvas/activity-log.json` for each topic that was updated.
Use `"platform": "news-scan"` to identify automated scan entries.

### 9. Report summary

Output a concise summary table:

| Topic | Evidence | Indicator | Posterior Change | Health |
|-------|----------|-----------|-----------------|--------|
| slug  | ev_NNN: description | t2_xxx FIRED / none | before → after | HEALTHY |

## Constraints

- Work directly on main — no git branches
- Do NOT update posteriors without a fired indicator or Bayesian likelihood ratios
- Do NOT write "Strong H2 signal" in posteriorImpact when no indicator fired
- Do NOT add evidence without checking for recycled_intel (deduplication)
- Do NOT skip source trust assessment on new evidence
- Do NOT invent shift magnitudes — use only pre-committed posteriorEffect
- Save topic JSONs after all modifications
- Update `meta.lastUpdated` on any topic that receives new evidence
