# Skill: Red Team

Challenge a posterior update with structured devil's advocate analysis.
Generates counterevidence, scores it, and produces a formal challenge.

## When to use

- Before committing a large posterior shift (>10pp on any hypothesis)
- When governance flags overconfidence (uncertainty < 0.1)
- When you want to stress-test a thesis before it hardens
- Periodically on high-stakes topics (ALERT classification)

## Python calls

### Generate a full red team challenge

```python
from engine import load_topic
from framework.red_team import generate_red_team, format_red_team_challenge

topic = load_topic("calibration-topic-slug")

# Proposed posteriors you want to challenge
proposed = {"H1": 0.60, "H2": 0.25, "H3": 0.10, "H4": 0.05}

challenge = generate_red_team(topic, proposed)
# Returns structured challenge with:
# - counterevidence for each hypothesis being elevated
# - devil's advocate scores
# - direction hints (which way the challenge pushes)
# - escalation tags

# Format for human review
formatted = format_red_team_challenge(challenge)
print(formatted)  # Markdown-formatted challenge document
```

### Score counterevidence independently

```python
from framework.red_team import score_counterevidence

scores = score_counterevidence(
    topic,
    hypothesis_key="H1",
    counterevidence=[
        {"text": "Counter-claim 1", "source": "...", "strength": "HIGH"},
        {"text": "Counter-claim 2", "source": "...", "strength": "MEDIUM"},
    ]
)
```

### Compute devil's advocate score

```python
from framework.red_team import compute_devil_advocate_score

da_score = compute_devil_advocate_score(
    counterevidence=scored_evidence,
    current_posterior=0.49,
    proposed_posterior=0.60,
)
# Returns score indicating how strong the case against the update is
```

## Red team workflow

1. **Identify the update** — which posteriors are shifting and by how much
2. **Generate challenge** — `generate_red_team()` produces structured counterarguments
3. **Evaluate** — does the counterevidence invalidate the update, weaken it, or fail?
4. **Decide** — proceed with update, reduce magnitude, or hold

## When red teaming is mandatory

The framework doesn't enforce mandatory red teaming, but these situations
should trigger it:

- Any Tier 1 indicator fire (15-30pp shift)
- Posterior crossing 0.50 (majority threshold)
- Posterior dropping below 0.10 (near-elimination)
- Health already DEGRADED or CRITICAL
- Topic classification at ALERT level

## Constraints

- Red teaming is adversarial BY DESIGN. The point is to find weaknesses,
  not to confirm the update.
- Counterevidence from the red team doesn't automatically block an update.
  It provides structured pushback for the operator to evaluate.
- Don't red team trivial updates (Tier 3 suggestive shifts of 1-2pp).
  Save it for decisions that matter.
