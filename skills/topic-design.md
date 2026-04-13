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
        "H1": {"label": "Outcome A", "prior": 0.40, "midpoint": 100, "unit": "days"},
        "H2": {"label": "Outcome B", "prior": 0.35, "midpoint": 200, "unit": "days"},
        "H3": {"label": "Outcome C", "prior": 0.15, "midpoint": 50,  "unit": "days"},
        "H4": {"label": "Outcome D", "prior": 0.10, "midpoint": 365, "unit": "days"},
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

### Phase 2: Hypotheses (2-6)
- [ ] Mutually exclusive
- [ ] Collectively exhaustive (posteriors sum to 1.00)
- [ ] Each has midpoint + unit for E[X]
- [ ] Each is falsifiable (anti-indicator exists)
- [ ] Adjacent hypotheses are distinguishable (>20% midpoint separation)
- [ ] Priors are documented in posteriorHistory[0] with justification

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
