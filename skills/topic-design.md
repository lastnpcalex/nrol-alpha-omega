# Skill: Topic Design

Create a new calibration topic or modify an existing one. Follows PROTOCOL.md
phases and runs the design gate before activation.

## When to use

- A new "Current Thing" needs tracking
- An existing topic needs hypothesis restructuring
- You need to scaffold a topic from a question

## Python calls

### Quick scaffold

```python
from engine import scaffold_topic

# Generates a blank topic JSON from a slug
json_path = scaffold_topic("calibration-new-topic")
# Creates topics/calibration-new-topic.json with template structure
```

### Full creation

```python
from engine import create_topic, save_topic

topic = create_topic({
    "slug": "calibration-new-topic",
    "title": "New Topic Title",
    "question": "Specific, measurable, time-bounded question?",
    "resolution": "Observable criterion that closes the question",
    "classification": "ROUTINE",
    "hypotheses": {
        # resolution_deadline = startDate + upper bound of label.
        # create_topic auto-stamps these; include them explicitly anyway.
        "H1": {"label": "<3 months",   "prior": 0.40, "midpoint": 45,  "unit": "days",
               "resolution_deadline": "YYYY-MM-DD"},  # startDate + 3mo
        "H2": {"label": "3-12 months", "prior": 0.35, "midpoint": 180, "unit": "days",
               "resolution_deadline": "YYYY-MM-DD"},  # startDate + 12mo
        "H3": {"label": "1-3 years",   "prior": 0.15, "midpoint": 730, "unit": "days",
               "resolution_deadline": "YYYY-MM-DD"},  # startDate + 3yr
        "H4": {"label": ">3 years",    "prior": 0.10, "midpoint": 1460,"unit": "days"},
        # H4 has no deadline — open-ended, can never be falsified by time alone
    },
    "indicators": {
        "tier1_critical": [...],
        "tier2_strong": [...],
        "tier3_suggestive": [...],
        "anti_indicators": [...],
    }
})

save_topic(topic)
```

### Run design gate

```python
from framework.topic_design_gate import run_design_gate

result = run_design_gate(topic)
# Returns:
# {
#   "mechanical": {"passed": bool, "checks": {...}},
#   "review": {"verdict": str, "issues": [...]},
#   "gate_passed": bool,
# }
```

## Design checklist (from PROTOCOL.md)

### Phase 1: Question
- [ ] Specific (not "what happens with X")
- [ ] Measurable (has observable resolution criterion)
- [ ] Time-bounded (explicit deadline or tracking horizon)
- [ ] **`meta.resolutionDate` set** — the date the topic is assessed and a
  winning hypothesis recorded. Distinct from per-hypothesis `resolution_deadline`
  (which falsifies individual time-bound hypotheses early). Past
  `resolutionDate` surfaces as CRITICAL in governance_report until the topic
  is marked RESOLVED. **If missing: `python framework/stamp_resolution_dates.py --topic <slug>`**

### Phase 2: Hypotheses (2-6)
- [ ] Mutually exclusive
- [ ] Collectively exhaustive (posteriors sum to 1.00)
- [ ] Each has midpoint + unit for E[X]
- [ ] Each is falsifiable (anti-indicator exists)
- [ ] Adjacent hypotheses are distinguishable (>20% midpoint separation)
- [ ] Priors are documented in posteriorHistory[0] with justification
- [ ] **Time-bounded hypotheses have `resolution_deadline` set** — any hypothesis
  whose label contains a finite upper bound (`<6 weeks`, `6wk-4mo`, `by Q3 2026`,
  etc.) MUST have `resolution_deadline: "YYYY-MM-DD"` equal to startDate + that
  upper bound. Open-ended hypotheses (`>12mo`, `no recession`) do not need one.
  `create_topic` auto-stamps these, but verify in the JSON before saving.
  **If missing: `python framework/stamp_deadlines.py --topic <slug>`**

### Phase 3: Indicators
- [ ] Each hypothesis has at least 1 pro-indicator
- [ ] Each hypothesis has at least 1 anti-indicator
- [ ] Indicators are observable (actions, not rhetoric)
- [ ] Each has clear fired/not-fired threshold
- [ ] posteriorEffect is pre-committed (declared now, not at fire time)
- [ ] Indicators span multiple evidence categories

### Phase 4: Actor model
- [ ] Key decision-makers identified
- [ ] Decision styles documented
- [ ] Biases and constraints noted

### Phase 5: Data feeds
- [ ] Quantitative metrics with sources and update frequency
- [ ] Baseline values recorded
- [ ] Thresholds defined for indicator triggers

## Phase 6: Cold storage scan

Before finalizing a new topic, scan `canvas/evidence-cold.json` for pre-existing
evidence that matches the new topic's domain, keywords, actors, or regions.

```
For each cold storage entry:
  1. Compare entry keywords/actors/regions against new topic's question, hypotheses,
     and indicator descriptions
  2. If overlap is significant (≥3 keyword matches or actor match + domain match):
     - Log the cold storage claims as evidence in the new topic's evidenceLog
     - Set posteriorImpact based on indicator matching (same rules as live pipeline)
     - Add note: "Retroactive from cold_NNN"
     - Do NOT remove from cold storage — it may match future topics too
  3. Report which cold entries were pulled in and why
```

Cold storage entries carry their original source trust scores. Do not re-assess
source trust — use the values recorded at triage time.

**Limitation**: Cold storage only contains evidence that was triaged through the
pipeline. It is NOT an exhaustive prior evidence scan. Always conduct independent
research when creating a new topic — cold storage supplements, not substitutes.

## Topic JSON structure

See SPEC.md for the full schema. Key sections:
- `meta`: slug, title, question, resolution, status, classification
- `model`: hypotheses (with posteriors), posteriorHistory, expectedValue
- `indicators`: tiers (tier1_critical through anti_indicators)
- `actorModel`: decision-makers and their styles
- `evidenceLog`: all evidence entries
- `dataFeeds`: quantitative metrics
- `dependencies`: upstream/downstream cross-topic links
- `governance`: last governance report snapshot

## Naming convention

Topic slugs follow: `calibration-{descriptive-name}`
Evidence IDs: `ev_NNN` (sequential within topic)
Indicator IDs: `t{tier}_{descriptive_slug}` (e.g., `t1_bilateral_deal_announced`)
