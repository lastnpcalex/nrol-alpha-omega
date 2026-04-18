# Operator Model — Possibility Cone Design Doc

**Status:** Draft, awaiting operator sign-off before build.
**Version:** 0.3 (pick-2 generators + critic Z-layers)
**Supersedes:** Earlier dichotomy-pair design (v0.2), naive fixed-position render (v0.1)

---

## 1. One-sentence pitch

A navigable visualization of the NROL-AO operator model where time flows left-to-right as a calendar cone, topics live on horizontal spines grouped into trust-based Z-bands, cross-topic dependencies render as bezier paths through Fréchet-bounded junctions, agent-derived conditional predictions sit as waypoints on parallel rails — and each prediction is rendered with critic-layer Z-planes behind it that can be navigated like pages in a book.

## 2. The metaphor

**Possibility cone.** Narrow at NOW on the left (known state), fans rightward into the space of conditional implications. Calendar time is the X-axis, literal dates. Topics that haven't resolved yet have their hypothesis clusters at NOW; downstream hypothesis nodes sit at their topic's resolution date.

**Z-bands for trust.** Three horizontal zones stacked top-to-bottom: FOREGROUND (high-trust topics, crisp), MID (moderate), BACKGROUND (low-trust, faded). Topic placement within a zone is by trust band computed from effective-weight-averaged source trust.

**Paths through junctions.** Every declared CPT edge renders as a bezier curve from the upstream topic's NOW cluster, through a junction marker at the upstream resolution date, to the downstream hypothesis node at the downstream resolution date. Path thickness is `log(1 + joint_prob * 40) * 1.6` with a hard floor (paths never disappear). Opacity is linear in joint probability (unlikely paths render faded but traceable).

**Junction markers.** Each junction shows a Fréchet-Hoeffding bracket: `[p_min | p_indep | p_max]` where width represents correlation uncertainty. A tight bracket means the joint probability is robust to correlation assumptions; a wide bracket means the number is load-bearing on an unverified assumption.

**Waypoint rails.** Each topic has a parallel rail offset above its spine where conditional predictions render as lens-colored diamonds positioned at their literal deadline. Rails never visually overlap the path.

**Critic Z-layers** (new in v0.3). Each prediction waypoint has up to 5 "critic plates" rendered behind it, one per critic in the sweep. Plates are offset-stacked (small X+Y offset per plate, like a page stack) and tinted by the critic's verdict.

## 3. Epistemic model

### 3.1 Pick-2 generators, complement-as-critics

The operator picks **any 2 of 6 ideator personas** as generators. Ideators:

- **GREEN** — midtopia / continuation
- **AMBER** — phase-shift / regime change
- **BLUE** — systemic resolution / optimist
- **RED** — tail-risk / pessimist
- **VIOLET** — actor-centric incentives
- **OCHRE** — structural determinism

The **4 personas the operator did not pick + GRAY** (universal shared-assumption skeptic) automatically become critics. The operator's choice expresses their forecasting stance; the critics represent every angle the operator did not build from.

**Examples:**
- Operator picks GREEN + BLUE (optimistic + continuation). Critics: AMBER + RED + VIOLET + OCHRE + GRAY. Every adversarial angle critiques.
- Operator picks GREEN + AMBER (dichotomy-style opposites). Critics: BLUE + RED + VIOLET + OCHRE + GRAY. Trajectory dichotomy still gets attacked from every axis it didn't cover.
- Operator picks RED + OCHRE (tail-risk + structural). Critics: GREEN + AMBER + BLUE + VIOLET + GRAY. Pessimistic-structural stance gets critiqued from optimistic and voluntarist angles.

### 3.2 Pipeline

