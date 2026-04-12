# NROL-αΩ — Loom Mirror

## What This Is

The Loom mirror is a browser-rendered interface for NROL-αΩ that runs inside [A Shadow Loom](https://github.com/lawrencecchen/a-shadow-loom) (Claude Code canvas). It provides the same dashboard and mirror views as the server-hosted `dashboard.html` and `mirror.html`, but operates standalone — no `server.py` required.

The critical difference: the Loom mirror connects the UI directly to a Claude agent via the Canvas SDK. This closes the human-in-the-loop gap between "I see a headline" and "the framework processes it."

## Architecture

```
canvas/
├── index.html              Dashboard — topic view, posteriors, evidence, indicators
├── mirror.html             Mirror — cross-topic overview, triage, activity feed
├── topics/*.json           Topic state files (same schema as topics/)
├── activity-log.json       Pipeline audit trail (Claude writes, canvas reads)
├── source_db.json          Cross-topic source performance DB
├── source-trust.json       SOURCE_TRUST base priors
├── NROLAO.svg / .png       Branding
├── triggers/
│   ├── pipeline.md         Standard triage→evidence→calibrate pipeline
│   ├── social-post.md      Social media variant (platform-aware fetch)
│   └── evidence-drop.md    File/screenshot drop processing
└── CLAUDE.md               Canvas instructions (auto-generated)
```

## How It Works

### Data Flow

The canvas is read-only from the browser's perspective. Claude writes files; the canvas renders them.

```
                    ┌─────────────────────────┐
                    │       Canvas (browser)   │
                    │                          │
  headline/URL ───► │  client-side triage      │
                    │  (keyword matching)      │
                    │         │                │
                    │         ▼                │
                    │  Loom.send(trigger)  ────┼──► Claude agent
                    │                          │        │
                    │  ◄── auto-refresh ───────┼────────┤
                    │                          │        │
                    │  activity feed renders   │        ▼
                    │  posteriors update        │   WebFetch URL
                    │  trust bars shift         │   triage against topics
                    └─────────────────────────┘   assess source (trust chain)
                                                   log evidence
                                                   update posteriors
                                                   calibrate source
                                                   write activity-log.json
                                                   write topics/*.json
```

### Input Paths

| Input | Route | Trigger |
|---|---|---|
| Plain headline text | Client-side triage → preview → "Run pipeline" button → `Loom.send()` | `pipeline.md` |
| News article URL | URL detected → skip client triage → `Loom.send()` immediately | `pipeline.md` |
| Social media URL (X, Bluesky, Reddit, etc.) | Platform detected → `Loom.send()` with platform flag | `social-post.md` |
| Dropped file/screenshot | `Loom.uploadAndSend()` via drop zone | `evidence-drop.md` |

### The Pipeline (What Claude Does)

When a trigger fires, Claude runs the NROL-αΩ evidence pipeline:

1. **Fetch** — retrieve content from URL (WebFetch, or Bluesky native skills for bsky.app)
2. **Triage** — match extracted content against all active topics (indicators, watchpoints, domain keywords)
3. **Source assessment** — look up source trust via the Governor's 5-tier chain (per-topic calibration → cross-topic domain → cross-topic overall → base priors → 0.50 unknown)
4. **Evidence logging** — append to topic's `evidenceLog` with tag, text, provenance, claimState, effectiveWeight
5. **Posterior update** — if impact is MODERATE or MAJOR, shift posteriors using `directionHints` and indicator `posteriorEffect`, attenuated by `effectiveWeight`
6. **Source calibration** — if the evidence confirms/refutes existing claims, update `source_db.json` with Bayesian trust adjustment
7. **Activity log** — append full audit trail to `activity-log.json`

The canvas polls `activity-log.json` every 15 seconds and re-renders.

## Epistemic Constraints

The Loom mirror is governed by the same epistemic discipline as the rest of NROL-αΩ. The triggers enforce these constraints explicitly:

### Source Trust

Trust scores come from the framework's calibration chain, never from LLM estimation. The 5-tier lookup:

1. Per-topic `sourceCalibration.effectiveTrust` (Bayesian posterior from resolved claims in this topic)
2. Cross-topic `source_db.json` domain trust (same tag, all resolved topics)
3. Cross-topic `source_db.json` overall trust (all domains)
4. `SOURCE_TRUST` base priors (category-based: wire=0.90, state media=0.40, etc.)
5. **0.50** for unknown sources (maximum ignorance prior)

The LLM is prohibited from inventing, adjusting, or "estimating" trust scores. If a source isn't in the database, it's 0.50. The calibration system learns the real number from resolved claims.

### Rhetoric vs. Evidence

The Governor's lint module flags `rhetoric_as_evidence` as a HIGH severity failure mode. Social media is disproportionately rhetoric. The trigger instructions enforce:

- Factual claims (something happened) → appropriate domain tag, posteriorImpact based on indicator tier
- Rhetoric (opinions, predictions, threats) → tagged RHETORIC, posteriorImpact NONE

Rhetoric does not move posteriors. This is correct behavior, not a limitation.

### Claim Lifecycle

Evidence enters as PROPOSED (0.5 weight), upgrades to SUPPORTED (1.0) when corroborated by a different source, downgrades to CONTESTED (0.2) when contradicted, or INVALIDATED (0.0) when refuted. These weights are fixed by the Governor — the LLM cannot override them.

### Effective Weight

`effectiveWeight = claimState_weight × source_trust_factor`

A PROPOSED claim from an unknown source: 0.5 × 0.5 = 0.25 weight. This means it barely moves posteriors, which is correct — an unverified claim from an untracked source should produce minimal change. The system converges on truth through accumulation and resolution, not through single dramatic updates.

## Setting Up the Loom Mirror

### From the Repo

```bash
# Clone the repo
git clone https://github.com/lastnpcalex/nrol-alpha-omega.git

# Copy the loom directory into your Claude Code canvas
cp -r nrol-alpha-omega/loom/* /path/to/your/project/canvas/

# Copy topic files
cp nrol-alpha-omega/topics/calibration-*.json /path/to/your/project/canvas/topics/

# Copy source data
cp nrol-alpha-omega/sources/source_db.json /path/to/your/project/canvas/
```

### From Scratch

1. Create a new Claude Code project with a `canvas/` directory
2. Copy `index.html`, `mirror.html`, and the `triggers/` directory
3. Create `topics/` with at least one topic JSON file (use `topics/_template.json` from the repo)
4. Create `activity-log.json` with `{"entries": []}`
5. Create `source-trust.json` with the SOURCE_TRUST dict from `framework/calibrate.py`
6. Optionally copy `source_db.json` from `sources/` for existing calibration data

The canvas entry point is `index.html`. The Loom iframe loads it automatically.

## Syncing with the Server

The Loom mirror and the `server.py` dashboard can coexist. They read the same topic JSON schema. To sync:

- **Loom → Server**: Copy `canvas/topics/*.json` and `canvas/source_db.json` back to the repo's `topics/` and `sources/` directories.
- **Server → Loom**: Copy updated topic files into `canvas/topics/`.
- **Activity log**: The `activity-log.json` is Loom-specific. It records what happened through the canvas pipeline. The server has its own execution logs.

The topic JSON schema is the contract between both interfaces. Changes to the schema in `SPEC.md` apply to both.

## Extending

### New Triggers

Add `.md` files to `canvas/triggers/`. Use `{{variable}}` placeholders. Load them with `Loom.loadTrigger('name', {vars})`.

### New Topic Types

Create a topic JSON following the schema in `SPEC.md`. Drop it in `canvas/topics/`. The triage system discovers it automatically — no code changes needed.

### Custom Lint Rules

The framework's `lint.py` failure modes can be extended. Add patterns to `FAILURE_MODES` and reference them in trigger instructions. The triggers should cite specific failure mode IDs, not general guidance.
