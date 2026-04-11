# NRL-Alpha Omega Framework

Governor-gated programmatic update system for the estimation engine.

## Modules

### Core Pipeline
| Module | Purpose |
|--------|---------|
| `runner.py` | CLI orchestrator — dispatches update, lint, test, audit, diff, health |
| `update.py` | Full update pipeline — load topic, gather intel, add evidence, shift posteriors, generate brief |
| `lint.py` | Evidence log linting — checks for rhetoric-as-evidence, recycled intel, stale data, feed mismatches |
| `test.py` | Hypothesis test registry — structured tests with governor-gated evidence injection |

### Epistemic Modules
| Module | Purpose |
|--------|---------|
| `red_team.py` | Devil's advocate system — scores counterevidence, builds contrarian case, text heuristic inference for string posteriorImpact values |
| `contradictions.py` | Contradiction detection — DIRECT, FEED_MISMATCH, MAGNITUDE, TEMPORAL types with severity tiers (HIGH/MEDIUM/LOW) |
| `scoring.py` | Prediction calibration — Brier scores, posterior snapshots, hypothesis expiry detection, partial scoring |
| `compaction.py` | Evidence log compaction — archives old entries while preserving key claims and their effective weights |
| `source_ledger.py` | Claim resolution tracker — scans evidence for confirmation/refutation pairs, Bayesian trust updates per source |
| `source_db.py` | Source database — cross-topic, domain-aware performance tracking. Stores per-source-per-domain hit rates and trust scores |
| `calibrate.py` | Base trust scores and source registration — the starting priors that source_ledger and source_db update from |

## Update Pipeline

```bash
# Routine update (daily)
python framework/runner.py update --topic hormuz-closure --mode routine

# Crisis update (high-tempo)
python framework/runner.py update --topic hormuz-closure --mode crisis

# With force override (bypasses governance blocks)
python framework/update.py hormuz-closure --force

# Check for expired hypotheses
python framework/update.py hormuz-closure --check-expired
```

The pipeline:
1. Load topic state
2. Read most recent brief (not all history — token-efficient)
3. Gather fresh intel via web search (if available)
4. Add evidence through governor gate (enrichment + validation)
5. Update posteriors if warranted (or hold with rationale)
6. Generate brief with all required sections
7. Save topic (governance snapshot embedded)

## Epistemic Modules: How They Connect

```
Evidence enters                Source calibration
add_evidence() ──────────────► source_ledger.py
      │                              │
      │  Contradiction check         │  Track claim outcomes
      ├──► contradictions.py         ├──► source_db.py
      │                              │         │
      │  Weight calculation          │         │  Domain-aware trust
      ├──► governor.get_effective_   │         │  (ECON: 0.96,
      │    weight() uses source      │         │   RHETORIC: 0.10)
      │    trust from calibration    │         │
      │                              ▼         ▼
      │                     Effective weight = claim_state × source_trust
      │
      ▼
Posterior update ──────────► red_team.py (devil's advocate check)
      │                              │
      │                              │  Counterevidence scoring
      │                              │  (scans compacted evidence too)
      │                              │
      ▼                              ▼
save_topic() ──────────────► scoring.py
      │                         │
      │  Governance snapshot     │  Brier score snapshots
      │  R_t, entropy            │  Hypothesis expiry detection
      │  Health assessment       │  Partial scoring for expired H
      │
      ▼
Topic JSON (single source of truth)
```

## CLI Reference

```bash
# Update pipeline
./run.sh update --topic <slug> --mode routine|crisis

# Lint evidence log
./run.sh lint --topic <slug> [--check-history]

# Run hypothesis test
./run.sh test --topic <slug> --test <test_name>

# Epistemic audit
./run.sh audit --topic <slug>

# Brief diff
./run.sh diff --topic <slug>

# Health snapshot
./run.sh health --topic <slug>

# Scoring/calibration
python framework/scoring.py <slug> --report
python framework/scoring.py <slug> --backfill
python framework/scoring.py <slug> --snapshot
python framework/scoring.py <slug> --resolve H3

# Source database
python framework/source_db.py ingest --topic <slug>
python framework/source_db.py profile --source Reuters
python framework/source_db.py domains --min-claims 3
python framework/source_db.py export --tag ECON
```

## Token Efficiency

The framework reads only the most recent brief (not all history), uses diff-based tracking, and compacts old evidence while preserving key claims. Typical overhead: ~1,000 tokens per update vs ~50,000 with full-history reads.
