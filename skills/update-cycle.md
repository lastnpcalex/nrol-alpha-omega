# Skill: Update Cycle

Fire an indicator, update posteriors, and run full governance validation.
This is the core posterior-moving operation.

## When to use

- Triage returned INDICATOR_MATCH and you've verified the observable criteria
- A pre-registered indicator's threshold has been met
- You need to apply a Bayesian update with likelihood ratios

## Prerequisites

- The indicator's observable criteria must be **verified**, not just plausible
- The indicator must be in NOT_FIRED status (check first)
- You must know which indicator ID to fire

## Python calls

### Option A: Fire a pre-registered indicator (preferred)

```python
from engine import load_topic, fire_indicator, save_topic
from governor import governance_report, check_update_proposal

topic = load_topic("calibration-topic-slug")

# Fire the indicator — this records firedDate and firedNote
topic = fire_indicator(
    topic,
    indicator_id="t2_indicator_slug",
    note="Observable criteria met: [describe what was observed]",
    firedDate="2026-04-12T00:00:00Z"
)

# The indicator's pre-committed posteriorEffect tells you what to apply.
# Read it from topic["indicators"]["tiers"]["tier2_strong"] (or whichever tier).
# Then update posteriors:
from engine import update_posteriors
topic = update_posteriors(
    topic,
    new_posteriors={"H1": 0.49, "H2": 0.31, "H3": 0.11, "H4": 0.09},
    reason="Indicator t2_indicator_slug FIRED: [description]. Applied pre-committed effects.",
    evidence_refs=["ev_001"]  # evidence entries that support this
)

# Run governance checks BEFORE saving
report = governance_report(topic)
print(f"Health: {report['health']}, Issues: {report['issues']}")

# If health is not CRITICAL, save
save_topic(topic)
```

### Option B: Bayesian update with likelihood ratios

```python
from engine import load_topic, bayesian_update, save_topic
from governor import check_update_proposal

topic = load_topic("calibration-topic-slug")

# check_update_proposal runs the 10 failure mode checks BEFORE applying
pre_check = check_update_proposal(
    topic,
    proposed_posteriors={"H1": 0.49, "H2": 0.31, "H3": 0.11, "H4": 0.09},
    reason="[justification]",
    evidence_refs=["ev_001"]
)
if not pre_check["passed"]:
    print(f"BLOCKED: {pre_check['failures']}")
    # Do not proceed — fix the issues first

# If pre-check passes, apply
topic = bayesian_update(
    topic,
    likelihoods={"H1": 1.2, "H2": 1.1, "H3": 0.7, "H4": 0.8},
    reason="[justification]"
)
save_topic(topic)
```

### Option C: Hold (no change)

```python
from engine import load_topic, hold_posteriors, save_topic

topic = load_topic("calibration-topic-slug")
topic = hold_posteriors(topic, reason="No new indicators fired. Evidence logged but below threshold.")
save_topic(topic)
```

## Full update cycle workflow (framework/update.py)

```python
from framework.update import run_update

# This runs the complete pipeline:
# 1. Orient (load topic, compute R_t, identify priorities)
# 2. Lint evidence log
# 3. Check for contradictions
# 4. Apply posteriors (fire indicators or hold)
# 5. Run governance report
# 6. Check dependencies
# 7. Generate brief
result = run_update("calibration-topic-slug",
    fired_indicators=["t2_indicator_slug"],
    new_evidence=[{...}],
    reason="Update cycle triggered by triage INDICATOR_MATCH"
)
```

## Constraints

1. **Posteriors must sum to 1.00** — the engine validates this and will reject
   updates that don't sum correctly.
2. **Pre-committed effects only** — when firing an indicator, apply the
   posteriorEffect declared in the indicator definition. Do not invent new
   shift magnitudes.
3. **Evidence coupling** — every posterior update must reference at least one
   evidence entry. Updates without evidence refs are rejected.
4. **Governor pre-check** — `check_update_proposal()` runs 10 failure mode
   checks. If it fails, the update is blocked. Fix the issue, don't bypass.
5. **posteriorHistory** — every update appends to `model.posteriorHistory`
   with date, new posteriors, and a note explaining the change.
6. **Classification auto-update** — `compute_classification()` runs after
   indicator fires to update ROUTINE/ELEVATED/ALERT status.

## After updating

- Check dependencies: `framework.dependencies.propagate_alert(topic)` to
  see if downstream topics now have stale assumptions.
- Update expectedValue if the model has midpoints.
- Update the mirror dashboard by syncing the topic JSON.
