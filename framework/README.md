# NRL-Alpha Omega Framework

A governor-gated, programmatic update system for the NRL-Alpha Omega geopolitical tracking engine.

## Why This Framework

The previous system required agents to read all briefs to "catch up," consuming many tokens. This framework:

1. **Minimizes token overhead**: Uses diff-based history, not full brief reads
2. **Automates governor-gated updates**: Scripts run the pipeline
3. **Generalizes tools**: Works for any topic, not just Hormuz
4. **Tests hypotheses systematically**: Built-in test registry

## Architecture

```
framework/
├── runner.py          # Main orchestrator
├── update.py          # Update pipeline
├── lint.py            # Linting (failure mode checks)
├── test.py            # Hypothesis testing
├── run.sh             # Shell wrapper
└── README.md          # This file
```

## Quick Start

### Shell Script

```bash
# Routine update
./run.sh update --topic hormuz-closure --mode routine

# Crisis update
./run.sh update --topic hormuz-closure --mode crisis

# Lint evidence log
./run.sh lint --topic hormuz-closure

# Run test case
./run.sh test --topic hormuz-closure --test resolution_achieved

# Epistemic audit
./run.sh audit --topic hormuz-closure
```

### Python Direct

```bash
# Update
python framework/runner.py update --topic hormuz-closure --mode routine

# Lint
python framework/runner.py lint --topic hormuz-closure

# Test
python framework/runner.py test \
    --topic hormuz-closure \
    --test resolution_achieved \
    --evidence '{"tag":"EVENT","text":"Resolution achieved","provenance":"OBSERVED"}'
```

## Commands

### `update` — Run Update Pipeline

Runs the full update pipeline:
1. Loads topic
2. Reads most recent brief (not all history!)
3. Gathers fresh intel via web search
4. Adds evidence (governor-gated)
5. Updates posteriors/sub-models if warranted
6. Generates brief
7. Saves topic (governor snapshot)

```bash
./run.sh update --topic hormuz-closure --mode routine
```

### `lint` — Lint Evidence Log

Checks for failure modes:
- Rhetoric-as-evidence
- Recycled intel
- Empty search not logged
- Feed key mismatches
- Stale evidence
- Anchoring bias

```bash
./run.sh lint --topic hormuz-closure --check-history
```

### `test` — Run Test Case

Runs a hypothesis test:
1. Loads hypothesis state
2. Lints test evidence
3. Adds evidence via governor
4. Updates posteriors if warranted
5. Records test result

```bash
./run.sh test --topic hormuz-closure --test resolution_achieved
```

Available tests:
- `resolution_achieved`
- `toll_regime_active`
- `iceland_seized`
- `ceasefire_active`
- `freedom_of_navigation`

### `audit` — Epistemic Audit

Checks governance health:
- Evidence freshness
- Hypothesis admissibility
- Unfalsifiable hypotheses
- Uncertainty ratio
- R_t regime

```bash
./run.sh audit --topic hormuz-closure
```

### `diff` — Show Brief Diff

Shows diff from last brief (for troubleshooting):

```bash
./run.sh diff --topic hormuz-closure
```

### `health` — Health Status

Shows governance health snapshot:

```bash
./run.sh health --topic hormuz-closure
```

## Token Efficiency

### Before (Old System)
```
Agent reads: 200+ brief files
Token overhead: ~50,000 tokens per update
Agent must read ALL history to "catch up"
```

### After (New Framework)
```
Agent reads: Most recent brief only
Token overhead: ~1,000 tokens per update
Diff history available for troubleshooting
```

## Governor Integration

All updates are governor-gated:
- Evidence freshness checks
- Rhetoric detection
- Hallucination checklist
- R_t regime checks
- Entropy monitoring

The governor enforces epistemic discipline automatically.

## Epistemic Improvements

This framework enables systematic epistemic improvement:

1. **Source hierarchy**: Trust scores for different sources
2. **Evidence categories**: Rhetoric vs fact vs prediction
3. **Sub-model expansion**: Add missing variables
4. **Resolution criterion evolution**: Toll regime vs freedom of navigation

## Contributing

1. Add new test cases to `test.py::TEST_CASES`
2. Add new failure modes to `lint.py::FAILURE_MODES`
3. Update search queries in `update.py::SEARCH_QUERIES`
4. Add new sub-models to topic schema

## License

Internal use only.
