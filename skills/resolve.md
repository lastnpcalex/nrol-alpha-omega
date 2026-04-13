# Skill: Resolve Predictions

Sweep all topics for expired predictions, resolve them against current evidence,
and fire source calibration.

## When to use

- Periodically (weekly or on-demand) to process expired prediction windows
- After a major event that resolves multiple predictions at once
- When checking a specific source's prediction track record

## Workflow

### 1. Scan for unresolved predictions

For each active topic in `canvas/topics/`:

```
For each evidence entry where:
  - tags includes "PREDICTION"
  - prediction.resolution is null
  - prediction.resolvesBy is in the past (or today)
→ collect as pending resolution
```

### 2. Resolve each prediction

For each pending prediction:

1. Read the `prediction.claim` and `prediction.resolutionCriteria`
2. Search the topic's evidenceLog for entries that confirm or refute the claim
   - Resolution source MUST be independent (not the same source as the prediction)
   - Resolution evidence should be factual (EVENT, DATA, POLICY) not RHETORIC
3. If found:
   - Set `prediction.resolution` to CONFIRMED or REFUTED
   - Set `prediction.resolvedDate` to now (ISO8601)
   - Set `prediction.resolvedBy` to the resolution evidence source
   - Set `prediction.resolvedEvidence` to the ev_NNN ID
4. If not determinable:
   - Set `prediction.resolution` to INCONCLUSIVE
   - Set `prediction.resolvedDate` to now
   - Note why in `prediction.note`

### 3. Fire source calibration

For each CONFIRMED or REFUTED prediction (skip INCONCLUSIVE):

1. Look up the prediction source in `canvas/source_db.json`
2. If source doesn't exist, add with `baseTrust: 0.50, category: "social_media"`
3. Update the source's domain stats:
   - Find or create a `PREDICTION` domain entry
   - Increment `claims`
   - If CONFIRMED: increment `confirmed`
   - If REFUTED: increment `refuted`
   - Recompute `hitRate` = confirmed / claims
   - Recompute `domainTrust` using the Bayesian formula:
     `domainTrust = (baseTrust * prior_weight + hitRate * claims) / (prior_weight + claims)`
     where `prior_weight = 2` (equivalent to 2 imaginary observations at baseTrust)
4. Update `effectiveTrust`:
   - If PREDICTION domain has >= 5 resolved claims: weight PREDICTION domain at 50%
     alongside other domains
   - If < 5: keep effectiveTrust unchanged (insufficient data)
5. Update `lastUpdated`

### 4. Minimum sample guard

Source trust should not swing wildly on small samples:

| Resolved predictions | Max trust delta from 0.50 |
|---------------------|--------------------------|
| 1-2 | +/- 0.05 |
| 3-4 | +/- 0.10 |
| 5-9 | +/- 0.15 |
| 10-19 | +/- 0.25 |
| 20+ | uncapped |

This prevents a source from jumping to 0.95 on 3 lucky calls.

### 5. Activity log

For each resolution, append to `canvas/activity-log.json`:

```json
{
  "id": "2026-04-20T12:00:00Z-resolve-source-handle",
  "timestamp": "ISO now",
  "type": "PREDICTION_RESOLVED",
  "headline": "Prediction resolved: [claim summary]",
  "source": "predictor handle",
  "sourceTrust": { "before": 0.XX, "after": 0.XX },
  "triageResult": null,
  "evidenceEntry": {
    "tag": "PREDICTION",
    "text": "the prediction claim",
    "claimState": "CONFIRMED | REFUTED",
    "effectiveWeight": 0
  },
  "posteriorDelta": null,
  "calibrationDelta": {
    "source": "predictor handle",
    "domain": "PREDICTION",
    "before_trust": 0.XX,
    "after_trust": 0.XX,
    "resolution": "CONFIRMED | REFUTED",
    "total_resolved": N
  },
  "topicSlugs": ["topic-where-prediction-lived"],
  "notes": "Prediction from [date]: '[claim]'. Resolution: [CONFIRMED/REFUTED] based on [ev_NNN]."
}
```

### 6. Report

List all predictions resolved in this sweep:

| Source | Prediction | Resolution | New Trust | Track Record |
|--------|-----------|------------|-----------|-------------|
| @handle | "claim" | CONFIRMED | 0.55 (was 0.50) | 3/4 (75%) |

## Constraints

- NEVER resolve a prediction using the predictor's own later statements
- INCONCLUSIVE does not count toward calibration (no hit, no miss)
- Predictions that are still within their window are SKIPPED (not yet resolvable)
- The minimum sample guard caps trust movement, not the hitRate calculation
- Resolved predictions stay in the evidence log (not deleted) with their resolution filled in
