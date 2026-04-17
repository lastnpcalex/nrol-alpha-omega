# Skill: Cross-Topic Dependencies

Wire causal relationships between topics with conditional probability tables (CPTs),
detect stale assumptions, and propagate drift alerts when upstream posteriors shift.

## When to use

- A topic's outcome depends on another topic's resolution
- An upstream topic's posteriors have changed and downstream assumptions may be stale
- You need to wire or update a conditional probability table
- After any posterior update, to check downstream impact

## Pipeline call

DO NOT manually edit dependency JSON. Use the pipeline.

```python
from framework.pipeline import process_dependency

# Wire a CPT between two topics
result = process_dependency(
    downstream_slug="calibration-houthi-posture",
    upstream_slug="hormuz-closure",
    conditionals={
        "H1": {
            "H1": 0.40, "H2": 0.30, "H3": 0.15, "H4": 0.15,
            "narrative": "If Hormuz reopens quickly, Houthi attacks resume at high intensity."
        },
        "H2": {
            "H1": 0.25, "H2": 0.40, "H3": 0.25, "H4": 0.10,
            "narrative": "Conditional reopening reduces Houthi strategic leverage, selective targeting."
        },
        "H3": {
            "H1": 0.15, "H2": 0.45, "H3": 0.30, "H4": 0.10,
            "narrative": "Prolonged closure keeps Houthi leverage high but coalition pressure builds."
        },
        "H4": {
            "H1": 0.35, "H2": 0.20, "H3": 0.10, "H4": 0.35,
            "narrative": "Extended closure + Iranian Red Sea threat reactivates Houthi escalation."
        },
    },
    assumption="Iran-US war trajectory shapes Houthi calculus. Hormuz outcome determines whether Houthis escalate, hold selective posture, or de-escalate.",
    tolerance=0.15,
    derivation_method="LLM_INTERPRETED",
)

# Result includes:
# - valid: bool
# - implied_posteriors: advisory — what downstream posteriors SHOULD be given current upstream beliefs
# - governance: health and issues
# - cpt_staleness: whether the CPT's derivation context has changed
```

## Conditional probability table (CPT) format

Each row answers: "If the upstream topic resolves as H_i, what is the implied distribution over this topic's hypotheses?"

```json
{
  "slug": "hormuz-closure",
  "assumption": "MANDATORY narrative describing causal mechanism",
  "tolerance": 0.15,
  "conditionals": {
    "H1": {"H1": 0.40, "H2": 0.30, "H3": 0.15, "H4": 0.15, "narrative": "scenario description"},
    "H2": {"H1": 0.25, "H2": 0.40, "H3": 0.25, "H4": 0.10, "narrative": "scenario description"},
    "H3": {"H1": 0.15, "H2": 0.45, "H3": 0.30, "H4": 0.10, "narrative": "scenario description"},
    "H4": {"H1": 0.35, "H2": 0.20, "H3": 0.10, "H4": 0.35, "narrative": "scenario description"}
  },
  "cptHash": {"upstreamHypotheses": [...], "downstreamIndicatorCount": 7, "derivedAt": "..."},
  "derivationMethod": "LLM_INTERPRETED"
}
```

### Validation rules (enforced by validate_conditionals)
- Each row must sum to 1.00 (±0.005)
- All upstream hypothesis keys must be present as rows
- All downstream hypothesis keys must be present in each row
- No probability may be exactly 0.0 (use 0.01 minimum)
- Matrix must not be uniform (identical rows = no information)

### Derivation methods
- `LLM_INTERPRETED`: derived by suggest_conditionals() or LLM analysis
- `OPERATOR_SUPPLIED`: manually specified by the human operator
- `EMPIRICAL`: calibrated from resolved data (future)

## Advisory implied posteriors

`compute_implied_posteriors()` marginalizes over upstream hypotheses:

    P(downstream_H_j) = Σ_i P(downstream_H_j | upstream_H_i) × P(upstream_H_i)

This is ADVISORY ONLY — it shows what the math suggests, not what the posteriors should be. For topics with multiple upstream dependencies, the function flags the independence assumption explicitly.

## Checking staleness

```python
from framework.dependencies import check_stale_dependencies, propagate_alert
from engine import load_topic

topic = load_topic("calibration-midterms-2026")
stale = check_stale_dependencies(topic)

# After an upstream update:
source = load_topic("calibration-us-recession-2026")
alerts = propagate_alert(source)
```

## Wiring guidelines

1. **Narrative assumption is MANDATORY** — lint fails if empty
2. **Causal, not correlational** — wire for actual mechanisms, not just relatedness
3. **Narrative per CPT row** — each row should explain WHY that upstream state implies those downstream probabilities
4. **Independence warning** — if a topic has multiple upstream deps, implied posteriors assume independence. Flag this.
5. **Advisory, not prescriptive** — implied posteriors inform the operator's judgment, they don't replace it
6. **No dependency laundering** — do not pass implied posteriors as likelihoods to process_evidence(). The lint catches this.
