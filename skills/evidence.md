# Skill: Evidence Management

Add evidence to a topic's evidence log with proper provenance, lint it through
the 5 failure modes, and check for contradictions.

## When to use

- Triage returned LOG_EVIDENCE action
- You have new factual information relevant to a topic
- You need to document a development even if it doesn't fire an indicator

## Python calls

### Add evidence

```python
from engine import load_topic, add_evidence, save_topic

topic = load_topic("calibration-topic-slug")

entry = {
    "id": "ev_007",                          # sequential within topic
    "time": "2026-04-12T14:30:00Z",          # when the event occurred (not when logged)
    "text": "Factual description of what happened. No analysis or speculation.",
    "tags": ["EVENT", "DIPLO"],              # from EVIDENCE_TTL tag list
    "source": "Reuters via AP wire",
    "claimState": "SUPPORTED",               # PROPOSED | SUPPORTED | CONTESTED | INVALIDATED
    "weight": 1.0,                           # effective after source trust scaling
    "posteriorImpact": "NONE. H2-directional but no indicator fired. t2_relevant_indicator requires [threshold]. This event is [below/above] that threshold.",
    "note": "Additional context if needed"
}

topic = add_evidence(topic, entry)
save_topic(topic)
```

### Lint the evidence log

```python
from framework.lint import run_lint

result = run_lint("calibration-topic-slug")
# Returns pass/fail for each check:
# - posterior_sum: do posteriors sum to 1.0?
# - evidence_refs: do all evidence entries have required fields?
# - resolution_criterion: is it defined and measurable?
# - submodel_consistency: do submodels align with main model?
```

### Check for contradictions

```python
from framework.contradictions import detect_contradictions

topic = load_topic("calibration-topic-slug")
contradictions = detect_contradictions(topic, new_entry)
# Returns list of contradictions found:
# - negation: new text contradicts existing text
# - numeric: new numbers conflict with existing data
# - feed_mismatch: new data conflicts with dataFeeds values
```

## Evidence entry schema

```json
{
  "id": "ev_NNN",
  "time": "ISO8601",
  "text": "Factual claim. Observable. No rhetoric.",
  "tags": ["TAG1", "TAG2"],
  "source": "Source name",
  "claimState": "PROPOSED | SUPPORTED | CONTESTED | INVALIDATED",
  "weight": 1.0,
  "posteriorImpact": "NONE | description of posterior movement with justification",
  "note": "optional"
}
```

## Valid tags (from governor.py EVIDENCE_TTL)

| Tag | TTL (hours) | Use for |
|-----|-------------|---------|
| EVENT | 72 | Something happened |
| DATA | 168 | Quantitative measurement |
| RHETORIC | 24 | Someone said something |
| INTEL | 72 | Non-public analysis |
| ANALYSIS | 72 | Expert assessment |
| EDITORIAL | 24 | Opinion piece |
| FORECAST | 72 | Prediction |
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

## 5 lint failure modes

Every evidence entry must pass these checks:

1. **rhetoric_as_evidence** — Is this someone's opinion disguised as a fact?
   If the source said "X thinks Y will happen", that's RHETORIC, not EVENT.
   Weight should be reduced.

2. **recycled_intel** — Is this the same information from a previous entry,
   just from a different source? Check existing evidence log for duplicates.
   Multiple sources reporting the same AP wire story = 1 evidence entry, not 3.

3. **anchoring_bias** — Does the posteriorImpact claim a shift that isn't
   justified? If no indicator fired, posteriorImpact should be NONE or
   "NONE pending confirmation." Don't write "Strong H2 signal" when there's
   no mechanism for that signal to move posteriors.

4. **phantom_precision** — Is the entry claiming more precision than the
   source provides? "Exactly 73.2% probability" from a qualitative assessment
   is phantom precision.

5. **stale_evidence** — Is this old information being treated as current?
   Check the timestamp against the tag's TTL.

## Claim lifecycle

```
PROPOSED  → New claim, not yet verified. Weight: 0.5
SUPPORTED → Verified by multiple sources or direct observation. Weight: 1.0
CONTESTED → Contradicted by other evidence. Weight: 0.2
INVALIDATED → Definitively disproven. Weight: 0.0
```

Effective weight = claimState weight * source_trust score.

## posteriorImpact rules

- If NO indicator fires: `"NONE. [Directional assessment]-directional but no indicator fired. [indicator_id] requires [threshold]. This [falls short/does not meet] that threshold."`
- If an indicator FIRES: `"[indicator_id] FIRED. Applied pre-committed effect: [H1 +Xpp, H2 -Ypp, ...]. New posteriors: [values]. Sum = 1.00."`
- Never write vague impact like "Strong signal for H2" or "Moderate impact" — either quantify via a fired indicator or state NONE.
