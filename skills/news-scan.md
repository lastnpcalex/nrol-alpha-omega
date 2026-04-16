# Skill: News Scan

Automated multi-topic news sweep. Searches for recent developments across all active topics, triages against indicators, logs evidence, fires indicators where thresholds are met, updates posteriors through Bayes, and runs governance checks.

## When to use

- Periodic scan for new developments (manual or scheduled)
- User invokes `/news-scan`
- Beginning of a new session to catch up on overnight developments

## Steps

DO NOT manually edit JSON files. You MUST use the framework pipeline.

### 1. Load active topics

Load all topic JSONs from `temp-repo/topics/` where `meta.status === "ACTIVE"`.

### 2. Search for news

For each active topic, web search for developments in the last 12 hours. Prioritize topics by classification (ALERT first) and R_t staleness.

### 3. Triage and process evidence

Call `from framework.pipeline import process_evidence, process_headline`.

For each news item found, use `process_headline` to automatically triage and process evidence across all relevant topics, or use `process_evidence` if you are processing a single topic manually.

```python
from framework.pipeline import process_evidence, process_headline, log_activity

# Recommended: use process_headline for automatic triage and processing
# If an indicator fires, likelihoods are derived automatically. 
# If no indicator fires, you MUST provide likelihoods in likelihoods_by_slug to perform Bayesian update.
results = process_headline(
    headline="News headline text",
    source="Source Name",
    likelihoods_by_slug={"topic-slug": {"H1": 0.15, "H2": 0.45, "H3": 0.30, "H4": 0.10}}
)

for res in results:
    log_activity(res, platform="news-scan")
```

### 4. Report what changed

Report a summary table of what changed, including Topic, Evidence Logged, Indicator Fired, Posterior Shift, and Governance Health.
