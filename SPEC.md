# NRL-Alpha Omega — Generalized Epistemic Bayesian Estimator

## Overview

A topic-agnostic, epistemic Bayesian estimation framework for tracking any "Current Thing" — any predictive news question with measurable resolution criteria. Generalizes the Hormuz Crisis Monitor's analytical patterns into a reusable engine that can spin up a new estimator for any topic in minutes.

Borrows epistemic governance concepts from the Agent Governor framework: claim provenance, evidence coupling, confidence management, and the principle that **natural language is a proposal, not an authority** — only verified evidence moves posteriors.

---

## Core Abstraction

Any "Current Thing" can be decomposed into:

```
TOPIC
├── question          # What are we estimating?
├── resolution        # How do we know it's resolved?
├── hypotheses[]      # Mutually exclusive, exhaustive outcomes
├── indicators[]      # Observable signals that shift posteriors
├── actorModel{}      # Decision-theoretic framework for key actors
├── evidenceLog[]     # Timestamped, tagged, provenance-tracked entries
├── dataFeeds{}       # Quantitative observables (markets, polls, metrics)
└── watchpoints[]     # Near-term checkpoints to monitor
```

---

## Architecture

```
NRL-Alpha Omega/
├── SPEC.md                # This file
├── engine.py              # Bayesian update engine + evidence manager
├── server.py              # Multi-topic HTTP server
├── dashboard.html         # Generalized dashboard (reads any topic state)
├── topics/                # One JSON file per active topic
│   ├── _template.json     # Template for creating new topics
│   └── {topic-slug}.json  # Active topic state files
├── briefs/                # Output briefings (topic-slug/YYYY-MM-DD-HHMM.md)
└── logs/                  # Execution logs
```

---

## State Schema (topics/{slug}.json)

```json
{
  "meta": {
    "slug": "string — URL-safe identifier",
    "title": "string — Human-readable topic name",
    "question": "string — The predictive question being estimated",
    "resolution": "string — Observable criterion that resolves the question",
    "created": "ISO8601",
    "lastUpdated": "ISO8601",
    "status": "ACTIVE | RESOLVED | SUSPENDED",
    "dayCount": 0,
    "startDate": "ISO8601 — When tracking began",
    "classification": "ROUTINE | ELEVATED | ALERT"
  },

  "model": {
    "hypotheses": {
      "H1": { "label": "string", "midpoint": 0, "unit": "string", "posterior": 0.0 },
      "H2": { "label": "string", "midpoint": 0, "unit": "string", "posterior": 0.0 },
      "...": "N hypotheses, posteriors must sum to 1.0"
    },
    "expectedValue": 0.0,
    "expectedUnit": "string — weeks, %, dollars, etc.",
    "posteriorHistory": [
      { "date": "ISO8601", "H1": 0.0, "H2": 0.0, "note": "reason for update" }
    ]
  },

  "subModels": {
    "optional — additional conditional/scenario models": {
      "scenarios": {},
      "conditionals": {}
    }
  },

  "indicators": {
    "tiers": {
      "tier1_critical": [
        {
          "id": "string",
          "desc": "string — observable condition",
          "status": "NOT_FIRED | PARTIAL | FIRED",
          "firedDate": "ISO8601 | null",
          "note": "string | null",
          "posteriorEffect": "description of how firing affects posteriors"
        }
      ],
      "tier2_strong": [],
      "tier3_suggestive": [],
      "anti_indicators": []
    }
  },

  "actorModel": {
    "description": "Decision-theoretic framework for the key actors",
    "actors": {
      "actor_id": {
        "name": "string",
        "role": "string",
        "decisionStyle": "string — behavioral model",
        "biases": ["string — known cognitive biases or fixations"],
        "filters": ["string — institutional filters on actor behavior"],
        "overrides": ["string — conditions where filters bypass"]
      }
    },
    "methodology": [
      "ACTIONS OVER RHETORIC — only verified events move posteriors",
      "TAG EVERYTHING — separate rhetoric from action",
      "DON'T FRONT-RUN — let indicators confirm, don't predict ahead",
      "SOCIALIZATION DETECTION — recognize narrative prep patterns"
    ]
  },

  "evidenceLog": [
    {
      "time": "ISO8601",
      "tag": "string — category tag (KINETIC, ECON, DIPLO, POLL, DATA, RHETORIC, INTEL, etc.)",
      "text": "string — what happened",
      "provenance": "OBSERVED | RETRIEVED | USER_PROVIDED | DERIVED",
      "source": "string | null — where this came from",
      "posteriorImpact": "NONE | MINOR | MODERATE | MAJOR"
    }
  ],

  "dataFeeds": {
    "feed_id": {
      "label": "string",
      "value": 0,
      "unit": "string",
      "baseline": 0,
      "asOf": "ISO8601"
    }
  },

  "watchpoints": [
    {
      "time": "string — when to check",
      "event": "string — what's happening",
      "watch": "string — what to look for"
    }
  ]
}
```

