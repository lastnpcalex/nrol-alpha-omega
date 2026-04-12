# Skill: Source Trust

Register sources, calibrate trust scores from prediction outcomes, and resolve
effective trust through the 5-tier chain.

## When to use

- A new source appears in evidence that isn't in the source database
- A topic has resolved and you need to calibrate source accuracy
- You need to know the effective trust weight for an evidence entry
- Source drift detection flags a reliability change

## Python calls

### Register a new source

```python
from framework.calibrate import register_source

# Adds to sources/source-trust.json with initial trust
register_source("New Wire Service", initial_trust=0.70)
```

### Resolve effective trust for a source + topic

```python
from framework.source_ledger import compute_effective_trust

trust = compute_effective_trust(topic, "Reuters")
# Returns float — the resolved trust after walking the chain
```

### Verify a claim (resolution tracking)

```python
from framework.calibrate import verify_claim

result = verify_claim(
    topic_name="calibration-topic-slug",
    source_name="Reuters",
    claim="Specific factual claim text",
    outcome="CORRECT"  # CORRECT | INCORRECT | PARTIAL | UNRESOLVED
)
```

### Calibrate trust from outcomes

```python
from framework.calibrate import calibrate_source_trust, update_trust_scores

# Single source
new_trust = calibrate_source_trust("Reuters", calibration_data)

# All sources across all topics
results = update_trust_scores(["calibration-topic-1", "calibration-topic-2"])
```

### Source database operations

```python
from framework.source_db import load_db, get_source_profile, get_domain_trust

db = load_db()

# Full profile for a source
profile = get_source_profile(db, "Reuters")
# {"name": ..., "overall_trust": float, "domains": {...}, "claims": int, ...}

# Domain-specific trust (e.g., Reuters on ECON topics)
domain_trust = get_domain_trust(db, "Reuters", "ECON")
```

### Detect source drift

```python
from framework.calibrate import detect_source_drift

drift = detect_source_drift("CNN", window_days=7)
# {"drifting": bool, "direction": "improving|declining|stable", ...}
```

## 5-Tier Trust Chain

When resolving effective trust for a source in a specific topic context,
the framework walks this chain top-to-bottom, using the first available value:

```
Tier 1: Per-topic calibration
         → topic-specific trust from resolved claims in THIS topic
         → Highest fidelity. Only available after claims resolve.

Tier 2: Cross-topic domain trust
         → source's trust in this evidence domain (ECON, DIPLO, etc.)
         → From source_db.json domain breakdown.

Tier 3: Cross-topic overall trust
         → source's aggregate trust across all topics
         → From source_db.json overall score.

Tier 4: SOURCE_TRUST base priors
         → Pre-registered trust in sources/source-trust.json
         → Starting point before any calibration data exists.

Tier 5: Unknown fallback
         → 0.50 (maximum uncertainty)
         → Used when source has never been seen before.
```

## Evidence weight formula

```
effectiveWeight = claimStateWeight × sourceTrust

claimStateWeight:
  PROPOSED     = 0.5
  SUPPORTED    = 1.0
  CONTESTED    = 0.2
  INVALIDATED  = 0.0

Example:
  Reuters (trust 0.96), SUPPORTED claim → 1.0 × 0.96 = 0.96
  Unknown blog (trust 0.50), PROPOSED claim → 0.5 × 0.50 = 0.25
```

## Source trust files

- `sources/source-trust.json` — Tier 4 base priors (22+ sources)
- `sources/source_db.json` — Tiers 1-3 calibrated trust database
- Each topic's `evidenceLog` entries carry `source` field for chain resolution

## Constraints

- Never assume a source is trustworthy because it's well-known. Use the chain.
- When a source is unknown (Tier 5, trust 0.50), note this explicitly in the
  evidence entry's `note` field.
- Source trust is asymmetric across domains — a source excellent at ECON data
  may be unreliable on KINETIC intelligence.
- Calibration only improves with resolved claims. Until claims resolve, trust
  is estimated, not measured.
