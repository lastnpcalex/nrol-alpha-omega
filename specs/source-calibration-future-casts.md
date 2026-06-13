# Source Calibration and Future-Cast Extensions

Status: future spec. This document describes planned additions only. It does not define current operator authority, does not change topic JSON, and does not authorize posterior movement.

## Purpose

NROL-AO already has source trust machinery, but the operator needs clearer separation between three concepts:

- Forecast calibration: Brier scoring of topic posterior trajectories against resolved outcomes.
- Source calibration: trust updates for sources based on confirmed/refuted claims.
- Hypothetical analysis: dry-run exploration of possible future events or operator actions without mutating topic state.

This spec defines future additions for source calibration and introduces two dry-run MCP workflows:

- Future cast: ask what would happen if a proposed event or hypothesis-resolution scenario occurred.
- Red-team proposal review: ask a deliberation team to critique an operator's proposed future action before any real commit.

Both workflows are advisory. They must never write to `topics/*.json`, `loom/topics/*.json`, `posteriorHistory`, `evidenceLog`, `sourceCalibration`, or `sources/source_db.json` unless a later implementation explicitly routes an operator-approved artifact into a separate future-cast database.

## Current Baseline

### Source Calibration

Current source trust is not a source-level Brier score table. It is a trust-ledger system.

The governor's evidence weight path is:

```text
effectiveWeight = claimState_weight * source_trust_factor
```

The source-trust lookup chain is:

```text
1. topic.sourceCalibration.effectiveTrust
2. sources/source_db.json domain trust for the evidence tag
3. sources/source_db.json overall trust
4. framework.calibrate.SOURCE_TRUST static prior
5. 0.50 unknown-source fallback
```

Topic-local calibration lives in `topic.sourceCalibration`:

```json
{
  "ledger": [
    {
      "timestamp": "...",
      "evidence_index": 12,
      "confirming_index": 18,
      "resolution": "CONFIRMED",
      "source": "Reuters",
      "confirming_source": "AP",
      "trust_delta": 0.05
    }
  ],
  "effectiveTrust": {
    "Reuters": 0.91
  }
}
```

Cross-topic calibration is intended to live in `sources/source_db.json` using the schema expected by `framework/source_db.py`:

```json
{
  "sources": {
    "Reuters": {
      "baseTrust": 0.9,
      "category": "wire",
      "domains": {
        "DIPLO": {
          "claims": 14,
          "confirmed": 11,
          "refuted": 3,
          "hitRate": 0.7857,
          "domainTrust": 0.87
        }
      },
      "effectiveTrust": 0.89,
      "totalClaims": 40,
      "totalConfirmed": 34,
      "totalRefuted": 6
    }
  },
  "meta": {
    "version": 1,
    "lastFullScan": "...",
    "topicsScanned": []
  }
}
```

If the cross-topic database is missing or malformed, the governor should continue using topic-local trust and static priors.

### Forecast Calibration

Brier scoring evaluates forecasts, not sources. It should remain attached to resolved topic posterior snapshots and lens/operator calibration reports.

Future work may compute source-attributed forecast error, but that must be introduced as a new analytic product. It must not be conflated with current source trust.

## Future Addition 1: Source Calibration Hardening

### Goals

- Validate `sources/source_db.json` schema before reading it.
- Provide a rebuild command that scans all eligible topic-local `sourceCalibration` ledgers and reconstructs the cross-topic database.
- Expose source calibration status through MCP and dashboards.
- Make source trust provenance visible on every evidence weight calculation.

### Proposed Commands

```text
python framework/source_db.py validate
python framework/source_db.py rebuild --topics active,resolved
python framework/source_db.py profile --source Reuters
python framework/source_db.py domains --min-claims 3
```

### Proposed MCP Tools

```text
source_calibration_status(slug: str = "")
source_profile(source: str, domain: str = "")
rebuild_source_db(dry_run: bool = true)
validate_source_db()
```

### Operator Output

The operator should be able to ask:

```text
What source trust was applied to this evidence item, and where did it come from?
```

Expected answer shape:

```json
{
  "source": "Reuters",
  "domain": "DIPLO",
  "trust": 0.9,
  "trust_source": "static_prior",
  "claim_state": "SUPPORTED",
  "claim_weight": 1.0,
  "effective_weight": 0.9
}
```

## Future Addition 2: Future Cast Mode

### Intent

Future cast mode answers:

```text
What would happen if H_i, event E, or proposal P resolved in topic X?
```

Examples:

```text
What happens to calibration-hormuz-reopen-2027 if H2 becomes impossible on 2027-04-01?
What happens if Iran and the US sign a sanctions-relief framework next week?
What happens if anti_h4_transit_recovery_toward_baseline fires with two independent refs?
```

This is a shadow analysis surface. It must not mutate the topic.

### Inputs

```json
{
  "slug": "calibration-hormuz-reopen-2027",
  "scenario": "US and Iran sign a sanctions-relief framework that explicitly commits to restoring commercial transit through Hormuz within 90 days.",
  "target": "t1_bilateral_deal",
  "proposed_transition": "FIRE",
  "observed_value": null,
  "asof": "2026-06-13",
  "assumptions": [
    "Two independent wire-service confirmations",
    "No simultaneous kinetic escalation"
  ],
  "operator_question": "Would this overcome confidence_inflation and how would H1/H2/H3/H4 move?"
}
```

### Dry-Run Workflow

1. Load topic into memory.
2. Clone it into a shadow object.
3. Convert the scenario into synthetic evidence marked `HYPOTHETICAL`.
4. Identify candidate indicators, anti-indicators, and affected hypotheses.
5. Compute a shadow posterior delta if the transition is structurally valid.
6. Run governance checks in dry-run mode.
7. Run red-team deliberation against the scenario and transition.
8. Produce an operator-facing packet with no writes to topic state.

