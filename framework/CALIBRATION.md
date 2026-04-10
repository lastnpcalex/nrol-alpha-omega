# NRL-Alpha Omega — Source Calibration System

This document explains how to verify, lint, and update source trust scores in the NRL-Alpha Omega framework.

## Overview

Source trust scores are **calibrated empirically** over time using Bayesian updating:
- **Prior**: Initial trust score (based on source type)
- **Evidence**: Verification results from cross-referencing
- **Posterior**: Updated trust score

## Trust Score Categories

| Category | Trust Range | Examples |
|--|--|--|
| Government/Military | 0.90-0.95 | CENTCOM, Pentagon, DoD |
| Wire Services | 0.88-0.90 | Reuters, AP, AFP |
| Newspapers | 0.80-0.85 | WaPo, NYT, Bloomberg |
| Financial | 0.70-0.80 | Fortune, WSJ, CNBC |
| General Media | 0.40-0.70 | CNN, Fox, Daily Mail |
| State Media | 0.30-0.45 | IRNA, ISNA, TASS |

## Commands

### 1. View Current Trust Scores

```bash
python framework/runner.py calibrate --topic hormuz-closure --subcommand trust
```

**Output:**
```json
{
  "CENTCOM": 0.95,
  "Reuters": 0.9,
  "WashingtonPost": 0.85,
  "DailyMail": 0.5
}
```

### 2. Verify a Claim

```bash
python framework/runner.py calibrate \
  --topic hormuz-closure \
  --source "DailyMail" \
  --claim "Iran is cornered" \
  --method "cross_reference" \
  --high-trust-source '{"source":"Pentagon","claim":"Iran not cornered"}'
```

**Verification Methods:**
- `cross_reference`: Compare with higher-trust source
- `expert`: Manual expert judgment
- `automated`: Automated data matching (e.g., oil prices)

### 3. Detect Source Drift

Check if a source's accuracy is degrading over time:

```bash
python framework/runner.py calibrate \
  --topic hormuz-closure \
  --subcommand drift \
  --source "DailyMail" \
  --window-days 7
```

**Output:**
```json
{
  "source": "DailyMail",
  "recent_period": "10 entries",
  "older_period": "20 entries",
  "recent_hit_rate": 0.4,
  "older_hit_rate": 0.6,
  "drift": -0.2,
  "drift_interpretation": "DEGRADING"
}
```

### 4. Register New Source

```bash
python framework/calibrate.py register --source "AssociatedPress" --trust 0.88
```

### 5. Register Source Category

```bash
python framework/calibrate.py category --category "wire_service" --range "0.88,0.90"
```

### 6. Batch Calibrate All Sources

```bash
python framework/calibrate.py calibrate --topics "hormuz-closure"
```

## Calibration Process

### Step 1: Make a Claim with Source

```
Source: DailyMail
Claim: "Iran is cornered"
```

### Step 2: Verify with Higher-Trust Source

```
High-Trust Source: Pentagon / CENTCOM
Claim: "No indication Iran is cornered"
```

### Step 3: Compare Results

- **Match**: Both agree → Positive evidence
- **Conflict**: Different claims → Negative evidence
- **Conflict (different interpretation)**: Neutral evidence

### Step 4: Update Trust Score

Using Bayesian updating:
```
Prior odds = prior / (1 - prior)
Posterior odds = Prior odds × Likelihood ratio
Posterior = Posterior odds / (1 + Posterior odds)
```

**Example:**
- DailyMail prior: 0.5
- 5 VERIFIED claims (confidence 0.8 each)
- 3 CONFLICT claims (confidence 0.8 each)
- Posterior: ~0.52 (slight degradation)

### Step 5: Log Calibration Attempt

Calibration data stored in `calibration/calibration_[source].json`:
```json
{
  "DailyMail": [
    {
      "timestamp": "2026-04-10T21:30:00+00:00",
      "claim": "Iran is cornered",
      "result": "CONFLICT",
      "confidence": 0.8
    }
  ]
}
```

## Preventing Manipulation

1. **Require 2+ sources for high-impact claims**: Can't use self-validation
2. **Transparency**: All calibration attempts logged
3. **Drift detection**: Automatically identify degrading sources
4. **Manual override**: Experts can flag special cases
5. **No retroactive adjustment**: Current score reflects past evidence

## Integration with Framework

The calibration module integrates with `update.py`:

```python
# In update.py
result = verify_claim(
    topic_name="hormuz-closure",
    source_name="DailyMail",
    claim="New claim text",
    verification_method="cross_reference",
    high_trust_source={
        "source": "Reuters",
        "claim": "Cross-reference claim"
    }
)
```

## Workflow Example

```bash
# Step 1: View current trust scores
python framework/runner.py calibrate --topic hormuz-closure --subcommand trust

# Step 2: Verify a claim from low-trust source
python framework/runner.py calibrate \
  --topic hormuz-closure \
  --source "DailyMail" \
  --claim "New claim" \
  --method "cross_reference" \
  --high-trust-source '{"source":"Reuters","claim":"Reuters version"}'

# Step 3: Check for drift
python framework/runner.py calibrate \
  --topic hormuz-closure \
  --subcommand drift \
  --source "DailyMail" \
  --window-days 7

# Step 4: If drift detected, consider adjusting initial trust
python framework/calibrate.py register --source "DailyMail" --trust 0.45
```

## Notes

- Trust scores are **empirical**, not arbitrary
- All calibration is transparent and logged
- High-trust sources are still subject to drift detection
- New sources start with category-based trust ranges
