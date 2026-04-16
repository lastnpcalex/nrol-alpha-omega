# Skill: Update Cycle

Fire an indicator and update posteriors through the Bayesian pipeline.

## When to use

- Triage returned INDICATOR_MATCH and you've verified the observable criteria
- A pre-registered indicator's threshold has been met
- You need to apply a Bayesian update with likelihood ratios

## Pipeline call

DO NOT manually edit JSON files. Use the pipeline.

```python
from framework.pipeline import process_evidence, log_activity

# Fire an indicator — likelihoods derived automatically from pre-committed effect
result = process_evidence(
    slug="topic-slug",
    entry={
        "text": "Observable criteria met: [what was observed]",
        "source": "Source Name",
        "tag": "EVENT",
        "tags": ["EVENT"],
    },
    fired_indicator_id="t2_indicator_slug",
    reason="Indicator t2_indicator_slug FIRED: [description]."
)

log_activity(result, platform="update-cycle")

# Check results
print(f"Posteriors before: {result['posteriors_before']}")
print(f"Posteriors after:  {result['posteriors_after']}")
print(f"Governance: {result['governance']['health']}")
```

### Bayesian update without an indicator

If no pre-registered indicator fires but the evidence is informative,
supply likelihoods directly:

```python
result = process_evidence(
    slug="topic-slug",
    entry={
        "text": "Factual description of what happened.",
        "source": "Source Name",
        "tag": "DIPLO",
        "tags": ["DIPLO", "POLICY"],
    },
    likelihoods={"H1": 0.15, "H2": 0.45, "H3": 0.30, "H4": 0.10},
    reason="[Why this evidence is informative and directional assessment]"
)
```

## Prerequisites

- The indicator's observable criteria must be **verified**, not just plausible
- The indicator must be in NOT_FIRED status (the pipeline checks this)
- You must know which indicator ID to fire

## What the pipeline does automatically

1. `add_evidence()` — enrichment, dedup, contradiction detection
2. `fire_indicator()` — marks indicator as FIRED with date and note
3. `suggest_likelihoods()` — derives likelihoods from pre-committed posteriorEffect
4. `bayesian_update()` — mechanical Bayes: P(H|E) = P(E|H)P(H) / sum
5. `snapshot_posteriors()` — records for Brier scoring
6. `auto_calibrate()` — resolves claims, updates source trust
7. `governance_report()` — full epistemic health check
8. `save_topic()` — embeds governance snapshot
9. `propagate_alert()` — checks downstream dependencies

## Constraints

1. **Posteriors computed by Bayes** — the engine computes them, you don't.
2. **Pre-committed effects only** — for indicator fires, the posteriorEffect
   in the indicator definition determines the likelihoods. Do not override.
3. **Evidence coupling** — every update references evidence. The pipeline enforces this.
4. **Governor pre-check** — `check_update_proposal()` runs 14 failure modes.
   If it fails, the update is blocked. Fix the issue, don't bypass.