### Required Output

```json
{
  "cast_id": "fc_20260613_001",
  "slug": "calibration-hormuz-reopen-2027",
  "status": "dry_run_only",
  "scenario_summary": "...",
  "candidate_transitions": [
    {
      "transition": "FIRE",
      "indicator_id": "t1_bilateral_deal",
      "structurally_valid": true,
      "governance": {
        "passed": false,
        "failures": ["confidence_inflation"],
        "explanation": "One ref would move H2 by more than 15pp; two independent refs required."
      },
      "shadow_posteriors": {
        "before": {"H1": 0.005, "H2": 0.005, "H3": 0.5971, "H4": 0.3929},
        "after": {"H1": 0.02, "H2": 0.34, "H3": 0.50, "H4": 0.14},
        "delta": {"H1": 0.015, "H2": 0.335, "H3": -0.0971, "H4": -0.2529}
      }
    }
  ],
  "red_team": {
    "strongest_objection": "The scenario is a diplomatic framework, not an operational reopening signal.",
    "missing_evidence": ["named signatories", "implementation date", "shipping/insurance confirmation"],
    "recommended_operator_action": "Do not FIRE until text and independent confirmation exist."
  },
  "authority": "No topic mutation. No posterior update. No source trust update."
}
```

### Storage

Future casts are not topic state. Interesting casts may be stored separately after operator request.

Proposed store:

```text
future_casts/future_casts.jsonl
```

Each row should include:

```json
{
  "cast_id": "fc_20260613_001",
  "created_at": "2026-06-13T00:00:00Z",
  "created_by": "operator",
  "slug": "calibration-hormuz-reopen-2027",
  "scenario_hash": "sha256:...",
  "scenario_summary": "...",
  "packet": {},
  "tags": ["DIPLO", "HORMUZ", "interesting"],
  "promoted_to_real_action": false,
  "promoted_proposal_id": null
}
```

No future-cast record may be treated as evidence. Promotion to real action must create a normal evidence/proposal path using real sources and existing governance gates.

### Proposed MCP Tools

```text
future_cast(
  slug: str,
  scenario: str,
  target: str = "",
  proposed_transition: str = "",
  observed_value: str = "",
  asof: str = "",
  assumptions: list[str] = [],
  save: bool = false
)

list_future_casts(slug: str = "", tag: str = "", limit: int = 25)
get_future_cast(cast_id: str)
save_future_cast(cast_id: str, tags: list[str] = [], note: str = "")
withdraw_future_cast(cast_id: str, reason: str = "")
```

## Future Addition 3: MCP Red-Team Review of Proposed Operator Actions

### Intent

The operator should be able to ask the MCP:

```text
Red-team this proposed action before I do it.
```

This is different from future cast mode. Future cast asks what a hypothetical event would do. Red-team proposal review critiques an intended operator action.

Examples:

```text
I want to commit proposal prop_123. Red-team it first.
I want to mark schema extension 4 approved. What could be wrong?
I want to FIRE t1_bilateral_deal from this article. What am I missing?
```

### Proposed MCP Tool

```text
red_team_operator_action(
  slug: str,
  action_type: str,
  action_payload: dict,
  operator_rationale: str = "",
  include_topic_excerpt: bool = true,
  save: bool = false
)
```

Supported `action_type` values:

```text
commit_match
submit_transition
apply_schema_extension_proposal
mark_schema_extension_proposal
commit_indicator_cleanup
withdraw_proposal
manual_note
```

### Deliberation Roles

- Advocate: strongest case for the operator action.
- Skeptic: strongest case that the action is invalid, premature, duplicate, overconfident, or governance-hostile.
- Judge: final recommendation with concrete blockers and fixes.

### Required Output

```json
{
  "review_id": "rt_20260613_001",
  "slug": "calibration-hormuz-reopen-2027",
  "action_type": "commit_match",
  "recommendation": "do_not_commit_yet",
  "severity": "high",
  "advocate": {
    "case": "..."
  },
  "skeptic": {
    "objections": [
      "The proposal relies on one source and would trigger confidence_inflation.",
      "The article reports talks, not a signed framework."
    ]
  },
  "judge": {
    "decision": "hold",
    "required_fixes": [
      "Find a second independent confirmation.",
      "Use OBSERVE rather than FIRE unless the agreement text is public."
    ],
    "safe_next_tool": "run_news_scan"
  },
  "authority": "Dry-run review only; no action applied."
}
```

### Storage

By default red-team reviews are transient. If `save=true`, write to a separate review store:

```text
future_casts/operator_reviews.jsonl
```

Saved reviews are audit aids. They are not evidence and must not affect topic posteriors or source trust.

## Governance Rules

All future-cast and red-team tooling must obey these rules:

- No writes to topic JSON unless the operator later initiates a normal real-action workflow.
- No writes to `posteriorHistory`.
- No writes to `evidenceLog`.
- No writes to `sourceCalibration`.
- No writes to `sources/source_db.json`.
- Synthetic evidence must be clearly labeled `HYPOTHETICAL`.
- Dry-run posteriors must be named `shadow_posteriors`, never `posteriors`.
- Saved future-cast records must live outside topic state.
- A future cast must never satisfy evidence requirements for `confidence_inflation`, source corroboration, or duplicate guards.

## Open Questions

- Should future casts use the exact `bayesian_update` code path with a no-write adapter, or a separate pure function that reproduces update math?
- Should the cast store be JSONL for reviewability or SQLite for query support?
- Should red-team review be required before high-impact operator actions, or remain optional?
- How should multi-topic future casts represent dependency propagation without implying real downstream alerts?
- Should future casts become calibratable predictions once saved, or remain purely exploratory?
