# NRL-Alpha Omega — Current Thing Protocol

## Purpose

Step-by-step protocol for scaffolding any "Current Thing" onto the epistemic
Bayesian estimator. Topic-agnostic — works for geopolitical crises, elections,
market events, policy outcomes, technology adoption, or anything with a
measurable resolution criterion.

The Governor enforces analytical discipline. The Dashboard visualizes state.
This protocol connects them.

---

## Phase 1: Define the Question

Every topic starts with a single predictive question that is:

- **Specific** — not "what happens with X" but "how long / how much / what outcome"
- **Measurable** — has an observable resolution criterion
- **Time-bounded** — either by the question itself or by a tracking horizon

```
Question:  "How long will [thing] remain [state]?"
           "What will [metric] be on [date]?"
           "Which of [outcomes] will occur by [deadline]?"

Resolution: Observable criterion that closes the question.
            Must be verifiable by a third party.
```

---

## Phase 2: Generate Hypotheses

Define 2-6 mutually exclusive, collectively exhaustive outcomes.

### Requirements (Governor-enforced)
- **Exhaustive**: posteriors must sum to 1.0
- **Falsifiable**: each hypothesis must have at least one anti-indicator
  (Governor flags unfalsifiable hypotheses as DEGRADED)
- **Measurable**: each hypothesis has a midpoint value and unit for E[X] computation
- **Distinguishable**: adjacent hypotheses must have different observable signatures

### Template
```
H1: [Short outcome]  midpoint=[value] unit=[unit]  prior=[0.xx]
H2: [Short outcome]  midpoint=[value] unit=[unit]  prior=[0.xx]
H3: [Short outcome]  midpoint=[value] unit=[unit]  prior=[0.xx]
H4: [Short outcome]  midpoint=[value] unit=[unit]  prior=[0.xx]
                                            Total: 1.00
```

### Prior Selection
- Start with maximum entropy (uniform) if you have no information
- If you have base rates or domain knowledge, encode it — but document WHY
- The prior is logged in posteriorHistory as the first entry

---

## Phase 3: Design Indicators (Tripwires)

Indicators are observable conditions that, when verified, shift posteriors.
They are the ONLY mechanism for posterior movement.

### Tier Structure

| Tier | Name | Shift Magnitude | Classification Effect |
|------|------|-----------------|----------------------|
| 1 | Critical | 15-30pp | → ALERT |
| 2 | Strong | 5-15pp | → ELEVATED |
| 3 | Suggestive | 1-5pp | Unchanged |
| Anti | Contrary | Reversal | May downgrade |

### Design Rules
1. Each indicator must be **observable** — not "X thinks Y" but "X does Y"
2. Each indicator must have a clear **fired/not-fired** threshold
3. Each hypothesis should have at least one pro-indicator AND one anti-indicator
4. Anti-indicators are required — the Governor flags hypotheses without them
5. Indicators should span different evidence categories (KINETIC, ECON, DIPLO, etc.)

### Template per indicator
```
id:              unique_slug
desc:            Observable condition (action, not rhetoric)
tier:            tier1_critical | tier2_strong | tier3_suggestive | anti_indicators
posteriorEffect: Which hypotheses shift and by how much
```

---

## Phase 4: Build the Actor Model

Who makes the decisions that drive this outcome? What are their biases?

### Per actor
```
name:           Who
role:           Their function in the outcome
decisionStyle:  How they decide (rational, impulsive, institutional, etc.)
biases:         Known cognitive biases or fixations
filters:        Institutional constraints on their behavior
overrides:      Conditions where filters get bypassed
```

### Methodology Rules (apply to all topics)
1. **ACTIONS OVER RHETORIC** — only verified events move posteriors
2. **TAG EVERYTHING** — separate rhetoric from action in evidence log
3. **DON'T FRONT-RUN** — set tripwires and let news confirm
4. **SOCIALIZATION DETECTION** — recognize narrative prep patterns

---

## Phase 5: Configure Data Feeds

Quantitative observables that track the situation. Each feed has:
- A current value and unit
- A pre-event baseline (for % change computation)
- An "as of" timestamp

Pick 3-8 feeds that are:
- Publicly available / verifiable
- Updated at least daily
- Relevant to at least one hypothesis

---

## Phase 6: Set Search Queries

Define 4-8 web search queries the system uses to find new evidence.
Use `{date}` placeholder for current date injection.

---

## Phase 7: Governor Validation

Before the topic goes live, run the Governor health check:

```bash
python engine.py govern <slug>
```

The Governor checks:
- **R_t scoring**: Is evidence fresh enough?
- **Hypothesis admissibility**: Are all hypotheses falsifiable?
- **Entropy**: How uncertain is the model? (high entropy early = good)
- **Anti-indicator coverage**: Does every hypothesis have a path to rejection?

Fix any issues the Governor flags before proceeding.

---

## Phase 8: Update Cycle

Each update cycle follows this sequence:

```
1. Load topic state
2. Search for news using configured queries
3. Log all new evidence with provenance tags
4. Check indicators against verified evidence
5. Run Governor pre-commit hallucination checklist
6. If indicators fired → compute posterior update with evidence_refs
7. If no indicators → HOLD posteriors (log the hold)
8. Update data feeds
9. Update watchpoints
10. Save state
11. Generate brief and dashboard snapshot
```

### Governor Hallucination Checklist (10 failure modes)
Before any posterior update, verify:
1. **no_evidence** — Is there actual new evidence, or just analysis?
2. **confidence_inflation** — Is the shift proportional to evidence strength?
3. **repetition_as_validation** — Is this the same evidence cited again?
4. **stale_evidence** — Is the evidence recent enough to act on?
5. **circular_reasoning** — Does the evidence depend on the conclusion?
6. **modal_confusion** — Are you confusing "could happen" with "is happening"?
7. **citation_drift** — Does the source actually say what you think it says?
8. **evidence_laundering** — Is rhetoric being treated as a kinetic event?
9. **quorum_failure** — Is a single source driving a major shift?
10. **scope_creep** — Is the evidence relevant to THIS question?

---

## Phase 9: Dashboard Snapshots

Generate standalone HTML dashboards at key moments:

```bash
# After each update cycle
python engine.py dashboard <slug> "event-label"

# List all snapshots
python engine.py dashboards [slug]
```

Snapshots are self-contained (state baked into HTML), stored in `dashboards/{slug}/`,
and gitignored. They provide a time-series of analytical state that can be
reviewed, compared, and shared without running the server.

---

## Quick Start: New Topic in 5 Minutes

```bash
# 1. Create from template
python engine.py scaffold <slug>

# 2. Edit the generated file
#    Fill in: question, resolution, hypotheses, indicators, actors, feeds
$EDITOR topics/<slug>.json

# 3. Validate
python engine.py validate <slug>

# 4. Run Governor check
python engine.py govern <slug>

# 5. First dashboard
python engine.py dashboard <slug> "initial"

# 6. Start update cycles
python engine.py brief <slug>
```

---

## Example: Hormuz Strait Closure

See `topics/hormuz-closure.json` for a fully worked example demonstrating:
- 4 duration hypotheses with midpoints
- 15 tiered indicators (4 critical, 5 strong, 5 suggestive, 6 anti)
- 3-actor decision model (Trump, CENTCOM, Iran)
- 2 sub-models (MEU Mission, Trump Ultimatum)
- 8 data feeds (oil, traffic, casualties)
- 16 evidence entries with provenance
- Posterior history showing 9 updates over 20 days

This is ONE instance of the framework. Any Current Thing can be scaffolded
the same way.
