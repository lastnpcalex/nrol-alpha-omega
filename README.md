<p align="center">
  <img src="logo.png" width="300" alt="NROL-αΩ mission patch — DELENDA EST CURRENT THING" />
</p>

# Necrorationalist Research Operations-αΩ

**Generalized epistemic Bayesian estimator for Current Thing.**

A topic-agnostic framework for tracking any predictive news question with measurable resolution criteria. Define a question, set hypotheses, wire up indicators, and the engine maintains posteriors with epistemic rigor — evidence-first, never vibes.

Any Current Thing can be decomposed into hypotheses, indicators, actor models, and evidence. This framework enforces that decomposition and keeps you honest about what you know and don't know.

## Quickstart

```bash
# Launch the dashboard
python server.py

# Check a topic from the CLI
python engine.py list
python engine.py show my-topic

# Run the epistemic governor
python governor.py report my-topic
python governor.py rt my-topic
python governor.py voi my-topic
python governor.py chain my-topic H3
```

## Architecture

```
engine.py          Bayesian update engine — governor-gated posteriors, evidence enrichment
governor.py        Epistemic governor — R_t scoring, claim lifecycle, admissibility, VoI
server.py          Multi-topic HTTP server (port 8098)
dashboard.html     Dark-theme intelligence dashboard with governance health display
topics/            One JSON state file per active topic
briefs/            Generated briefings per topic
```

## Creating a New Topic

1. Copy `topics/_template.json` to `topics/{your-slug}.json`
2. Fill in: question, resolution criterion, hypotheses, indicators, actor model
3. Reload the dashboard — it auto-discovers topics

## Governor-Gated Engine

The governor is not a read-only linter — it is a hard gate on all engine mutations. Every hypothesis creation, posterior update, and evidence addition passes through governance checks before being accepted.

- **Topic creation** — `validate_hypotheses()` runs admissibility checks; INADMISSIBLE topics are blocked
- **Posterior updates** — `check_update_proposal()` gates all shifts; critical failures (no evidence, circular reasoning) raise `GovernanceError`
- **Evidence enrichment** — `add_evidence()` auto-classifies entries with ledger type, claim state, and effective weight
- **Governance snapshots** — every `save_topic()` embeds a live governance health report (R_t, entropy, admissibility)
- **Prediction detection** — future-tense claims are auto-downgraded to PROPOSED (0.5 weight) regardless of provenance
- **Rhetoric guard** — RHETORIC-tagged evidence used to justify posterior shifts triggers a warning

## Epistemic Governance

The governor (`governor.py`) enforces analytical discipline:

- **R_t = PD/E** — evidence freshness scoring (SAFE / ELASTIC / DANGEROUS / RUNAWAY)
- **Dual ledger** — facts (auto-decay) vs decisions (explicit supersession)
- **Claim lifecycle** — PROPOSED → SUPPORTED → CONTESTED → INVALIDATED
- **Admissibility gating** — validates hypothesis quality (setpoint clarity, observability, falsifiability)
- **Value of Information** — prioritizes search queries by expected information gain
- **Hallucination checklist** — 11 failure modes checked before any posterior update
- **Constraint chain** — full audit trail of how each hypothesis moved from prior to current

## Acknowledgments

The epistemic governance layer is built on patterns from **[@unpingable](https://github.com/unpingable)**'s **[Agent Governor](https://github.com/unpingable/agent_governor)** framework. Specifically:

- The **R_t = PD/E control equation** for evidence freshness scoring
- The **dual-ledger design** (facts vs decisions) for separating empirical observations from analytical choices
- The **claim lifecycle** (PROPOSED → SUPPORTED → CONTESTED → INVALIDATED) for tracking evidence state
- **Admissibility gating** for hypothesis quality validation (setpoint clarity + observability)
- **Value of Information** prioritization for directing search effort
- The **hallucination failure modes** (extended to 11) as a pre-commit checklist for posterior updates
- The **monotonic constraint compiler** pattern adapted as constraint chain auditing

Agent Governor is a control and evidence layer for supervising tool-using AI agents. Its core insight — "natural language is a proposal, not an authority" — translates directly to Bayesian estimation: rhetoric is a proposal, only verified evidence moves posteriors.

Thank you to unpingable for making the framework public.

## License

Do what you want with it. *Delenda est Current Thing.*