1. **Ideation** (Haiku × 2 personas × all topics × hypotheses with posterior > 0.05). Each proposal logged to DB.
2. **Vetting** (Sonnet × per-proposal, same lens as its generator acts as first vetter): falsifiability, deadline realism, dedup, CPT alignment. Verdict: APPROVE / REJECT / MODIFY.
3. **Meta-critique** (Opus × 5, one per critic persona). Each critic sees the full candidate portfolio through their persona's lens and returns per-prediction verdicts (APPROVE / MODIFY / DROP / NEUTRAL) plus a portfolio-level narrative.
4. **Consensus rule.** A prediction is written to the topic JSON via `process_conditional_prediction()` iff:
   - Sonnet vetting APPROVE or MODIFY, AND
   - At most 1 of 5 critics DROPPED it (≥ 4/5 critics did not drop)
5. **DB logs every verdict from every critic.** Rejections preserve the CoT for audit.

### 3.3 Cost budget per sweep

- Haiku: ~56 calls (14 topics × ~4 hypotheses, ×2 generators)
- Sonnet: ~200 calls (per-proposal vetting)
- Opus: 5 calls (one per critic)
- **Total: ~$4-5 per sweep.** Abort threshold: $10.

### 3.4 Convergence signals

Three categories of prediction-level agreement, visualized distinctly:

**FULL GENERATOR CONVERGENCE** (gold halo + gold badge)
- Both operator-picked generators independently produce the same prediction
- Same event AND same probability (within 0.2)
- Strongest signal when generators are epistemic opposites (e.g. Green+Amber)

**PARTIAL GENERATOR CONVERGENCE** (silver halo + silver badge)
- Both generators flag the same event but disagree on probability (≥ 0.2 delta)
- Tooltip shows probability split: `GREEN 55% vs BLUE 80%`
- Tells the operator: "worth tracking — and the spread is itself informative"

**CRITIC CONSENSUS** (visualized via Z-plate stack — see §4.2)
- Independent of generator convergence
- Measures how many of the 5 critics APPROVED vs DROPPED

## 4. Visual language

### 4.1 Cone geometry

| Element | Encoding |
|---------|----------|
| X position | Calendar date (NOW on left, rightmost = farthest prediction deadline or topic resolution) |
| Y position (across whole viewport) | Z-band: FOREGROUND (top 35%), MID (middle 40%), BACKGROUND (bottom 25%) |
| Y position (within zone) | One horizontal lane per topic, evenly spread in the zone |
| Path thickness | `max(0.8, log2(1 + joint_prob * 40) * 1.6)` — log-scaled, hard floor |
| Path opacity | `max(0.06, min(0.85, joint_prob * 2.8))` — linear in joint probability |
| Cone gradient | Subtle amber wash expanding rightward — purely aesthetic, no data encoding |

### 4.2 Critic Z-plates (NEW in v0.3)

Behind every prediction waypoint, render up to 5 small plates in canonical order (critic persona alphabetical: AMBER, BLUE, GRAY, GREEN, OCHRE, RED, VIOLET — whichever 5 are active for this sweep).

Plate geometry:
- ~10×6px translucent rectangles
- Stacked with progressive offset: each plate `(+2px X, +2px Y)` from previous
- Topmost plate sits directly behind the prediction diamond

Plate color by verdict:
| Verdict | Plate color |
|---------|-------------|
| APPROVE | Persona color at full saturation |
| MODIFY | Amber (`#f59e0b`) |
| DROP | Dim gray (`#475569`) at 30% opacity |
| NEUTRAL / no verdict | Invisible (no plate drawn) |

**Visual patterns:**
- All 5 APPROVE → five colored plates visibly stacked → "deep, robust"
- All 5 DROP → five dim gray plates → "ghost stack, adversarially fragile"
- Mixed → uneven color pattern → "contested, with specific vulnerabilities named by which critic dropped it"

### 4.3 Critic-layer navigation

**Critic strip** above or below the viewport: row of persona chips for the 5 active critics + an ALL chip. Chip interaction:

| State | Behavior |
|-------|----------|
| ALL (default) | Full Z-plate stack renders behind every prediction. Diamonds render with full aggregate visual. |
| Single critic focused | That critic's plate becomes dominant (brought to front, full opacity, larger). Other critics' plates recede (+2px extra offset each, opacity halved). Diamonds re-color based on focused critic's verdict: APPROVE = full persona color, MODIFY = amber outline, DROP = gray translucent, NEUTRAL = dimmed unchanged. |