---

## Bayesian Update Engine (engine.py)

### Core Operations

1. **`load_topic(slug)`** — Load a topic state from disk
2. **`update_posteriors(topic, updates)`** — Apply Bayesian updates
   - Enforces: all posteriors sum to 1.0
   - Recomputes expected value: `E[X] = Σ(posterior_i × midpoint_i)`
   - Appends to posteriorHistory with reason
   - Validates monotonic evidence (no repetition-as-validation)
3. **`fire_indicator(topic, indicator_id, note)`** — Mark an indicator as fired
   - Timestamps the firing
   - Suggests posterior adjustment based on tier
4. **`add_evidence(topic, entry)`** — Add to evidence log with provenance
5. **`compute_classification(topic)`** — Determine ROUTINE/ELEVATED/ALERT from indicator state
6. **`save_topic(topic)`** — Write state back to disk
7. **`create_topic(config)`** — Initialize a new topic from template
8. **`generate_brief(topic, mode)`** — Produce a structured briefing markdown

### Epistemic Safeguards (from Agent Governor)

- **Claim-Evidence Coupling**: Every posterior update must reference specific evidence entries
- **Provenance Tracking**: Each evidence entry tagged with how it was obtained
- **Confidence Gates**: Major posterior shifts (>10pp) require Tier 1 or 2 indicator support
- **Stale Evidence Detection**: Evidence older than topic-configured TTL gets flagged
- **No Repetition-as-Validation**: Same evidence cited twice does not compound confidence

### Update Procedure (per cycle)

```
1. Load topic state
2. Search for news (web search queries derived from topic config)
3. Check indicators against new evidence
4. If indicators fired → compute posterior update
5. If no indicators → HOLD posteriors
6. Add new evidence to log with provenance tags
7. Compute classification
8. Generate briefing
9. Save updated state
```

---

## Indicator Tier System

| Tier | Name | Posterior Effect | Classification |
|------|------|-----------------|---------------|
| 1 | Critical | Major shift (15-30pp) | → ALERT |
| 2 | Strong | Moderate shift (5-15pp) | → ELEVATED |
| 3 | Suggestive | Minor shift (1-5pp) or thesis confirmation | unchanged |
| Anti | Contrary | Reversal proportional to tier equivalent | may downgrade |

When indicators fire:
- **Tier 1**: ALERT mode. Major posterior update. Increase check frequency.
- **Tier 2**: ELEVATED mode. Moderate update. Flag for attention.
- **Tier 3**: Note and minor adjustment. Thesis validation.
- **Anti-indicator**: Reduce probability of primary thesis. If primary drops below 20%, flag thesis for structural review.

---

## Evidence Tags (extensible per topic)

Core tags available to all topics:
- **DATA** — Quantitative measurements, statistics, official numbers
- **EVENT** — Observable occurrences (actions, movements, incidents)
- **POLICY** — Official decisions, laws, regulations, orders
- **RHETORIC** — Statements, threats, promises (discounted by default)
- **INTEL** — Analysis, pattern recognition, socialization signals
- **ECON** — Market data, economic indicators
- **DIPLO** — Diplomatic actions and communications

