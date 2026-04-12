# Skill: Calibration & Scoring

Score predictions against outcomes, compute Brier scores, and run calibration
analysis to measure forecast accuracy.

## When to use

- A topic has resolved (outcome is known)
- A hypothesis has expired (time window passed)
- You want to measure historical forecast accuracy
- Periodic calibration health checks

## Python calls

### Record an outcome

```python
from engine import load_topic, save_topic
from framework.scoring import record_outcome

topic = load_topic("calibration-topic-slug")
topic = record_outcome(
    topic,
    resolved_hypothesis="H2",
    note="Resolution criterion met: [observable evidence]"
)
save_topic(topic)
```

### Record a partial outcome (hypothesis expired)

```python
from framework.scoring import record_partial_outcome

topic = record_partial_outcome(
    topic,
    expired_hypothesis="H1",
    redistribution="proportional",  # or explicit dict
    note="H1 time window expired without resolution"
)
```

### Compute Brier score for a snapshot

```python
from framework.scoring import compute_brier_score

score = compute_brier_score(
    posteriors_dict={"H1": 0.49, "H2": 0.31, "H3": 0.11, "H4": 0.09},
    resolved_hypothesis="H2"
)
# Returns:
# {
#   "brier_score": float,  # 0 = perfect, 1 = worst, 0.25 = random
#   "per_hypothesis": {"H1": float, ...},
#   "interpretation": "EXCELLENT | GOOD | FAIR | POOR | TERRIBLE"
# }
```

### Score all historical snapshots

```python
from framework.scoring import score_all_snapshots

scores = score_all_snapshots(topic)
# Returns list of Brier scores for each posteriorHistory entry
# Shows how accuracy improved (or didn't) over time
```

### Full calibration report

```python
from framework.scoring import compute_calibration_report

report = compute_calibration_report(topic)
# Returns:
# {
#   "topic": str,
#   "resolved_hypothesis": str,
#   "snapshots": [...],        # all scored snapshots
#   "initial_brier": float,    # score of initial priors
#   "final_brier": float,      # score at resolution
#   "improvement": float,      # initial - final (positive = got better)
#   "trajectory": str,         # IMPROVING | STABLE | DEGRADING
#   "calibration_health": str, # based on final Brier score
# }
```

### Backfill from outcomes (source trust calibration)

```python
from framework.backfill import full_backfill_pipeline

result = full_backfill_pipeline(
    topic_slug="calibration-topic-slug",
    winning_hypothesis="H2",
    actual_value=150.0,
    note="Resolved on 2026-06-01"
)
# This:
# 1. Records the outcome
# 2. Scores all snapshots
# 3. Walks the evidence log and scores each source
# 4. Updates source trust based on which sources were right
# 5. Returns full calibration report
```

## Brier score interpretation

| Score | Grade | Meaning |
|-------|-------|---------|
| 0.00 - 0.05 | EXCELLENT | Near-perfect calibration |
| 0.05 - 0.15 | GOOD | Meaningfully better than chance |
| 0.15 - 0.25 | FAIR | Slightly better than random |
| 0.25 | BASELINE | Equivalent to uniform random guess |
| 0.25 - 0.50 | POOR | Worse than guessing |
| 0.50 - 1.00 | TERRIBLE | Systematically wrong |

## Snapshot management

```python
from framework.scoring import snapshot_posteriors, check_expired_hypotheses

# Take a manual snapshot (automatic on posterior update)
topic = snapshot_posteriors(topic, trigger="manual")

# Check if any hypotheses have passed their time windows
expired = check_expired_hypotheses(topic)
# Returns list of hypotheses that should be redistributed
```

## Calibration workflow

1. **Topic resolves** → `record_outcome()` with the winning hypothesis
2. **Score snapshots** → `score_all_snapshots()` to see accuracy trajectory
3. **Backfill sources** → `full_backfill_pipeline()` to update source trust
4. **Generate report** → `compute_calibration_report()` for the full picture
5. **Update source DB** → calibrated trust scores feed into future topics

## Constraints

- Brier scoring requires a resolved outcome. You cannot score an active topic.
- Source calibration from outcomes should use the full backfill pipeline to
  ensure all sources in the evidence log are properly scored.
- Calibration health is per-topic. Cross-topic calibration requires aggregating
  Brier scores across multiple resolved topics.
- The scoring system rewards EARLY accuracy — a forecast that was right from
  day 1 scores better than one that corrected late, because all snapshots
  are scored, not just the final one.