Chip navigation:
- Tap chip (mobile + desktop)
- Arrow keys ←/→ cycle through critics (desktop)
- Active chip highlights with its persona color
- Small text indicator near chip: `Viewing: RED layer — tail-risk critique`

**Preserved across focus states:** Generator convergence halos (gold/silver) remain visible but slightly muted so the critic layer is the dominant signal.

### 4.4 Other visual elements (preserved from v0.2)

- NOW line: vertical amber dashed line at `dateToX(today)`, labeled "NOW"
- Date ticks: monthly along bottom edge
- Zone dividers: horizontal dashed lines between FOREGROUND/MID/BACKGROUND
- Topic labels: left-aligned to the right of the NOW line cluster
- Resolution date markers: small amber diamond with date label at each topic's resolution X
- Lens chip strip: toggleable filters for prediction visibility per lens

## 5. Update modal

Replace current 3-card dichotomy modal with persona-picker:

```
┌──────────────────────────────────────────────────────────┐
│ WHAT LENSES DO YOU BELIEVE IN?                            │
│                                                            │
│ Pick 2 generators. The remaining 4 + GRAY critique.       │
│                                                            │
│ ┌────────┐ ┌────────┐ ┌────────┐                          │
│ │ GREEN  │ │ AMBER  │ │ BLUE   │                          │
│ │ cont.  │ │ shift  │ │ resol. │                          │
│ └────────┘ └────────┘ └────────┘                          │
│ ┌────────┐ ┌────────┐ ┌────────┐                          │
│ │ RED    │ │ VIOLET │ │ OCHRE  │                          │
│ │ tail   │ │ actors │ │ struct │                          │
│ └────────┘ └────────┘ └────────┘                          │
│                                                            │
│ Selected: GREEN + AMBER (trajectory opposites)            │
│ Critics:  BLUE · RED · VIOLET · OCHRE · GRAY              │
│                                                            │
│ ⚠ Est. $4-5 · ~2-3 min · 5 Opus meta-critiques            │
│                                                            │
│           [ Cancel ]         [ Run Sweep ]                │
└──────────────────────────────────────────────────────────┘
```

**Contextual hint below selection:**
- If generators are opposites (Green+Amber, Blue+Red, Violet+Ochre): "trajectory opposites — convergence carries extra weight"
- If generators are aligned (Green+Blue, Red+Amber, Green+Violet): "aligned stance — strong adversarial pressure from the opposing dichotomy"
- Otherwise: no hint

Run Sweep button disabled unless exactly 2 are picked.

## 6. Data model

### 6.1 extrapolation_db schema additions

Existing tables unchanged. Add to `vetting` table semantics: `persona` column now distinguishes generator-self-vetting (persona = generator lens) vs critic vetting (persona = critic lens).

Add new column to `meta_lint` table:
- `critic_persona` (existing) now takes values from the full set, not just GRAY
- One row per critic per run (instead of one row per run as before)

Add `critic_verdicts` table for per-prediction, per-critic verdicts:
```sql
CREATE TABLE critic_verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES agent_runs(id),
  ideation_id INTEGER NOT NULL REFERENCES ideations(id),
  critic_persona TEXT NOT NULL,  -- AMBER | BLUE | GRAY | OCHRE | RED | VIOLET
  verdict TEXT NOT NULL,         -- APPROVE | MODIFY | DROP | NEUTRAL
  reasoning TEXT,
  vetted_at TEXT NOT NULL
);
```

### 6.2 Topic JSON `conditionalPredictions[]` additions

Add to existing schema:
```json
{
  "id": "cp_123",
  "lens": "GREEN",
  "conditionalProbability": 0.75,
  "...": "existing fields",
  "criticVerdicts": {
    "AMBER": {"verdict": "APPROVE", "reasoning": "..."},
    "BLUE": {"verdict": "APPROVE", "reasoning": "..."},
    "RED": {"verdict": "DROP", "reasoning": "..."},
    "VIOLET": {"verdict": "MODIFY", "reasoning": "..."},
    "OCHRE": {"verdict": "APPROVE", "reasoning": "..."},
    "GRAY": {"verdict": "APPROVE", "reasoning": "..."}
  },
  "lensAgreement": ["GREEN", "AMBER"]  // if generator convergence, list of lenses that converged
}
```

