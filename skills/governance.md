# Skill: Governance Audit

Run the full epistemic governance suite: R_t scoring, evidence freshness,
hypothesis admissibility, entropy analysis, KL divergence, and health grading.

## When to use

- Before or after any posterior update
- When checking overall topic health
- When deciding which topics need attention
- When the mirror dashboard shows DEGRADED or CRITICAL health

## Python calls

### Full governance report

```python
from engine import load_topic
from governor import governance_report

topic = load_topic("calibration-topic-slug")
report = governance_report(topic)

# Returns:
# {
#   "health": "HEALTHY | DEGRADED | CRITICAL",
#   "issues": ["list of problems"],
#   "rt": {"rt": float, "regime": "SAFE|ELASTIC|DANGEROUS|RUNAWAY", ...},
#   "entropy": float,          # Shannon entropy in bits
#   "max_entropy": float,      # log2(num_hypotheses)
#   "uncertainty_ratio": float, # entropy / max_entropy (0.0 = certain, 1.0 = uniform)
#   "kl_from_prior": {"klDivergence": float, "interpretation": str},
#   "evidence_freshness": {"total": int, "fresh": int, "stale": int, ...},
#   "hypothesis_admissibility": {"H1": {"grade": str, ...}, ...},
#   "top_queries": [...],      # VoI-ranked search priorities
# }
```

### Individual components

```python
from governor import (
    compute_rt,              # Per-hypothesis R_t scores
    compute_topic_rt,        # Aggregate topic R_t (max of hypotheses)
    audit_evidence_freshness, # Per-tag TTL-based freshness
    validate_hypotheses,     # Admissibility grading
    compute_entropy,         # Shannon entropy
    compute_uncertainty_ratio, # Normalized entropy
    compute_kl_from_prior,   # Divergence from initial prior
    prioritize_queries,      # VoI search ranking
    check_update_proposal,   # Pre-commit failure mode check (10 modes)
)

# R_t per hypothesis
rt = compute_rt(topic)
# {"H1": {"rt": 0.23, "regime": "ELASTIC", "priorityRank": 2, ...}, ...}

# Evidence freshness
fresh = audit_evidence_freshness(topic)
# {"total": 10, "fresh": 7, "stale": 3, "staleEntries": [...], "byTag": {...}}

# Pre-commit check (run BEFORE applying posteriors)
check = check_update_proposal(topic, proposed_posteriors, reason, evidence_refs)
# {"passed": bool, "failures": [...], "warnings": [...], "checks": {...}}
```

## R_t: Search Priority Score

R_t is an entropy-weighted attention-allocation heuristic per hypothesis:

```
R_t(H_i) = entropy_contribution(H_i) * time_decay / evidence_recency

entropy_contribution = -p_i * log2(p_i)    # Shannon information content
time_decay = log2(1 + delay_hours / 24)     # log-scaled staleness
evidence_recency = (24h_count * 3) + (72h_count * 1) + 1  # recent evidence bonus
```

### Regime thresholds (configurable per topic via `rtConfig`)

| Regime | Threshold | Meaning |
|--------|-----------|---------|
| SAFE | < 0.1 | Well-evidenced, recently updated |
| ELASTIC | 0.1 - 0.3 | Normal operating range |
| DANGEROUS | 0.3 - 1.0 | Stale, needs attention |
| RUNAWAY | > 1.0 | Critically stale, prioritize immediately |

R_t is NOT a truth measure. It measures "how urgently does this hypothesis
need fresh evidence?" A RUNAWAY hypothesis isn't wrong — it's under-evidenced.

## Health grading (governor.py:1284)

```
HEALTHY:  0 issues
DEGRADED: 1-2 issues
CRITICAL: 3+ issues
```

Issues are checked in order:
1. R_t regime DANGEROUS or RUNAWAY
2. Majority of evidence is stale (stale > fresh)
3. Any hypothesis is INADMISSIBLE
4. Any hypothesis is unfalsifiable (no anti-indicators)
5. Uncertainty > 0.9 (model not discriminating)
6. Uncertainty < 0.1 (possible overconfidence)
7. KL from prior is PRIOR_DOMINATED

## 10 failure modes (pre-commit check)

The `check_update_proposal` function runs these before any posterior update:

1. no_evidence — no actual evidence, just analysis
2. confidence_inflation — shift too large for evidence strength
3. repetition_as_validation — same evidence cited multiple times
4. stale_evidence — majority of evidence base is past TTL
5. circular_reasoning — evidence depends on the conclusion
6. modal_confusion — confusing "could happen" with "is happening"
7. citation_drift — source doesn't say what's claimed
8. survivorship_bias — only considering confirming evidence
9. authority_substitution — trusting source prestige over evidence
10. scope_creep — evidence from adjacent domain applied too broadly

## Mirror dashboard equivalent

The Loom mirror now runs a faithful JS port of the full governance report.
Functions `computeRt()`, `auditEvidenceFreshness()`, `computeEntropy()`,
`computeKlFromPrior()`, `validateHypotheses()`, and `governanceReport()`
in `mirror.html` are direct ports of `governor.py` and produce identical
output. Health badges show HEALTHY/DEGRADED/CRITICAL; R_t badges show
SAFE/ELASTIC/DANGEROUS/RUNAWAY.
