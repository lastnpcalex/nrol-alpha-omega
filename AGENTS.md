# NROL-AO — Agent Instructions

You are operating the NROL-AO Bayesian estimation framework. This is a
governor-gated system where **natural language is a proposal, not an authority**
— only verified evidence moves posteriors.

## Standing orders

These apply automatically whenever you interact with topic data, evidence,
or posteriors. You do not need to be told to follow them.

### On new information (headlines, URLs, data)

1. **Triage first**: `from engine import triage_headline`
   - Match against all active topics in `topics/`
   - Check if any indicator's observable threshold is met
   - Assess source trust via 5-tier chain
   - Route: UPDATE_CYCLE / LOG_EVIDENCE / MONITOR / IGNORE
2. **Never skip triage** to go straight to evidence or posteriors

### On evidence logging

1. Full provenance required: id, time, text, tags, source, claimState, posteriorImpact
2. Lint through 5 failure modes before saving:
   - rhetoric_as_evidence, recycled_intel, anchoring_bias, phantom_precision, stale_evidence
3. posteriorImpact is "NONE" unless an indicator actually fired
4. Source trust resolved, never assumed (unknown = 0.50)

### On posterior updates

1. Only fired indicators or Bayesian likelihood ratios move posteriors
2. Apply pre-committed posteriorEffect only — do not invent magnitudes
3. Posteriors must sum to 1.00
4. Run `check_update_proposal()` before applying
5. Run `governance_report()` after applying
6. Check `propagate_alert()` for downstream dependency staleness
7. Append to posteriorHistory with date + justification note

### On governance

1. Health = HEALTHY (0 issues) / DEGRADED (1-2) / CRITICAL (3+)
2. R_t regimes: SAFE < 0.1, ELASTIC < 0.3, DANGEROUS < 1.0, RUNAWAY > 1.0
3. Evidence freshness uses per-tag TTLs (RHETORIC=24h, EVENT=72h, DATA=168h, POLICY=720h)
4. Flag CRITICAL health immediately — do not proceed with updates until addressed

## Skill files

Detailed workflow prompts with actual function calls in `skills/`:

- `skills/triage.md` — headline routing
- `skills/update-cycle.md` — indicator fire + posterior update
- `skills/evidence.md` — evidence logging + lint
- `skills/governance.md` — epistemic health audit
- `skills/topic-design.md` — create/modify topics
- `skills/dependencies.md` — cross-topic dependency management
- `skills/source-trust.md` — source registration + calibration
- `skills/red-team.md` — devil's advocate challenges
- `skills/calibration.md` — prediction scoring + Brier scores

**Read the relevant skill file when performing that workflow.**

## Key files

- `engine.py` — core Bayesian engine (load/save topics, update posteriors, fire indicators)
- `governor.py` — epistemic governor (R_t, freshness, admissibility, health)
- `framework/triage.py` — headline triage
- `framework/dependencies.py` — cross-topic staleness detection
- `framework/lint.py` — evidence and topic linting
- `framework/contradictions.py` — contradiction detection
- `framework/red_team.py` — devil's advocate scoring
- `framework/scoring.py` — Brier scores and calibration
- `topics/*.json` — active calibration topics (one file per topic)
- `sources/source-trust.json` — base trust priors (Tier 4)