### 6.3 Write path

`process_conditional_prediction()` gains optional `critic_verdicts` and `lens_agreement` parameters. When the sweep writes a prediction, it includes the full critic verdict matrix. Existing HUMAN-authored predictions have empty `criticVerdicts: {}`.

## 7. Build scope

### Files to modify

| File | Change |
|------|--------|
| `canvas/model.html` | Replace dichotomy modal with persona-picker. Add critic layer strip. Render critic Z-plates behind waypoints. Implement focus-state re-rendering. |
| `temp-repo/skills/extrapolate.md` | Replace dichotomy workflow with pick-2-critics-complement. Document 5-critic Opus pass. Update consensus rule. |
| `temp-repo/skills/extrapolation-tuning.md` | Clarify ideator vs critic roles. No persona prompt changes (prompts remain valid). |
| `temp-repo/framework/extrapolation_db.py` | Add `critic_verdicts` table. Add `log_critic_verdict()` function. |
| `temp-repo/framework/scoring.py` | Update `add_conditional_prediction()` to accept `critic_verdicts` dict and `lens_agreement` list. |
| `temp-repo/framework/pipeline.py` | Update `process_conditional_prediction()` signature to forward the new fields. |

### Files NOT modified

- `.claude/commands/extrapolate.md` — permission-blocked, existing stub already passes through `$ARGUMENTS`.
- Any topic JSONs — existing predictions continue to work (empty `criticVerdicts: {}` is a valid state).
- Any other canvas pages — scope is isolated to `model.html`.

### Backwards compatibility

Existing predictions written before v0.3 have no `criticVerdicts` field. Render them as if all critic verdicts were NEUTRAL → no plates drawn. Diamond renders normally. No migration needed.

## 8. Open questions

- **Topic-level critique.** Do critics comment on topics as wholes or only per-prediction? Gray currently produces a portfolio-level narrative. Other critics should too — but do we surface the narrative anywhere, or only the per-prediction verdicts? **Tentative:** portfolio narratives stored in `portfolio_snapshots.critic_narrative`, visible in a future "portfolio view" panel.

- **Critic verdict UI for operator overrides.** Can the operator manually override a critic's verdict ("the system dropped this, I disagree, keep it")? If yes, needs an override trail. **Tentative:** defer to v0.4, not v0.3.

- **Sweep history.** Multiple sweeps overlay their predictions. How to distinguish "today's sweep" vs "last week's"? **Tentative:** defer — existing lens filter + agent_run_id tagging is enough for now, explicit history selector is future work.

- **Critic calibration.** Do we track whether a critic's DROP on a prediction was correct (i.e., the prediction failed) vs wrong (the prediction was correct)? **Tentative:** yes, compute per-critic Brier scores at resolution time. Separate column in calibration reports.

## 9. Not in scope for this rebuild

- Automated topic creation / refinement
- Cross-sweep convergence (comparing two different sweep runs' portfolios)
- Exporting portfolio to external formats
- Real-time sweep progress streaming in the UI (current plan: modal says "sweep started, canvas refreshes when done")
- Mobile-specific critic layer gestures (current plan: tap-only)

## 10. Success criteria

A sweep completes end-to-end and the canvas shows:
1. New prediction waypoints at the right calendar X positions
2. Generator convergence halos (gold/silver) where lenses converged
3. Critic Z-plates stacked behind every prediction, color-coded by verdict
4. Critic chip strip lets the operator navigate into each critic's layer
5. Tooltip on any prediction shows: prediction text, probability, generator lens, convergence state, critic verdicts summary, per-critic reasoning on hover

If the above renders correctly after a first TRAJECTORY sweep with 2 generators and 5 critics, v0.3 ships.
