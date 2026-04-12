<p align="center">
  <img src="logo.png" width="300" alt="NROL-αΩ mission patch — CURRENT THING DELENDA EST" />
  <br/>
  <sub>Simplified vector files available (AI/SVG). Patch art inspired by <a href="https://github.com/vgel">@vgel</a>.</sub>
</p>

# NROL-αΩ

**Necro Rationalist Operations Laboratory-αΩ** — A personal epistemic operating system.

Governor-gated Bayesian estimation engine that scaffolds the human-LLM pair so neither can degrade the other's reasoning. The human brings judgment. The LLM brings perception, tool use, and knowledge retrieval. The governor prevents both from updating on vibes, recycling stale evidence, or mistaking repetition for confirmation.

Not a forecasting tool. A framework for navigating uncertainty across every domain that matters to your life — geopolitics, economics, AI, climate, finance — with tracked beliefs, auditable reasoning, and calibration feedback that tells you where you're actually wrong.

## Why This Exists

You want to understand what's happening in the world without becoming a full-time forecaster. You want to drop a headline into a system and know whether it matters, how much, and to what. You want a record of how your beliefs actually evolved, not how you remember them evolving. And you want the LLM helping you to be structurally prevented from doing what LLMs do worst: confidently agreeing with whatever you already believe.

The system solves a specific problem: **an LLM without guardrails will sycophantically mirror your priors, and a human without structure will drift without noticing.** The governor breaks both failure modes by making every update pass through mechanical checks — cite your evidence, pass the hallucination checklist, survive the pre-commit gate, or get blocked. The LLM doesn't get to update on vibes. Neither do you.

Common failure modes this makes structurally harder:

1. **Anchoring**: you pick a number and then find evidence to support it
2. **Source laundering**: a rumor gets repeated across outlets and starts looking like consensus
3. **Rhetoric-as-evidence**: a politician's threat gets treated like an observed event
4. **Stale priors**: yesterday's assessment gets copy-pasted as today's with no new information
5. **Confirmation bias**: counterevidence gets lower weight because it's inconvenient
6. **Sycophantic convergence**: the LLM agrees with you, you feel validated, both walk away more confident and no more accurate

Every mutation — adding evidence, shifting posteriors, updating sub-models — passes through governance checks. The system tracks its own calibration (Brier scores), detects contradictions in the evidence log, and maintains domain-specific trust ratings for sources based on their empirical track record.

The goal isn't to be right. The goal is to *know how wrong you are* and get less wrong over time.

## How You Use It

### Daily (2 minutes): Triage

Drop a headline into the triage function. The system checks it against pre-registered indicators, watchpoints, and domain keywords across all active topics and tells you:

- **INDICATOR_MATCH → UPDATE_CYCLE**: This fires a pre-registered indicator. You already committed to what this means for your posteriors at topic design time. Run the update.
- **TOPIC_RELEVANT → MONITOR/REVIEW**: This touches a topic but doesn't match a specific indicator. Log it as evidence if substantive. If the same kind of event keeps showing up with no indicator match, you need a new indicator — the system's gap is now visible.
- **IRRELEVANT → IGNORE**: Doesn't touch any active topic. Move on.

