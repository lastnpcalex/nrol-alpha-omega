# Skill: Triage

Route a new headline or piece of evidence to the correct topic(s) and determine
the appropriate action.

## When to use

- A news headline appears that might be relevant to active topics
- A URL or social media post is submitted for processing
- You need to decide whether new information warrants an update cycle

## Python calls

```python
# Primary entry point — handles everything
from engine import triage_headline
result = triage_headline("headline text here", source="Reuters")

# Returns:
# {
#   "headline": str,
#   "source": str,
#   "matches": [
#     {
#       "slug": str,              # topic slug
#       "relevance": str,         # INDICATOR_MATCH | TOPIC_RELEVANT | IRRELEVANT
#       "action": str,            # UPDATE_CYCLE | LOG_EVIDENCE | MONITOR | REVIEW | IGNORE
#       "explanation": str,
#       "matched_indicators": [],  # which indicators this could fire
#       "pre_committed_effects": [], # what posteriors would shift
#       "dependency_implications": [], # downstream topics affected
#       "rt_status": {},           # R_t regime for this topic
#     }
#   ],
#   "top_action": str,            # highest-priority action across all matches
#   "summary": str,
# }
```

## Action routing

| Relevance | Action | What to do next |
|-----------|--------|-----------------|
| INDICATOR_MATCH | UPDATE_CYCLE | Use the `update-cycle` skill to fire the indicator |
| TOPIC_RELEVANT | LOG_EVIDENCE | Use the `evidence` skill to add to evidence log |
| TOPIC_RELEVANT | MONITOR | Note it, check back later |
| TOPIC_RELEVANT | REVIEW | Needs human judgment before acting |
| IRRELEVANT | IGNORE | No action needed |

## Framework triage internals

The triage function (`framework/triage.py:34`) does:

1. **Keyword extraction** — strips stopwords, extracts meaningful terms
2. **Indicator scan** — checks headline against all active indicators across all topics
3. **Topic matching** — scores relevance by keyword overlap with topic questions,
   hypothesis labels, indicator descriptions, and evidence log
4. **Source trust lookup** — resolves source through the 5-tier trust chain
5. **R_t context** — flags topics that are in DANGEROUS/RUNAWAY regime
6. **Dependency implications** — identifies downstream topics that would be affected

## Constraints

- Triage is READ-ONLY. It does not modify any topic state.
- Do not skip triage and go straight to adding evidence. Triage determines
  the correct action and prevents mis-routing.
- Source trust assessment happens here — if a source is unknown (trust 0.50),
  note that in any subsequent evidence entry.
- If triage returns INDICATOR_MATCH, verify the indicator's observable
  criteria are actually met before proceeding to UPDATE_CYCLE. Triage matches
  on keywords, not on verified observations.

## Mirror dashboard equivalent

The Loom mirror (`loom/mirror.html`) has a client-side triage port in
`triageTopic()` that mirrors `framework/triage.py`. When operating through
the canvas, triage results are displayed in the Triage panel and can be
sent to Claude via `Loom.loadTrigger('pipeline', vars)`.
