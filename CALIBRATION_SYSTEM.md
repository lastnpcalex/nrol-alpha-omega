# NRL-Alpha Omega — Source Calibration System

## Executive Summary

This document explains how to **verify, lint, and update source trust scores** in the NRL-Alpha Omega framework.

The calibration system ensures **source trust scores are empirically maintained** and cannot be manipulated by bias or error.

---

## How Source Calibration Works

### 1. Initial Trust Scores (Category-Based)

| Source Type | Trust Range | Examples |
|-------------|-------------|----------|
| Government/Military | 0.90-0.95 | CENTCOM, Pentagon |
| Wire Services | 0.88-0.90 | Reuters, AP, AFP |
| Newspapers | 0.80-0.85 | WaPo, NYT |
| Financial Media | 0.70-0.80 | Fortune, WSJ |
| General Media | 0.40-0.70 | CNN, Daily Mail |
| State Media | 0.30-0.45 | IRNA, ISNA |

### 2. Empirical Calibration

Trust scores are **updated via Bayesian updating**:

```
Prior odds = prior / (1 - prior)
Evidence = verified - conflicted
Likelihood ratio = 1 + 2 × evidence
Posterior odds = Prior odds × Likelihood ratio
Posterior = Posterior odds / (1 + Posterior odds)
```

### 3. Cross-Reference Verification

When a low-trust source makes a claim, **cross-reference with higher-trust source**:

```
Low-trust claim: "Iran is cornered" (DailyMail)
High-trust check: "No such indication" (CENTCOM)
Result: CONFLICT → Negative evidence for DailyMail
```

---

## Commands

### View Trust Scores

```bash
python framework/runner.py calibrate --topic hormuz-closure --subcommand trust
```

### Verify a Claim

```bash
python framework/runner.py calibrate \
  --topic hormuz-closure \
  --source "DailyMail" \
  --claim "New claim" \
  --method "cross_reference" \
  --high-trust-source '{"source":"Reuters","claim":"Cross-ref"}'
```

### Detect Drift

```bash
python framework/runner.py calibrate \
  --topic hormuz-closure \
  --subcommand drift \
  --source "DailyMail" \
  --window-days 7
```

### Register New Source

```bash
python framework/calibrate.py register --source "AssociatedPress" --trust 0.88
```

### Register Source Category

```bash
python framework/calibrate.py category --category "wire_service" --range "0.88,0.90"
```

---

## Calibration Data Storage

Calibration history stored in `calibration/`:

```
calibration/
├── calibration_DailyMail.json
├── calibration_CENTCOM.json
└── source_trust.json  # Current trust scores
```

Each file tracks:
- Timestamp
- Claim text
- Verification result (VERIFIED/CONFLICT/CONFLICTED)
- Confidence score

---

## Prevention of Manipulation

1. **Require 2+ sources**: Can't self-validate
2. **Transparency**: All calibration logged
3. **Drift detection**: Automatic degradation alerts
4. **Manual override**: Experts can flag special cases
5. **No retroactive adjustment**: Current score = past evidence

---

## Integration Example

Add calibration to update pipeline:

```python
# In framework/update.py
def verify_claim(topic_name: str, source_name: str, claim: str,
                 verification_method: str, high_trust_source: dict) -> dict:
    """Verify claim and calibrate source."""
    from calibrate import verify_claim as vc
    
    result = vc(
        topic_name=topic_name,
        source_name=source_name,
        claim=claim,
        verification_method=verification_method,
        high_trust_source=high_trust_source,
    )
    
    return result
```

---

## Workflow Example

1. **Claim from low-trust source**: "Iran is cornered" (DailyMail)
2. **Verify with high-trust**: CENTCOM says "No such indication"
3. **Cross-reference result**: CONFLICT
4. **Calibration**: DailyMail evidence = -0.8
5. **Bayesian update**: DailyMail trust = 0.5 → 0.48
6. **Log**: `calibration_DailyMail.json` updated

---

## Files Modified

- `framework/calibrate.py` - New calibration module
- `framework/runner.py` - Added calibrate command
- `framework/CALIBRATION.md` - Detailed documentation

---

## Next Steps

1. **Run calibration on all sources**
2. **Verify claims in upcoming updates**
3. **Detect drift in high-impact sources**
4. **Adjust trust scores based on evidence**

---

*Generated: 2026-04-10*
*Status: Calibration system implemented and tested*