Topics can define additional custom tags.

---

## Dashboard

Single-page app that renders any topic state file. Key panels:

1. **Header**: Topic title, question, classification, day count, last updated
2. **Posterior Bar**: Stacked bar chart of current hypothesis posteriors
3. **History Chart**: Line chart of posterior evolution over time
4. **Sub-Models**: Conditional/scenario models (if present)
5. **Indicators**: Tiered status display with color coding
6. **Data Feeds**: Key quantitative observables
7. **Evidence Log**: Latest entries with tag coloring
8. **Watchpoints**: Near-term checkpoints
9. **Actor Model**: Key actors and their decision frameworks (collapsible)

**Topic Selector**: Dropdown or tabs for switching between active topics.

Dark theme, monospace, intelligence-assessment aesthetic. 60-second auto-refresh.

---

## Server

Python HTTP server serving:
- `GET /` → Dashboard
- `GET /topics` → List of active topic slugs
- `GET /topics/{slug}/state.json` → Topic state
- `GET /topics/{slug}/briefs/` → Briefing list
- Static assets

Port configurable, default 8098 (one below Hormuz at 8099).

---

## Creating a New Topic

1. Copy `topics/_template.json`
2. Fill in: slug, title, question, resolution criterion
3. Define hypotheses (2-6 recommended, with midpoints and units)
4. Define indicators per tier
5. Define actor model (who makes the decisions, what are their biases)
6. Set initial priors
7. Configure data feeds and search queries
8. Save as `topics/{slug}.json`

The engine validates the state on load and rejects malformed topics.

---

## Briefing Format

```markdown
# {TOPIC TITLE} BRIEF — {Date} {Time}
## Classification: {ROUTINE | ELEVATED | ALERT}
## Day {N} since tracking began

### BREAKING / NEW DEVELOPMENTS
[Bullet summary of new evidence, tagged by type]

### INDICATOR STATUS
[Any changes to indicator status]

### DATA FEEDS
[Current values of tracked quantitative observables]

### POSTERIORS
{H1}={X}% {H2}={X}% ... | E[{unit}]={X}
[If changed: UPDATED — reason]
[If unchanged: HELD — no new indicators]

### SUB-MODELS
[Conditional/scenario model updates if applicable]

### KEY WATCHPOINTS NEXT 12-24H
[What to monitor in the next cycle]
```

---

## Design Principles

1. **Topic-agnostic**: The engine knows nothing about geopolitics, markets, or any domain. All domain knowledge lives in the topic config.
2. **Evidence-first**: Posteriors only move on evidence, never on vibes or repetition.
3. **Transparent**: Every posterior update is logged with its reason and supporting evidence.
4. **Calibrated**: The indicator tier system provides guardrails against over-updating.
5. **Composable**: Sub-models allow conditional reasoning without polluting the main model.
6. **Minimal**: No dependencies beyond Python stdlib. Dashboard is vanilla JS.

---

## Relationship to Hormuz Monitor

NRL-Alpha Omega generalizes the Hormuz Crisis Monitor. The Hormuz monitor can be expressed as a single topic config within this framework. The key abstractions lifted:

- `hormuzModel.hypotheses` → `model.hypotheses`
- `meuModel` → `subModels`
- `tripwires` → `indicators.tiers`
- `forceTracker` + `marketData` + `casualties` → `dataFeeds`
- `intelLog` → `evidenceLog` (with added provenance)
- `trumpUltimatum` → topic-specific `dataFeeds` entry
- Decision-theoretic framework → `actorModel`

## Relationship to Agent Governor

Borrows epistemic patterns but not the full governance stack:
- **Claim-Evidence Coupling** → posterior updates require evidence references
- **Provenance Tracking** → evidence entries carry source metadata
- **Confidence Gates** → tier system limits magnitude of updates
- **Stale Evidence** → TTL-based evidence freshness
- **No Repetition-as-Validation** → deduplication in update engine
