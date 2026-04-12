# NROL-AO Skills

Structured prompts for AI coding assistants (Claude Code, gemini-cli, Cursor, etc.)
to operate the NROL-AO Bayesian estimation framework correctly.

Each skill is a self-contained prompt that teaches the assistant how to invoke
the framework's actual Python functions, respect governor constraints, and avoid
the epistemic failure modes the framework was designed to prevent.

## How to use

### Claude Code
Copy the relevant skill file contents into your CLAUDE.md or reference them
in conversation: "Use the `triage` skill to process this headline."

### gemini-cli
Add to your `.gemini/instructions.md` or paste into context.

### Any assistant
These are model-agnostic. The prompts describe what to call and what constraints
to respect. The assistant handles the mapping to tool calls.

## Skill index

| Skill | File | When to use |
|-------|------|-------------|
| **Triage** | [triage.md](triage.md) | New headline/evidence arrives — classify, route, assess source |
| **Update Cycle** | [update-cycle.md](update-cycle.md) | Fire indicator, update posteriors, run governance checks |
| **Evidence** | [evidence.md](evidence.md) | Add evidence to a topic, lint it, check contradictions |
| **Governance** | [governance.md](governance.md) | Run epistemic health audit, R_t scoring, admissibility checks |
| **Topic Design** | [topic-design.md](topic-design.md) | Create or modify a calibration topic from scratch |
| **Dependencies** | [dependencies.md](dependencies.md) | Wire cross-topic dependencies, check staleness, propagate alerts |
| **Source Trust** | [source-trust.md](source-trust.md) | Register sources, calibrate trust, verify claims |
| **Red Team** | [red-team.md](red-team.md) | Challenge a posterior update with devil's advocate scoring |
| **Calibration** | [calibration.md](calibration.md) | Score predictions, compute Brier scores, backfill from outcomes |

## Constraint: Governor-first

Every skill assumes the Governor is the authority. Key constraints that apply
across ALL skills:

1. **Posteriors sum to 1.00** — always. No exceptions. Check after every update.
2. **Only fired indicators move posteriors** — rhetoric, analysis, and forecasts
   are logged as evidence but do NOT shift posteriors unless a pre-registered
   indicator's observable threshold is met.
3. **Claim lifecycle** — every evidence entry has a claimState
   (PROPOSED/SUPPORTED/CONTESTED/INVALIDATED). Only SUPPORTED claims carry
   full weight.
4. **Source trust chain** — per-topic calibration > cross-topic domain >
   cross-topic overall > SOURCE_TRUST base priors > 0.50 unknown fallback.
5. **5 lint failure modes** — every evidence entry must pass:
   rhetoric_as_evidence, recycled_intel, anchoring_bias, phantom_precision,
   stale_evidence.
6. **Pre-committed effects** — indicator posteriorEffect values are declared
   at topic design time, not invented at fire time.