The triage layer draws on SOC alert triage architecture from cybersecurity — matching incoming signals against pre-registered indicators of compromise. Nobody has applied this pattern to personal epistemic use before. It includes a third output mode (TOPIC_RELEVANT) that addresses the flexibility critique from [Millidge (2024)](https://www.beren.io/2024-05-05-Does-Scaffolding-Help-Humans/): the system doesn't only see what it pre-registered. It also flags novel events that touch a topic's domain without matching a specific indicator.

```python
from engine import triage_headline
r = triage_headline("CENTCOM announces new phase of operations in Persian Gulf", "CENTCOM")
# → INDICATOR_MATCH on hormuz-closure, fires 'new_phase' (tier1_critical)
# → Pre-committed effect: H3/H4 +15-25pp
# → Source trust: 95% (high)
# → Action: UPDATE_CYCLE
```

### Weekly (30 minutes): Update cycle

Pick the topic with the worst R_t (evidence staleness score). Run a full governed update — intel search, evidence ingestion, indicator check, posterior update or hold, brief generation. The governor enforces the full OODA loop: search for genuinely new information, validate before ingesting, cite evidence for every shift, and pass the 14-point hallucination checklist before posteriors move.

### Ongoing: The Mirror

The mirror dashboard (`/mirror`) is the longitudinal surface — how your beliefs have actually evolved, not how you remember them evolving:

- **Cross-topic overview**: all topics at a glance with posterior bars, governance health, R_t regime, last updated
- **Drift alerts**: stale evidence, stale cross-topic dependencies, R_t in DANGEROUS/RUNAWAY
- **Posterior trajectories**: time-series charts of how each hypothesis has moved over weeks and months
- **Dependency graph**: which topics feed into which, with stale edges highlighted

This is the product nobody else has. Metaculus tracks point estimates. Prediction markets track prices. This system tracks the full trajectory with governance metadata, evidence provenance, source trust histories, and calibration feedback attached.

### Cross-Topic Dependencies

Topics are not independent. "Will there be a recession?" and "Where will the Fed rate be?" are correlated through shared evidence and shared causal structure. The dependency system lets you declare these relationships:

```json
{
  "dependencies": {
    "upstream": [{
      "slug": "calibration-us-recession-2026",
      "assumptions": {"H1": 0.55, "H3": 0.15},
      "tolerance": 0.08
    }]
  }
}
```

When the upstream topic's posteriors drift beyond the assumed values, the downstream topic's governance health degrades and the mirror shows a stale dependency alert. The operator reviews whether the assumption still holds. This is alert-based, not auto-propagation — the math for Bayesian forecast reconciliation exists ([Athanasopoulos et al., 2024](https://robjhyndman.com/papers/hf_review.pdf)), but the operator decides whether to act.

### Loom Integration

The system is designed to be operated through [A Shadow Loom](https://github.com/lastnpcalex/a-shadow-loom) — a tree-branching conversation interface where every chat is a directed graph, not a linear thread. The mirror dashboard embeds in the loom as a persistent panel. When you see a drift alert or a triage result that needs investigation, you branch into a conversation with Claude Code that has the full NROL engine loaded.

The tree structure maps to analytical branching: explore "what if we fire this indicator" on one branch and "what if we hold" on another. Each branch preserves the reasoning. The topic's posteriorHistory is linear, but the loom captures the *deliberation* that produced it — the part no other system preserves.

The mirror is the read surface (what does the system know). The loom is the write surface (what should we do about it).

### Epistemic Limitations

This engine is honest about what it is and what it isn't.

**What it is**: a Bayesian estimation engine where the update mechanics are principled and the governance layer enforces epistemic discipline. Posteriors are computed via Bayes' theorem from explicit likelihoods. Evidence weight feeds back into the update via a probabilistic mixture model — contested evidence is treated as a mixture of signal and noise, not discarded or blindly trusted. Source trust is Bayesian-updated per domain with surprisal-weighted likelihood ratios, so a source correctly predicting something surprising earns more trust than one confirming the obvious. Calibration is tracked via Brier scores and fed back into governance health.

**What it isn't**: a parametric generative model with closed-form likelihood functions. You can't call `P(evidence | H3)` and get a number from a distribution. But the system does contain a **distributed qualitative generative model** — the topic state file encodes a structured causal story about how the world produces evidence under each hypothesis:

- **Conditional probability tables** in sub-models: `P(H_i | scenario)` is specified explicitly (e.g. `khargConditionalHormuz: {H1: 0.0, H2: 0.05, H3: 0.35, H4: 0.6}`)
- **Indicators as likelihood proxies**: each indicator's `posteriorEffect` encodes which observables are more probable under which hypotheses — a tier-1 indicator with "H3/H4 surge" is implicitly saying P(indicator fires | H3) >> P(indicator fires | H1)
- **Actor decision models**: decision styles, biases, filters, and overrides model how key actors *generate* the evidence you observe. Trump's fixation-driven decision style predicts different observables than institutional rationality.
- **Tag direction hints**: KINETIC → H3/H4, DIPLO → H1/H2 encode which evidence types are more probable under which hypotheses

`suggest_likelihoods()` mechanizes part of this model — converting indicator effects and sub-model conditionals into explicit likelihood ratios via inverse Bayes. The actor models and tag hints are currently used by the LLM operator implicitly when supplying likelihoods; they're structured context, not yet code.

The human judgment lives in the topic design: defining hypotheses, setting indicator thresholds, specifying actor models, writing conditional tables. Everything downstream — the likelihood derivation, the Bayesian update, the evidence weighting, the governance checks — is mechanical.

#### The LLM-as-operator architecture

The system is designed to be operated by a language model — not as a novelty, but because the hardest part of intelligence analysis (translating unstructured reports into structured likelihood judgments) is exactly what LLMs do well, and the hardest failure mode of LLMs (confident hallucination with no self-awareness) is exactly what the governor was built to catch.

The architecture splits the problem:
- **The LLM** handles perception: reading news, identifying what's new vs. recycled, assessing source quality, mapping observations to hypotheses. This is where human-like judgment lives.
- **The engine** handles inference: Bayes' theorem, mixture model attenuation, entropy computation, Brier scoring. No judgment calls — pure math on whatever the operator feeds it.
- **The governor** handles discipline: rhetoric detection, hallucination checklists, evidence freshness scoring, admissibility gates. It exists because LLMs will confidently update posteriors on vibes if you let them. The governor doesn't let them.

TTLs are the critical mechanism here. Every evidence tag has a time-to-live — OSINT decays in 24 hours, commodity prices in 8 hours, diplomatic signals in 48 hours. When evidence ages past its TTL, governance health degrades. This isn't a suggestion; it's a forcing function that makes the operator go *search for new information* rather than recycling stale context. An LLM without TTL pressure will happily re-summarize last week's brief and call it an update. The TTL makes that behavior visible in the health score.

The hallucination checklist (`governor.hallucination_check()`) runs before every posterior update: Are you citing evidence that actually exists in the log? Is the evidence recent enough? Are you double-counting same-chain entries? Did you actually search, or are you pattern-matching from training data? These are exactly the failure modes that emerge when an LLM operates a forecasting system without guardrails.

This is not "AI-assisted analysis." It's a mechanical Bayesian system that uses an LLM as its sensory organ and a governor as its immune system.

#### Fundamental constraint: conditional dependence between evidence

This is the system's single biggest theoretical weakness, and it's not fully solvable without a joint distribution over sources — which no one has for geopolitical intelligence.

Intelligence sources are correlated in ways that are often opaque and dynamic. A Reuters correspondent in Dubai and an AP correspondent in Dubai might be independently reporting, or they might both be working off the same CENTCOM background briefing. Two "independent" confirmations that trace to the same satellite imagery are one data point, not two. When the system counts them as independent corroboration, it inflates confidence.

The engine provides **information-chain tracking** (`informationChain` field on evidence entries) so operators can declare when entries trace to the same primary source — the governor will not count same-chain entries as independent corroboration. But this only handles *known* dependencies. Most source correlations are invisible: shared briefings, shared imagery access, shared wire service feeds, herd behavior among analysts.

A proper Bayesian treatment would model the full joint distribution over sources. This engine does not attempt that. It takes the pragmatic position: make dependencies *declarable* when known, discount same-chain evidence automatically, and accept that undeclared dependencies will occasionally inflate confidence. The Brier score feedback loop is the long-run corrective — if correlated evidence is systematically overcounted, calibration will degrade and the operator will see it. But Brier scores aggregate over all sources simultaneously; they can't isolate *which* correlations are causing miscalibration. The operator still has to diagnose that.

This is an honest limitation, not a planned feature. If you have ideas for tractable approaches to source-correlation modeling in sparse-evidence domains, we'd like to hear them.

## The Loop

The system's update cycle maps to Boyd's OODA loop, but the key insight is *where judgment lives vs. where mechanism lives*. Human and LLM judgment are confined to perception (what happened?) and topic design (what matters?). Everything downstream — the Bayesian math, the governance checks, the calibration scoring — is mechanical. The governor exists to enforce that boundary.

```mermaid
flowchart LR
    subgraph OBSERVE ["OBSERVE"]
        direction TB
        Search["Search feeds\n& sources"]
        Identify["Identify what's\nnew vs. recycled"]
    end

    subgraph ORIENT ["ORIENT"]
        direction TB
        Design["Topic design gate\ncoverage · distinguishability\nprior justification"]
        Enrich["Evidence enrichment\nclaim state · source trust\ncontradiction detection"]
        Governor["Governor gate\n14 failure modes\nR_t · entropy · KL"]
    end

    subgraph DECIDE ["DECIDE"]
        direction TB
        Likelihoods["Set likelihoods\nP(E|H_i) for each\nhypothesis"]
        Bayes["Bayesian update\nP(H|E) = P(E|H)P(H)\n/ sum P(E|H_j)P(H_j)"]
        Suggest["suggest_likelihoods()\ninverse Bayes from\nfired indicators"]
    end

    subgraph ACT ["ACT"]
        direction TB
        Brief["Generate brief\nassessment · watchpoints\nindicator status"]
        Calibrate["Brier score snapshot\ncalibration tracking"]
        Save["save_topic()\ngovernance embed\ndesign gate embed"]
    end

    OBSERVE -->|"LLM · web tools"| ORIENT
    ORIENT -->|"function · mechanical"| DECIDE
    DECIDE -->|"function · Bayes theorem"| ACT
    ACT -->|"LLM · next cycle"| OBSERVE

    Search -->|"LLM + web"| Identify
    Design -->|"function"| Enrich -->|"function"| Governor
    Likelihoods -->|"LLM or function"| Bayes
    Suggest -.->|"function · optional"| Likelihoods
    Brief -->|"LLM"| Calibrate -->|"function"| Save

    style OBSERVE fill:#1a1a2e,stroke:#4a9eff,color:#eee
    style ORIENT fill:#1a1a2e,stroke:#e94560,color:#eee
    style DECIDE fill:#1a1a2e,stroke:#f5a623,color:#eee
    style ACT fill:#1a1a2e,stroke:#0f9b58,color:#eee
```

**Where judgment lives:**

| Phase | Who | What they do |
|-------|-----|-------------|
| **Observe** | LLM + web tools | Search for news, read sources, determine what's new vs. recycled. This is perception — the hardest part to mechanize and where LLMs earn their keep. |
| **Orient** | Function (mechanical) | Design gate, evidence enrichment, governor checks. Zero judgment — structural validation, claim-state assignment, contradiction detection, source trust lookup. |
| **Decide** | LLM sets likelihoods *or* function derives them from indicators | The operator says "how likely is this evidence if H3 is true?" — that's the judgment call. Or `suggest_likelihoods()` derives it mechanically from pre-committed indicator definitions. Either way, Bayes' theorem does the math. |
| **Act** | LLM writes brief, function scores | Brief synthesis is LLM work. Brier scoring, governance snapshots, design gate embedding are all mechanical. The brief is the deliverable; the scores are the accountability. |

The governor's role is to make the boundary between judgment and mechanism *enforceable*. An LLM without the governor will confidently update posteriors on vibes. The governor forces it to show its work — cite evidence, pass the hallucination checklist, survive the pre-commit gate — or get blocked.

## How It Works (Detailed Pipeline)

```mermaid
flowchart TD
    Intel["Web Search / Intel\n(manual or agent)"]

    subgraph ADD ["add_evidence()"]
        direction TB
        Enrich["**Governor Enrichment**\nClassify FACT or DECISION\nAssess claim state: PROPOSED / SUPPORTED\nCompute weight = claim state x source trust\nDetect rhetoric, predictions, duplicates"]
        Contra["**Contradiction Check**\nDIRECT (HIGH) · FEED_MISMATCH (HIGH)\nMAGNITUDE (MEDIUM) · TEMPORAL (LOW)"]
        SrcCal["**Source Calibration**\nTrack claim by source x domain tag\nBayesian update on confirmation/refutation\nDomain > source identity (ECON 99% vs RHETORIC 0%)"]
        Enrich --> Contra --> SrcCal
    end

    subgraph UPDATE ["update_posteriors() / bayesian_update()"]
        direction TB
        Gate["**Governor Pre-Commit Gate**\n14 failure modes checked\nEvidence supports shift direction\nNo circular reasoning or rhetoric-only\nNo unresolved HIGH contradictions\nShift proportional to evidence weight\nbayesian_update: mechanical P(H|E) from likelihoods"]
        Block["CRITICAL failure\n**GovernanceError** (hard block)"]
        Warn["HIGH failure\nWarning + audit trail"]
        Force["Force override\navailable with audit log"]
        Gate -->|"CRITICAL"| Block
        Gate -->|"HIGH"| Warn
        Gate -->|"blocked + --force"| Force
    end

    subgraph SAVE ["save_topic()"]
        direction TB
        Gov["Governance snapshot\nR_t freshness · Entropy · KL from prior"]
        Expire["Expired hypothesis detection\nPartial Brier scoring"]
        Snap["Prediction calibration\nsnapshot"]
        Gov --> Expire --> Snap
    end

    Output["Brief Output + Dashboard"]

    Intel --> ADD
    ADD --> UPDATE
    UPDATE -->|"passed"| SAVE
    Force --> SAVE
    SAVE --> Output

    style ADD fill:#1a1a2e,stroke:#e94560,color:#eee
    style UPDATE fill:#1a1a2e,stroke:#f5a623,color:#eee
    style SAVE fill:#1a1a2e,stroke:#0f9b58,color:#eee
    style Block fill:#8b0000,stroke:#e94560,color:#fff
    style Force fill:#444,stroke:#f5a623,color:#fff
```

## Bayesian & Information-Theoretic Mechanics

The engine implements several formal mechanisms from Bayesian inference and information theory. These aren't decorative — they're load-bearing parts of the update pipeline that control how evidence flows into posteriors.

### Posterior Updates via Bayes' Theorem

`bayesian_update()` computes posteriors mechanically:

$$P(H_i|E) = \frac{P(E|H_i) \cdot P(H_i)}{\sum_j P(E|H_j) \cdot P(H_j)}$$

The operator supplies likelihoods P(E|H_i) for each hypothesis — "how probable is this evidence if H_i is true?" — and the engine handles the rest. Both raw and adjusted likelihoods are recorded in `posteriorHistory` for full auditability.

### Evidence Weight Attenuation (Mixture Model)

Evidence quality feeds into the Bayes computation through a proper probabilistic mixture model. Each piece of cited evidence has an `effectiveWeight` *w* representing the probability that the evidence is genuine signal rather than noise:

$$P(E|H_i) = w \cdot P(E|H_i,\text{real}) + (1-w) \cdot P(E|\text{noise})$$

where P(E|noise) = mean of raw likelihoods across all hypotheses (uninformative — identical for all H, so the noise component produces zero posterior movement after normalization). At *w*=1.0, the full likelihood passes through. At *w*=0, all hypotheses receive the same likelihood and posteriors don't move. At intermediate weights, the update is attenuated proportionally. Unlike a linear interpolation toward a fixed neutral value, this formulation is coherent: it corresponds to a well-defined generative model ("the evidence is real with probability *w*, noise otherwise") and preserves the direction of all likelihood ratios at every weight level.

`effectiveWeight` itself is the product of two factors:
- **Claim state weight**: PROPOSED (0.5) → SUPPORTED (1.0) → CONTESTED (0.2) → INVALIDATED (0.0)
- **Source trust**: Bayesian-updated per source per domain, starting from base priors and refined by claim resolution history with surprisal weighting

### Inverse Bayes: Likelihoods from Indicators

`suggest_likelihoods()` reverses the Bayes computation. Given indicator-defined posterior shifts (e.g. "H3 +15pp"), it derives the likelihoods that would produce those shifts:

$$L(H_i) \propto \frac{P_{\text{target}}(H_i)}{P_{\text{current}}(H_i)}$$

For indicators referencing sub-model scenarios (e.g. "Kharg +10pp"), the function resolves through the topic's conditional probability tables — `P(H_i | \text{scenario})` — to translate sub-model movements into hypothesis-level likelihood ratios.

### KL Divergence — Two Applications

**Prior-domination detection** (`compute_kl_from_prior`): Measures D_KL(current posterior ‖ initial prior). Low entropy + low KL = the model is confident but hasn't moved far from where it started — a sign that confidence is inherited from the prior rather than earned from evidence. The governance system flags this as `PRIOR_DOMINATED`.

**Operator-vs-mechanical divergence** (inside `bayesian_update`): When the operator supplies both likelihoods and their intuitive posteriors, the engine computes D_KL(mechanical ‖ intuitive). Divergence > 0.05 nats triggers a governance note. The mechanical result always wins, but the divergence is logged — making the gap between "what the math says" and "what the operator expected" visible and auditable.

### Shannon Entropy and R_t

The posterior distribution's Shannon entropy H = −Σ p_i log₂(p_i) drives several mechanisms:

- **Uncertainty ratio** (H / H_max): 1.0 = uniform (maximum ignorance), 0.0 = all mass on one hypothesis. Governance flags both extremes — near-maximum means the model isn't discriminating, near-zero means check for overconfidence.
- **R_t (evidence staleness risk)**: An entropy-weighted operational heuristic, not a pure information-theoretic derivation. For each hypothesis, R_t = (entropy contribution × time decay) / evidence recency. The entropy term ensures low-probability tail hypotheses with high surprise value get flagged when stale — a 5% hypothesis that hasn't been checked carries more surprise value if true than a 50% hypothesis. But R_t doesn't model the domain's actual volatility; it uses log-scaled time decay as a proxy. A fast-moving conflict and a slow-moving geological process get the same staleness curve unless the operator tunes `rtConfig` thresholds. This is a practical attention-allocation heuristic that uses entropy as a component, not a statement about information-theoretic optimality.
- **VoI query prioritization**: When entropy is high, the system prioritizes discriminating queries (which hypothesis is right?). When entropy is low, it prioritizes disconfirmation queries (is the leading hypothesis actually wrong?). Unfired high-tier indicators always rank highest.

### Brier Score Calibration

Every posterior update triggers a prediction snapshot. When a topic resolves, `record_outcome()` scores all historical snapshots against ground truth using the Brier score:

$$BS = \frac{1}{N} \sum_{i=1}^{N} (p_i - o_i)^2$$

where *o_i* = 1 for the correct hypothesis, 0 otherwise. Brier scores feed back into governance health — `POORLY_CALIBRATED` (Brier > 0.4) degrades system health. Hypotheses that expire by time (day count exceeds 1.5× the midpoint) get partial Brier scoring without waiting for full topic resolution.

### Bayesian Source Trust with Surprisal Weighting

Source trust isn't a static lookup table. The source ledger tracks claim outcomes per source per domain tag (ECON, KINETIC, DIPLO, etc.) and updates trust via Bayesian likelihood ratios — weighted by how surprising the resolved claim was.

Base LRs (cross-topic): confirmed → LR 3:1, refuted → LR 1:3. Per-topic: confirmed → LR 1.2, refuted → LR 0.7. These are then exponentiated by a surprisal weight:

$$LR_{\text{eff}} = LR_{\text{base}}^{s}, \quad s = \text{clamp}\left(\frac{-\log_2(p_{\text{domain}})}{1\text{ bit}},\ 0.5,\ 2.0\right)$$

where *p*_domain is the confirmation base rate for this domain tag. A source that correctly called something surprising (low base rate of confirmation in that domain) earns up to 2× the trust credit. A source that confirmed the obvious (high base rate) earns as little as 0.5×. This prevents "oil goes up during a war" from earning the same trust boost as "Iran releases hostages by Tuesday." The normalizer of 1 bit means a coin-flip base rate (p=0.5) produces weight 1.0 — the unsurprised default.

Surprisal weighting requires a minimum of 3 resolved claims per domain before it activates. With fewer, the base rate estimate is too noisy and the weighting would amplify noise rather than signal — so the system falls back to unweighted LRs until enough data accumulates.

Trust is stored and queried at four levels of specificity (first match wins): per-topic calibration → cross-topic domain trust → cross-topic overall trust → static base prior. The minimum trust across all cited sources is used (conservative).

## Source Trust: How It Updates

Sources don't have a single trust score. Trust is tracked **per domain** — a source that's excellent at reporting economic data might be unreliable on diplomatic analysis.

```mermaid
block-beta
    columns 3

    block:header:3
        columns 3
        space name["Al Jazeera — Base Trust: 0.60"] space
    end

    block:domains:3
        columns 4
        DIPLO["DIPLO\n0.99\n5/6 confirmed"]
        EVENT["EVENT\n0.94\n17/18 confirmed"]
        DATA["DATA\n0.82\n3/5 confirmed"]
        RHETORIC["RHETORIC\n1.0\n1/1 confirmed"]
    end

    block:effective:3
        columns 3
        space eff["Effective Trust → 0.99"] space
    end

    style DIPLO fill:#0f9b58,color:#fff
    style EVENT fill:#0f9b58,color:#fff
    style DATA fill:#f5a623,color:#fff
    style RHETORIC fill:#0f9b58,color:#fff
    style eff fill:#1a1a2e,stroke:#0f9b58,color:#eee
```

The update mechanism is Bayesian with surprisal weighting:

```mermaid
flowchart LR
    Claim["New claim enters\nevidence log"] --> Tag["Tagged with source\n+ domain (ECON, KINETIC, ...)"]
    Tag --> Scan["Source ledger scans\nfor confirmation/refutation\nfrom *different* sources"]
    Scan -->|"Confirmed"| Surp["Surprisal weight\n-log₂(domain base rate)\nnormalized to 1 bit"]
    Scan -->|"Refuted"| SurpR["Surprisal weight\n-log₂(1 - domain base rate)"]
    Surp --> Up["LR = 3:1 ^ surprisal\nSurprising confirm → up to 9:1\nExpected confirm → down to √3:1"]
    SurpR --> Down["LR = 1:3 ^ surprisal\nSurprising refutation → stronger penalty"]
    Up --> Weight
    Down --> Weight
    Weight["Effective weight =\nclaim_state × min(source_trust)"]

    style Up fill:#0f9b58,color:#fff
    style Down fill:#8b0000,color:#fff
    style Surp fill:#1a1a2e,stroke:#f5a623,color:#eee
    style SurpR fill:#1a1a2e,stroke:#f5a623,color:#eee
```

A source confirmed 5 times in ECON and refuted 3 times in RHETORIC will have high ECON trust and low RHETORIC trust. When that source makes a new ECON claim, it gets high weight. When it makes a RHETORIC claim, it gets low weight. The system learns this automatically from the evidence log. Crucially, the five ECON confirmations don't all count equally — if the domain base rate is 99% (ECON claims are almost always confirmed), each confirmation earns minimal trust credit. A single correct call in a domain where sources are usually wrong is worth more than five correct calls where everyone is right.

**Key finding from testing against live data**: domain predicts reliability far better than source identity (r=0.159 for source alone). ECON claims are 99.4% reliable across all sources; RHETORIC claims are 0% reliable.

## Architecture

```
engine.py                  Topic I/O, add_evidence, update_posteriors, bayesian_update, suggest_likelihoods, save_topic
governor.py                Epistemic governor — 14 failure modes, R_t, entropy, KL from prior, claim lifecycle
server.py                  Multi-topic HTTP dashboard (port 8098)

framework/
├── triage.py              SOC-style news triage — match headlines against indicators, watchpoints, keywords
├── dependencies.py        Cross-topic dependency graph — staleness detection, propagation alerts
├── update.py              Programmatic update pipeline (routine/crisis modes)
├── red_team.py            Devil's advocate — counterevidence scoring, contrarian analysis
├── contradictions.py      Multi-type contradiction detection with severity tiers
├── scoring.py             Brier score calibration, hypothesis expiry, partial scoring
├── source_ledger.py       Claim resolution tracking, Bayesian source trust updates
├── source_db.py           Cross-topic, domain-aware source performance database
├── backfill.py            Historical backfill + outcome-based source scoring
├── compaction.py          Evidence log compaction (preserves key claims + weights)
├── calibrate.py           Base source trust scores, verification functions
├── topic_design_gate.py   Pre-governor design gate — coverage matrix, distinguishability, prior justification + adversarial prompt
├── runner.py              CLI orchestrator
├── lint.py                Evidence log linting (failure mode checks)
└── test.py                Hypothesis test registry

mirror.html                Longitudinal dashboard — trajectories, drift alerts, dependency graph, triage input
topics/                    One JSON state file per active topic (gitignored; calibration-*.json tracked)
briefs/                    Generated intelligence briefs per topic (gitignored)
sources/                   Source database (cross-topic trust tracking)
```

### Key Invariant

**Every mutation goes through the governor.** Never write directly to `topic["evidenceLog"]`, `topic["model"]["hypotheses"]`, or `topic["subModels"]`. Always use `add_evidence()`, `update_posteriors()` / `bayesian_update()`, `update_submodel()`, `hold_posteriors()`. The governor enriches, validates, and gates every change.

**Every topic goes through the design gate.** Before a topic enters the governor's jurisdiction, it must pass `topic_design_gate.py` — a two-stage structural review that runs automatically on every `save_topic()` call:

1. **Mechanical checks** (`run_mechanical_checks()`) — instant, no LLM, no judgment calls. Catches:
   - Missing fields, empty indicator tiers, hypotheses without anti-indicators
   - Non-observable resolution criteria, degenerate hypothesis labels
   - **Coverage matrix**: builds a hypothesis × indicator map by parsing `posteriorEffect` strings with direction (positive/negative). Flags hypotheses with zero indicator coverage (permanently underdetermined) and coverage asymmetry (can only gain or only lose probability)
   - **Distinguishability analysis**: detects hypothesis pairs that share all indicators *and* are moved in the same direction — meaning no evidence can discriminate between them. Direction-aware: two hypotheses affected by the same indicator in opposite directions (H1 +10pp, H2 -5pp) are distinguishable even though they share the indicator
   - **Prior justification**: uniform priors warned as potential lazy defaults; non-uniform priors without a documented rationale in `posteriorHistory` are blocked
   - Indicator observability (flags subjective language like "believe", "seem", "likely")
2. **Adversarial review** (`generate_review_prompt()`) — produces a fixed prompt for an LLM subagent that acts as an adversarial examiner, not a collaborator. Eight checks covering edge cases the mechanical layer can't reach. The subagent must PASS or FAIL each check with specific objections.

The gate is wired into `engine.py` — results are embedded in `topic["governance"]["designGate"]` on every save, including the coverage matrix and indistinguishable pairs. A blocked topic triggers a warning but doesn't prevent saving (the operator needs to fix and re-save).

The governor gates every *update*. The design gate gates the *topic itself*. A bad topic design wastes months of tracking and corrupts the calibration corpus. The design gate catches structural flaws before that investment begins.

```bash
# Run the design gate
python framework/topic_design_gate.py topics/my-topic.json          # mechanical checks
python framework/topic_design_gate.py topics/my-topic.json --prompt  # + adversarial review prompt
```

Two paths for posterior updates:
- **`update_posteriors()`** — operator supplies final posteriors directly. The governor validates them against the hallucination checklist but the Bayesian math is implicit (in the operator's head).
- **`bayesian_update()`** — operator supplies explicit likelihoods `P(E|H_i)` and the engine computes posteriors mechanically via Bayes' theorem. Same governor gate, but the reasoning is auditable: likelihoods are recorded in `posteriorHistory`. Likelihoods are attenuated by the `effectiveWeight` of cited evidence — contested or low-trust evidence produces weaker updates automatically.
- **`suggest_likelihoods()`** — converts fired indicator `posteriorEffect` strings into likelihood ratios via inverse Bayes. Parses explicit pp shifts, qualitative directions (`H3/H4 surge`), and submodel references (resolved through conditional distributions). Returns a structured suggestion for operator review before passing to `bayesian_update()`.

## Quickstart

```bash
# List topics
python engine.py list

# Show topic state
python engine.py show hormuz-closure

# Run a governance report
python governor.py report hormuz-closure

# Run a full update cycle
python framework/runner.py update --topic hormuz-closure --mode routine

# Lint the evidence log
python framework/runner.py lint --topic hormuz-closure

# Run the red team
python -c "
from engine import load_topic
from framework.red_team import generate_red_team, format_red_team_challenge
topic = load_topic('hormuz-closure')
red = generate_red_team(topic, topic['model']['hypotheses'])
print(format_red_team_challenge(red))
"

# Check calibration
python framework/scoring.py hormuz-closure --report

# Ingest source data into the cross-topic database
python framework/source_db.py ingest --topic hormuz-closure

# Triage a headline
python -c "
from engine import triage_headline
import json
r = triage_headline('CENTCOM announces new phase of operations in Persian Gulf', 'CENTCOM')
print(json.dumps(r, indent=2))
"

# Check cross-topic dependencies
python -c "
from framework.dependencies import build_dependency_graph
import json
print(json.dumps(build_dependency_graph(), indent=2))
"

# Launch the dashboard
python server.py
```

## Dashboards

`python server.py` launches the server on port 8098 (binds `0.0.0.0` — accessible over Tailscale or LAN).

### Topic Dashboard (`/`)

The per-topic dashboard auto-detects all topics in `topics/` and renders:

- Posterior distribution bar + historical chart
- Sub-models with scenarios, deadlines, and conditional probabilities
- Indicator status across all tiers (with fired/pending states)
- Data feeds with baseline deltas
- Evidence log (latest 20, color-coded by tag)
- Actor model and methodology rules
- Epistemic governor health (R_t regime, entropy, admissibility, issues)
- Value of Information priority queries

Select topics from the dropdown. Auto-refreshes every 60 seconds.

### Mirror Dashboard (`/mirror`)

The longitudinal surface — how your beliefs have actually evolved across all topics:

- **Triage input**: drop a headline + source, get instant routing across all active topics
- **Cross-topic overview**: all topics at a glance with posterior bars, health badges, R_t regime, staleness
- **Drift alerts**: stale dependencies, DANGEROUS/RUNAWAY R_t, downstream propagation warnings
- **Posterior trajectories**: time-series charts per topic showing how each hypothesis moved
- **Dependency graph**: upstream/downstream edges with stale edge highlighting and drift magnitude

Auto-refreshes every 60 seconds.

### API Endpoints

| Method | Path | Returns |
|--------|------|---------|
| GET | `/topics` | List of all topics (slug, title, status, classification) |
| GET | `/topics/{slug}/state.json` | Full topic state |
| GET | `/topics/{slug}/governance.json` | Live governance report |
| GET | `/overview` | Cross-topic overview (posteriors, health, R_t, staleness) |
| GET | `/trajectories` | Posterior history for all topics |
| GET | `/trajectories/{slug}` | Posterior history for one topic |
| GET | `/dependencies` | Full dependency graph (nodes, edges, stale edges) |
| POST | `/triage` | Triage a headline: `{"headline": "...", "source": "..."}` |

## Requirements

Python 3.10+. Zero external dependencies — stdlib only. No pip install, no venv, no requirements.txt.

## Example: LK-99 Superconductor (Resolved)

The repo includes a historical reconstruction of the LK-99 room-temperature superconductor saga (July-August 2023) as a worked example of the full topic lifecycle.

**Question**: Is LK-99 a room-temperature, ambient-pressure superconductor?

**Hypotheses**:
- H1: Genuine RT superconductor (prior: 0.10)
- H2: Partial — real but not full SC (prior: 0.20)
- H3: Not SC — mundane explanation (prior: 0.50)
- H4: Fraud or severe methodological failure (prior: 0.20)

**Posterior evolution** over 25 days:

```
H3 ███████████████████████████████████████████████ 0.90  ← Cu2S impurity
H1 █                                               0.01
H2 █                                               0.02
H4 ████                                            0.07
```

**What the system caught**:
- Social media hype (RHETORIC tag) → zero posterior movement, correctly ignored
- Huazhong partial levitation video → H2 bump only, not H1 (partial signal ≠ Meissner)
- DFT flat bands (LBNL) → small H1/H2 boost (theoretical support, not proof)
- 6+ failed replications → bulk failure indicator fired, H3 surged
- Cu2S phase transition identified → smoking gun, H3 locked in

**Governance snapshot at resolution**:
- **KL from prior**: 0.39 nats → `MODERATE`. The prior already favored H3 (0.50), so reaching H3=0.90 was a significant move but not a reversal. Honest classification: the evidence confirmed a direction the prior already leaned, rather than overturning it.
- **R_t**: all hypotheses `SAFE` — evidence was fresh at time of resolution.
- **Health**: `DEGRADED` (post-resolution, all 15 evidence entries have aged past TTL — expected for a closed topic).

**How `bayesian_update()` would have worked** (retrospective):

The Aug 3 update (6+ failed replications) could have been expressed as explicit likelihoods:

```python
# If H3 is true (mundane), how likely are 6 failed replications? Very.
# If H1 is true (genuine SC), how likely are 6 failures? Very unlikely.
bayesian_update(topic, likelihoods={
    "H1": 0.05,   # P(6 failures | genuine SC) — almost impossible
    "H2": 0.30,   # P(6 failures | partial SC) — possible if effect is subtle
    "H3": 0.90,   # P(6 failures | not SC) — expected
    "H4": 0.60,   # P(6 failures | fraud) — expected but not certain
}, reason="6+ independent replication failures", evidence_refs=[...])
```

The engine computes posteriors mechanically via Bayes' theorem. If the cited evidence has low `effectiveWeight` (contested claims, low-trust sources), the likelihoods are attenuated via the mixture model — the evidence is treated as a probability-weighted mix of genuine signal and uninformative noise, so weak evidence produces proportionally weaker posterior shifts without distorting likelihood direction. If the operator also supplies their intuitive posteriors, the system logs the KL divergence between mechanical and intuitive results — making the gap between math and intuition visible.

**`suggest_likelihoods()`** — instead of hand-crafting likelihoods, derive them from pre-committed indicator definitions. Indicators are defined at topic creation before the evidence arrives — they're a pre-registered analysis plan, not a post-hoc rationalization. When an indicator fires, the function mechanizes what the operator already committed to:

```python
# Fire an indicator, then get suggested likelihoods
fire_indicator(topic, "t1_bulk_failure", note="6+ labs failed to replicate")
suggestion = suggest_likelihoods(topic, ["t1_bulk_failure"])
# suggestion["suggested_likelihoods"] = {"H1": 0.05, "H2": 0.30, "H3": 1.0, "H4": 0.60}
# suggestion["target_posteriors"] = {"H1": 0.01, "H2": 0.03, "H3": 0.93, "H4": 0.03}
# suggestion["ready"] = True

# Review, optionally adjust, then apply
bayesian_update(topic, suggestion["suggested_likelihoods"],
                reason="6+ replication failures", evidence_refs=[...])
```

For indicators with unparseable effects (e.g. "Thesis confirmation"), `ready` returns `False` and the operator can supply overrides via `override_effects={"indicator_id": {"H1": +5, "H3": -3}}`.

**Information chains** (retrospective): Multiple outlets reported the Huazhong levitation video. Without `informationChain` tracking, each article could have been counted as independent corroboration. With it:

```python
add_evidence(topic, {
    "tag": "EXPERIMENTAL", "source": "reuters",
    "text": "Huazhong University video shows partial levitation of LK-99 sample",
    "informationChain": "huazhong-levitation-video-2023-07",  # same primary source
})
add_evidence(topic, {
    "tag": "EXPERIMENTAL", "source": "scmp",
    "text": "Chinese university demonstrates LK-99 sample levitating",
    "informationChain": "huazhong-levitation-video-2023-07",  # same video, same chain
})
# Governor treats these as ONE evidential unit, not independent corroboration
```

**Source trust after outcome scoring** (carried into future science topics):

| Source | Domain Trust | Why |
|--------|-------------|-----|
| arXiv (Lee & Kim) | 0.25 | Made the wrong claim (20 years sunk cost) |
| Huazhong University | 0.10 | Viral video was ferromagnetic Cu2S, not Meissner |
| IBS Korea (single crystal) | 0.75 | Definitive negative result |
| University of Maryland | 0.75 | Identified the Cu2S mechanism |

If Huazhong publishes an EXPERIMENTAL claim on the next science topic, the governor starts them at 0.10 domain trust instead of 0.50. They have to earn it back.

To explore the LK-99 topic: `python engine.py show calibration-lk99-superconductor`

## Creating a New Topic

1. Copy `topics/_template.json` to `topics/{your-slug}.json`
2. Set `meta.topicType` to one of: `conflict`, `science`, `election`, `tech` (or leave empty for custom)
3. Fill in: question, resolution criterion, hypotheses (with midpoints), indicators, actor model
4. Wire up data feeds with baseline values
5. Choose relevant tags from the tag registry (see below) and list them in `tagConfig.availableTags`
6. Run `python engine.py show your-slug` to verify the governor accepts it
7. Start adding evidence — the system handles enrichment, claim states, and calibration automatically

## Evidence Tags

Tags classify evidence by domain. Each tag has a TTL (how fast it goes stale), a fact/decision classification, and optional direction hints for the red team's heuristic inference.

The system ships with **28 tags** across 6 categories. Pick the ones relevant to your topic:

| Category | Tags |
|----------|------|
| **Universal** | `EVENT` `DATA` `RHETORIC` `INTEL` `ANALYSIS` `OSINT` `POLICY` |
| **Conflict** | `KINETIC` `FORCE` `DIPLO` `SIGINT` |
| **Economic** | `ECON` `MARKET` |
| **Political** | `POLITICAL` `POLL` `LEGAL` `REGULATORY` `JUDICIAL` `LEGISLATIVE` |
| **Science** | `SCIENTIFIC` `EXPERIMENTAL` `TECHNICAL` |
| **Social** | `CORPORATE` `DEMOGRAPHIC` `SOCIAL` `ENVIRONMENTAL` `EDITORIAL` `FORECAST` |

### Topic Type Presets

Setting `meta.topicType` in your topic JSON automatically configures which tags the red team uses for direction inference:

| Topic Type | Example Use Cases | Key Tags | Direction Logic |
|-----------|-------------------|----------|----------------|
| `conflict` | Wars, crises, blockades | KINETIC, FORCE, DIPLO, ECON | Kinetic events argue for longer timelines; diplomacy argues shorter |
| `science` | LK-99, replication studies | EXPERIMENTAL, SCIENTIFIC, TECHNICAL | Lab results and papers push toward confirmation |
| `election` | Elections, referenda | POLL, POLITICAL, LEGAL | Neutral — direction from content, not tag |
| `tech` | AI capabilities, product launches | TECHNICAL, SCIENTIFIC, REGULATORY | Technical demos argue "sooner"; regulation argues "slower" |

### Custom Tag Configuration

For topics that don't fit a preset, configure `tagConfig` in the topic JSON:

```json
{
  "tagConfig": {
    "availableTags": ["EVENT", "DATA", "SCIENTIFIC", "EXPERIMENTAL", "RHETORIC"],
    "directionHints": {
      "EXPERIMENTAL": {"H1": 1, "H2": 1, "H3": -1},
      "SCIENTIFIC": {"H1": 1}
    },
    "escalationTags": ["EXPERIMENTAL"],
    "deescalationTags": ["RHETORIC"]
  }
}
```

## Epistemic Governance

The governor (`governor.py`) enforces analytical discipline through multiple mechanisms:

| Mechanism | What It Does |
|-----------|-------------|
| **R_t (info-theoretic)** | Evidence freshness scoring — entropy contribution × log-time decay / evidence recency → SAFE / ELASTIC / DANGEROUS / RUNAWAY |
| **14 Failure Modes** | Pre-commit checklist (3 CRITICAL, 5 HIGH, 3 MEDIUM, 3 LOW) |
| **Dual Ledger** | Facts (auto-decay) vs decisions (explicit supersession) |
| **Claim Lifecycle** | PROPOSED → SUPPORTED → CONTESTED → INVALIDATED |
| **Contradiction Detection** | DIRECT, FEED_MISMATCH, MAGNITUDE, TEMPORAL — with severity tiers |
| **Source Trust Weighting** | Evidence weight = claim_state × domain-specific source trust |
| **Prediction Detection** | Future-tense claims auto-downgraded to PROPOSED (0.5 weight) |
| **Rhetoric Guard** | RHETORIC-tagged evidence used to justify shifts triggers failure |
| **Hypothesis Expiry** | Auto-detects when dayCount exceeds hypothesis midpoint × 1.5 |
| **Brier Score Tracking** | Snapshot posteriors at each update; score against outcomes |
| **Entropy Monitoring** | Tracks posterior distribution spread for calibration health |
| **KL from Prior** | Detects prior-dominated confidence — sharp posteriors that never moved from the initial prior |
| **Information Chains** | Evidence entries sharing an `informationChain` ID are treated as one evidential unit, not independent corroboration |

### Hallucination Failure Modes (14 total)

| # | Mode | Severity | Trigger |
|---|------|----------|---------|
| 1 | no_evidence | CRITICAL | Posterior shift with no supporting evidence |
| 2 | circular_reasoning | CRITICAL | Evidence cites its own posterior as support |
| 3 | unresolved_contradiction | CRITICAL | HIGH-severity contradiction + shift > 0.02 |
| 4 | stale_evidence | HIGH | >50% of evidence older than R_t window |
| 5 | rhetoric_as_evidence | HIGH | RHETORIC tag used to justify shift |
| 6 | prediction_treated_as_fact | HIGH | Future-tense claim at OBSERVED weight |
| 7 | anchoring | HIGH | Shift too small given evidence weight |
| 8 | source_laundering | HIGH | Same claim from same source counted twice |
| 9 | magnitude_mismatch | MEDIUM | Shift size doesn't match evidence strength |
| 10 | missing_counterevidence | MEDIUM | No devil's advocate check before shift |
| 11 | unfalsifiable_hypothesis | MEDIUM | Hypothesis has no observable resolution |
| 12 | temporal_confusion | LOW | Evidence timestamp inconsistent with claim |
| 13 | feed_key_mismatch | LOW | Data feed reference doesn't match topic |
| 14 | duplicate_evidence | LOW | Semantic duplicate of existing entry |

## Calibration Roadmap

### The problem

The engine's Bayesian mechanics are implemented and tested. What's missing is *empirical validation that the operator is well-calibrated* — that the likelihoods fed into `bayesian_update()` produce posteriors that track reality. Without this, the system is a well-oiled machine pointed at an unknown angle.

Brier scoring infrastructure exists (`snapshot_posteriors`, `record_outcome`, `compute_brier`). The LK-99 topic has been backfilled with 6 prediction snapshots and scores (average Brier 0.069, WELL_CALIBRATED). But backfilled topics validate mechanics, not judgment — you can't un-know the answer. Real calibration requires **prospective predictions**: commit to posteriors *before* resolution, then score honestly when the answer arrives.

### What's needed

1. **Prospective predictions only.** Snapshots must be timestamped before resolution. No retroactive scoring.
2. **Non-cherry-picked topics.** If you only forecast things you're confident about, Brier scores flatter you and teach nothing. Selection criteria, not vibes.
3. **Pre-committed resolution criteria.** Each topic defines what "resolved" means and by when. Open-ended topics that never close are unfalsifiable.
4. **Minimum corpus.** N ≥ 10 resolved prospective topics before deriving empirical weight functions. N ≥ 20 before automating them.
5. **Domain diversity.** Calibration on physics alone doesn't validate geopolitical judgment. The corpus needs to span domains.

### Goal: learn claim-state weights from data

Right now, claim-state weights are hand-set: SUPPORTED = 1.0, PROPOSED = 0.5, CONTESTED = 0.2, INVALIDATED = 0.0. The long-term goal is to derive these empirically — what weight on PROPOSED evidence actually produces the best-calibrated posteriors? This requires enough resolved topics with enough prediction snapshots to fit a curve. We're not there yet, and pretending otherwise would be the kind of false precision the governor exists to catch.

### Proposed calibration topics

Ten topics designed using the framework's own criteria: multiple plausible hypotheses (not just yes/no), observable indicators and data feeds, genuine prior uncertainty, and a time horizon that actually resolves. Each topic below sketches the hypothesis space and the evidence feeds that would drive updates — the operator sets up the full topic file prospectively and commits to regular updates before resolution.

**Topic 1: 750 GeV diphoton excess** (particle physics — backfill only)
- *Hypotheses*: H1: new particle, H2: statistical fluctuation, H3: detector artifact, H4: BSM physics but not a resonance
- *Resolution*: ICHEP 2016 data release. **Already resolved** (H2 won). Backfill like LK-99 — tests mechanics, not prospective judgment.
- *Why include*: operator had documented early skepticism, updated before consensus. Second historical anchor alongside LK-99.

**Topic 2: Iran war — Strait of Hormuz status by August 2026** (geopolitics/trade)
- *Hypotheses*: H1: full unconditional reopen (<3 months), H2: conditional reopen (Iran retains inspection/fee regime), H3: remains effectively closed through August, H4: escalation closes Bab al-Mandeb too
- *Indicators*: daily transit counts, Brent/WTI spread, ceasefire compliance reports, Islamabad negotiation outcomes, Houthi posture shifts
- *Current state*: ceasefire agreed Apr 8 but Hormuz still at standstill as of Apr 10. Iran insists on transit permission regime. Genuinely uncertain — the ceasefire could collapse or calcify.
- *Horizon*: ~4 months. *Difficulty*: hard.

**Topic 3: US recession by Q4 2026** (economics)
- *Hypotheses*: H1: no recession (GDP stays positive), H2: technical recession (2 negative quarters, shallow), H3: significant recession (unemployment >5.5%), H4: stagflation (negative growth + inflation >4%)
- *Indicators*: ISM PMI (below 50 already), consumer confidence (declining 3 quarters), unemployment (4.6% and rising), GDP prints, yield curve, Sahm Rule trigger
- *Current state*: Polymarket prices ~25% recession probability. Mixed signals — labor weakening but growth forecasts still positive (1.5-2.0%). Tariff uncertainty adds volatility.
- *Horizon*: ~6 months (Q3/Q4 GDP prints). *Difficulty*: medium — the indicators disagree, which is exactly when Bayesian updating earns its keep.

**Topic 4: Ukraine — formal ceasefire by end 2026** (geopolitics)
- *Hypotheses*: H1: comprehensive peace deal (territorial settlement), H2: frozen conflict (de facto ceasefire, no agreement), H3: limited truces only (Easter-style, no lasting agreement), H4: escalation (new offensive or external actor entry)
- *Indicators*: negotiation track (Abu Dhabi/Geneva/Paris), territorial control changes, Western arms deliveries, Russian domestic pressure, energy leverage shifts
- *Current state*: 32-hour Easter ceasefire agreed Apr 10, but broader talks stalled. Territory impasse unresolved. Washington distracted by Iran. Genuine multi-hypothesis uncertainty.
- *Horizon*: ~8 months. *Difficulty*: hard.

**Topic 5: South China Sea — kinetic incident with casualties by end 2026** (geopolitics)
- *Hypotheses*: H1: continued gray zone only (no casualties), H2: incident with injuries but no deaths, H3: lethal incident (deaths), H4: MDT Article IV invocation
- *Indicators*: ADIZ incursions, warship near-misses (one on Mar 30), joint exercise frequency (500+ US-PH exercises planned), diplomatic track, China defense budget trajectory
- *Current state*: near-miss between BRP Benguet and PLA frigate Jingzhou. Escalation pattern clear, but both sides have so far avoided casualties. Classic "slow burn, high consequence" — exactly the kind of tail risk the system's entropy weighting is designed to flag.
- *Horizon*: ~8 months. *Difficulty*: hard — low base rate but rising indicators.

**Topic 6: Section 122 tariffs at 150-day expiry (late July 2026)** (economics/policy)
- *Hypotheses*: H1: expire as scheduled, H2: renewed at same 10% rate, H3: expanded (higher rate or broader scope), H4: replaced by bilateral deals (partial rollback)
- *Indicators*: White House statements, Section 301 investigation outcomes, trade deficit data, business lobbying activity, Congressional action, WTO rulings
- *Current state*: 10% tariff on ~$1.2T of imports, effective Feb 24. SCOTUS struck down IEEPA tariffs; Section 122 has a statutory 150-day limit. New Section 301 probes launched into EU, Mexico, China. Political incentives unclear — expiry vs. renewal both have constituencies.
- *Horizon*: ~3.5 months (late July 2026). *Difficulty*: medium — policy prediction with observable leading indicators.

**Topic 7: Fed funds rate by end 2026** (economics)
- *Hypotheses*: H1: no further cuts (stays 3.50-3.75%), H2: one cut (to 3.25-3.50%), H3: two+ cuts (to 3.00-3.25% or below), H4: rate hike (inflation forces reversal)
- *Indicators*: CPI/PCE prints, unemployment rate, Fed dot plot, FOMC minutes language, market-implied probabilities, tariff impact on prices
- *Current state*: Fed held steady in March. Median dot plot projects one cut in 2026. Goldman forecasts two. Bankrate forecasts three. The tariff-inflation tension creates genuine ambiguity — cut for growth or hold for inflation?
- *Horizon*: ~8 months. *Difficulty*: medium — data-driven, falsifiable, multiple expert forecasts to calibrate against.

**Topic 8: US midterms 2026 — House control** (domestic politics)
- *Hypotheses*: H1: Democrats flip House (>218 seats), H2: Democrats gain seats but fall short, H3: Republicans hold (status quo ±5 seats), H4: Republicans gain seats
- *Indicators*: generic ballot polling, Trump approval (currently ~41%, economic approval 31%), special election results, redistricting outcomes, candidate recruitment, fundraising
- *Current state*: historical base rate strongly favors opposition gains in midterms. Trump approval declining (net -19%). But GOP getting midterm polling boost despite Trump's numbers — unusual divergence worth tracking.
- *Horizon*: ~7 months (Nov 2026). *Difficulty*: medium — strong historical prior but current cycle has unusual dynamics.

**Topic 9: Houthi Red Sea posture by mid-2026** (conflict/trade)
- *Hypotheses*: H1: attacks resume at pre-ceasefire intensity, H2: selective targeting (political screening, not indiscriminate), H3: de facto ceasefire holds (no commercial attacks), H4: Houthis escalate beyond Red Sea (Bab al-Mandeb full closure)
- *Indicators*: MARAD advisories, shipping insurance rates, Houthi statements, Iran war ceasefire status, Operation Rough Rider outcomes, maritime tracking data
- *Current state*: Houthis paused commercial attacks after Gaza ceasefire (Oct 2025), resumed Israel strikes in March 2026. Currently screening ships by political identity rather than attacking indiscriminately. Holding Red Sea leverage in reserve. Directly coupled to Hormuz topic — not independent, and that correlation itself is worth tracking.
- *Horizon*: ~3 months. *Difficulty*: medium — dependent on Iran war trajectory.

**Topic 10: Next RTSC claim survives independent replication** (physics)
- *Hypotheses*: H1: claim published and replicated within 12 months (≥2 independent labs), H2: claim published, partial replication (1 lab, or only some properties), H3: claim published, fails replication, H4: no credible claim published in window
- *Indicators*: arXiv preprints, journal publications, replication attempts, materials characterization data, theory predictions
- *Current state*: post-LK-99, the field has higher replication standards and faster debunking cycles. Base rate for H1 is near zero historically. But the operator's prior (skeptical, updated early on LK-99) is testable — does that calibration transfer to the next claim?
- *Horizon*: rolling (12-month window from any new claim). *Difficulty*: hard — rare events with high noise.

**Selection rationale:**
- **Domains**: geopolitics (2, 4, 5), economics (3, 6, 7), domestic politics (8), conflict/trade (9), physics (1, 10). Weighted toward geopolitics/economics because that's where the operator has active judgment to test.
- **Timescales**: 3 months (6, 9) to rolling (10). Clustering at 6-8 months ensures bulk resolution by early 2027.
- **Difficulty**: deliberate mix. Topics 6 and 7 are data-driven (observable indicators, expert forecasts to benchmark against). Topics 4 and 5 are genuinely hard (multi-actor, low-frequency events). Topic 10 is a known-hard problem that tests whether skeptical priors transfer across domains.
- **Correlation structure**: topics 2 and 9 are coupled (Hormuz and Houthi posture move together). Topics 3, 6, and 7 are correlated through macroeconomic channels. This is deliberate — the system needs to handle correlated topics honestly, and the calibration corpus should test that.
- **Topic 1 is the exception**: already resolved, backfill only. Included as a second historical anchor alongside LK-99.

### Timeline

- **Phase 1 — now (April 2026):** Backfill topic 1 (750 GeV). Set up topics 2, 6, and 9 prospectively — these have the shortest horizons (3-4 months) and the most observable indicators. Begin regular update cycles. Topic 2 (Hormuz) already has an active topic file; the others need new ones.
- **Phase 2 — May/June 2026:** Set up remaining topics (3, 4, 5, 7, 8, 10). First prospective resolutions expected: topic 6 (Section 122 expiry, late July) and topic 9 (Houthi posture, mid-2026).
- **Phase 3 — late 2026 / early 2027:** Bulk of resolutions arrive (midterms in November, Fed rate by December, Hormuz/recession/Ukraine by year-end). Compute aggregate Brier scores across domains. If N ≥ 10 resolved: derive preliminary empirical weight functions. If N < 10: identify which topics stalled and why.
- **Phase 4 — 2027+:** If calibration holds across domains, automate claim-state weight derivation. If it doesn't, diagnose *where* operator judgment diverges from outcomes — is it likelihood-setting, indicator design, or evidence selection? The Brier decomposition (reliability + resolution + uncertainty) tells you which component is failing. Feed that back into the topic design process, not just the weight functions.

The system will tell you honestly whether you're calibrated. The only thing it can't do is make you set up the topics in the first place. That's on you.

## Acknowledgments

The epistemic governance layer builds on patterns from **[@unpingable](https://github.com/unpingable)**'s **[Agent Governor](https://github.com/unpingable/agent_governor)** framework:

- The **R_t control equation** concept for evidence freshness scoring (rewritten with information-theoretic grounding: entropy contribution × log-time decay)
- The **dual-ledger design** (facts vs decisions) for separating observations from analytical choices
- The **claim lifecycle** (PROPOSED → SUPPORTED → CONTESTED → INVALIDATED) for evidence state
- **Admissibility gating** for hypothesis quality (setpoint clarity + observability)
- **Value of Information** for directing search effort
- The **hallucination failure modes** (extended from 11 to 14) as a pre-commit checklist
- The **monotonic constraint compiler** adapted as constraint chain auditing

Agent Governor's core insight — "natural language is a proposal, not an authority" — maps directly to Bayesian estimation: rhetoric is a proposal, only verified evidence moves posteriors.

## License

Do what you want with it. *Current Thing delenda est.*
