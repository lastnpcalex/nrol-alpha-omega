"""
NRL-Alpha Omega — Generalized Epistemic Bayesian Estimator Engine

Core engine for loading, updating, and managing topic state files.
Governor integration: epistemic governance is a hard gate on mutations,
not an optional lint pass. The governor vets both data and thinking.

No external dependencies — Python stdlib + governor.py only.
"""

import json
import os
import copy
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from governor import (
    check_update_proposal,
    classify_evidence,
    assess_claim_state,
    get_effective_weight,
    validate_hypotheses,
    compute_topic_rt,
    compute_entropy,
    compute_max_entropy,
    compute_uncertainty_ratio,
    audit_evidence_freshness,
)

TOPICS_DIR = Path(__file__).parent / "topics"
BRIEFS_DIR = Path(__file__).parent / "briefs"
DASHBOARDS_DIR = Path(__file__).parent / "dashboards"


# ---------------------------------------------------------------------------
# Governance Exception
# ---------------------------------------------------------------------------

class GovernanceError(Exception):
    """Raised when a governor check blocks an operation."""
    def __init__(self, message: str, failures: list = None, warnings: list = None):
        super().__init__(message)
        self.failures = failures or []
        self.warnings = warnings or []


# ---------------------------------------------------------------------------
# Topic I/O
# ---------------------------------------------------------------------------

def load_topic(slug: str) -> dict:
    """Load a topic state file. Raises if malformed."""
    path = TOPICS_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"Topic not found: {slug}")
    with open(path, "r", encoding="utf-8") as f:
        topic = json.load(f)
    validate_topic(topic)
    return topic


def save_topic(topic: dict) -> None:
    """Write topic state back to disk with embedded governance snapshot."""
    slug = topic["meta"]["slug"]
    topic["meta"]["lastUpdated"] = _now_iso()

    # Guard: enrich any evidence entries that bypassed the governor
    _GOVERNOR_FIELDS = ("ledger", "claimState", "effectiveWeight")
    evidence_log = topic.get("evidenceLog", [])
    enriched_count = 0
    for entry in evidence_log:
        if any(field not in entry for field in _GOVERNOR_FIELDS):
            entry["ledger"] = entry.get("ledger") or classify_evidence(entry)
            entry["claimState"] = entry.get("claimState") or assess_claim_state(
                entry, evidence_log
            )
            entry["effectiveWeight"] = entry.get("effectiveWeight") or get_effective_weight(
                entry, evidence_log
            )
            enriched_count += 1
    if enriched_count:
        warnings.warn(
            f"Governor guard: {enriched_count} evidence entries were missing "
            f"governor fields — enriched retroactively on save. "
            f"Use engine.add_evidence() to avoid this.",
            stacklevel=2,
        )

    # Compute and embed governance snapshot
    try:
        rt = compute_topic_rt(topic)
        entropy = compute_entropy(topic)
        max_entropy = compute_max_entropy(topic)
        uncertainty = compute_uncertainty_ratio(topic)
        freshness = audit_evidence_freshness(topic)
        admissibility = validate_hypotheses(topic)

        # Count issues (mirrors governance_report logic)
        issues = []
        if rt["regime"] in ("DANGEROUS", "RUNAWAY"):
            issues.append(f"R_t in {rt['regime']} — needs fresh evidence")
        if freshness["stale"] > freshness["fresh"]:
            issues.append(f"Majority stale evidence ({freshness['stale']}/{freshness['total']})")
        inadmissible = [k for k, v in admissibility.items() if v["grade"] == "INADMISSIBLE"]
        if inadmissible:
            issues.append(f"Inadmissible hypotheses: {', '.join(inadmissible)}")
        unfalsifiable = [k for k, v in admissibility.items() if v["falsifiability"] == "NO"]
        if unfalsifiable:
            issues.append(f"Unfalsifiable hypotheses: {', '.join(unfalsifiable)}")
        if uncertainty > 0.9:
            issues.append("Near-maximum uncertainty — model is not discriminating")
        elif uncertainty < 0.1:
            issues.append("Near-zero uncertainty — check for overconfidence")

        health = "HEALTHY" if len(issues) == 0 else "DEGRADED" if len(issues) <= 2 else "CRITICAL"

        topic["governance"] = {
            "health": health,
            "issues": issues,
            "rt": {
                "rt": rt["rt"],
                "regime": rt["regime"],
                "worst_hypothesis": rt.get("worst_hypothesis"),
            },
            "entropy": entropy,
            "maxEntropy": max_entropy,
            "uncertaintyRatio": uncertainty,
            "evidenceFreshness": {
                "fresh": freshness["fresh"],
                "stale": freshness["stale"],
                "total": freshness["total"],
            },
            "hypothesisAdmissibility": {
                k: v["grade"] for k, v in admissibility.items()
            },
            "lastComputed": _now_iso(),
        }
    except Exception:
        # Governor computation must never prevent saving state
        pass

    path = TOPICS_DIR / f"{slug}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topic, f, indent=2, ensure_ascii=False)


def list_topics() -> list[dict]:
    """Return list of {slug, title, status, classification} for all topics."""
    results = []
    if not TOPICS_DIR.exists():
        return results
    for p in sorted(TOPICS_DIR.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                t = json.load(f)
            meta = t.get("meta", {})
            gov = t.get("governance", {})
            results.append({
                "slug": meta.get("slug", p.stem),
                "title": meta.get("title", p.stem),
                "status": meta.get("status", "UNKNOWN"),
                "classification": meta.get("classification", "ROUTINE"),
                "question": meta.get("question", ""),
                "lastUpdated": meta.get("lastUpdated", ""),
                "governanceHealth": gov.get("health") if gov else None,
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def create_topic(config: dict) -> dict:
    """
    Create a new topic from a config dict. Returns the initialized state.

    Governor gate: INADMISSIBLE hypotheses block creation.
    MARGINAL hypotheses generate a warning in the evidence log.
    """
    template_path = TOPICS_DIR / "_template.json"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            topic = json.load(f)
    else:
        topic = _empty_topic()

    # Remove protocol notes from template
    topic.pop("_protocol", None)
    if "indicators" in topic:
        topic["indicators"].pop("_design_rules", None)

    # Overlay config onto template
    _deep_merge(topic, config)

    # Set defaults
    now = _now_iso()
    topic.setdefault("meta", {})
    topic["meta"].setdefault("created", now)
    topic["meta"].setdefault("lastUpdated", now)
    topic["meta"].setdefault("status", "ACTIVE")
    topic["meta"].setdefault("dayCount", 0)
    topic["meta"].setdefault("classification", "ROUTINE")

    # Structural validation
    validate_topic(topic)

    # Governor hard gate: admissibility
    admissibility = validate_hypotheses(topic)
    inadmissible = {k: v for k, v in admissibility.items()
                    if v["grade"] == "INADMISSIBLE"}
    marginal = {k: v for k, v in admissibility.items()
                if v["grade"] == "MARGINAL"}

    if inadmissible:
        details = "; ".join(
            f"{k}: {v['passed']}/{v['total']} checks "
            f"(clarity={v['setpoint_clarity']}, "
            f"observable={v['observability']}, "
            f"falsifiable={v['falsifiability']})"
            for k, v in inadmissible.items()
        )
        raise GovernanceError(
            f"Cannot create topic: INADMISSIBLE hypotheses — {details}",
            failures=list(inadmissible.keys()),
        )

    if marginal:
        for k, v in marginal.items():
            failed_checks = [c for c, passed in v["checks"].items() if not passed]
            topic.setdefault("evidenceLog", []).append({
                "time": _now_iso(),
                "tag": "INTEL",
                "text": (f"GOVERNANCE: Hypothesis {k} is MARGINAL at creation "
                         f"(failed: {', '.join(failed_checks)})"),
                "provenance": "DERIVED",
                "posteriorImpact": "NONE",
                "ledger": "DECISION",
                "claimState": "PROPOSED",
                "effectiveWeight": 0.5,
            })

    save_topic(topic)
    return topic


def scaffold_topic(slug: str) -> str:
    """
    Create a new topic scaffold from template. Does NOT validate — the user
    needs to fill in the blanks first. Returns the file path.
    """
    template_path = TOPICS_DIR / "_template.json"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            topic = json.load(f)
    else:
        topic = _empty_topic()

    # Set the slug and timestamps
    now = _now_iso()
    topic["meta"]["slug"] = slug
    topic["meta"]["created"] = now
    topic["meta"]["lastUpdated"] = now
    topic["meta"]["startDate"] = now

    # Write initial prior to history
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    if hypotheses:
        history_entry = {"date": now[:10], "note": "Prior (uniform — fill in domain knowledge)"}
        for k, h in hypotheses.items():
            history_entry[k] = h["posterior"]
        topic["model"]["posteriorHistory"] = [history_entry]

    # Save without validation (user needs to fill in)
    path = TOPICS_DIR / f"{slug}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topic, f, indent=2, ensure_ascii=False)

    return str(path)


# ---------------------------------------------------------------------------
# Bayesian Updates
# ---------------------------------------------------------------------------

def update_posteriors(topic: dict, new_posteriors: dict[str, float],
                      reason: str, evidence_refs: list[str] = None) -> dict:
    """
    Apply a posterior update with governor pre-commit gate.

    new_posteriors: {"H1": 0.30, "H2": 0.40, ...}
    reason: why the update happened
    evidence_refs: list of evidence log timestamps/IDs supporting this update

    Governor gate: runs hallucination failure mode checklist before applying.
    CRITICAL failures block the update (GovernanceError).
    Warnings are logged to the evidence log but the update proceeds.
    """
    hypotheses = topic["model"]["hypotheses"]

    # Validate all keys exist
    for k in new_posteriors:
        if k not in hypotheses:
            raise ValueError(f"Unknown hypothesis: {k}")

    # Build merged posteriors (keep old values for keys not in update)
    merged = {}
    for k in hypotheses:
        merged[k] = new_posteriors.get(k, hypotheses[k]["posterior"])

    # Enforce sum-to-1
    total = sum(merged.values())
    if abs(total - 1.0) > 0.01:
        raise ValueError(f"Posteriors sum to {total:.4f}, must be ~1.0")

    # Normalize to exactly 1.0
    for k in merged:
        merged[k] = round(merged[k] / total, 4)

    # === GOVERNOR HARD GATE: Hallucination Checklist ===
    proposal_check = check_update_proposal(
        topic, merged, evidence_refs=evidence_refs, reason=reason
    )

    if not proposal_check["passed"]:
        raise GovernanceError(
            f"Posterior update blocked by governance: "
            f"{', '.join(proposal_check['failures'])}",
            failures=proposal_check["failures"],
            warnings=proposal_check["warnings"],
        )

    # Non-critical warnings: log them but proceed
    if proposal_check["warnings"]:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"GOVERNANCE WARNING on posterior update: "
                     f"{', '.join(proposal_check['warnings'])}. "
                     f"Reason: {reason}"),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "PROPOSED",
            "effectiveWeight": 0.5,
        })

    # Belt-and-suspenders: original confidence gate
    max_shift = max(abs(merged[k] - hypotheses[k]["posterior"]) for k in merged)
    if max_shift > 0.10 and evidence_refs is None:
        raise ValueError(
            f"Major posterior shift ({max_shift:.0%}) requires evidence_refs. "
            "Provide references to supporting evidence entries."
        )

    # Apply
    for k in merged:
        hypotheses[k]["posterior"] = merged[k]

    # Recompute expected value
    topic["model"]["expectedValue"] = round(
        sum(h["midpoint"] * h["posterior"] for h in hypotheses.values()), 2
    )

    # Append to history
    history_entry = {"date": _now_iso()[:10]}
    for k in hypotheses:
        history_entry[k] = hypotheses[k]["posterior"]
    history_entry["note"] = reason
    topic["model"].setdefault("posteriorHistory", []).append(history_entry)

    return topic


def hold_posteriors(topic: dict, reason: str = "No new indicators") -> dict:
    """Record that posteriors were reviewed but not changed."""
    add_evidence(topic, {
        "tag": "INTEL",
        "text": f"Posteriors HELD: {reason}",
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
    })
    return topic


def update_submodel(topic: dict, submodel_id: str,
                    new_probs: dict[str, float], reason: str,
                    evidence_refs: list[str] = None) -> dict:
    """
    Update sub-model scenario probabilities with governor gate.

    submodel_id: key in topic["subModels"] (e.g. "meuMission")
    new_probs: {"kharg": 0.60, "declareVictory": 0.12, ...}
    reason: why the update happened
    evidence_refs: list of evidence timestamps supporting this update

    Governor gate: runs hallucination checklist (same as posterior updates).
    CRITICAL failures block the update. Warnings are logged.
    Evidence trail is always recorded.
    """
    submodels = topic.get("subModels", {})
    if submodel_id not in submodels:
        raise ValueError(f"Unknown sub-model: {submodel_id}")

    sm = submodels[submodel_id]
    scenarios = sm.get("scenarios", {})

    # Validate all keys exist
    for k in new_probs:
        if k not in scenarios:
            raise ValueError(f"Unknown scenario in {submodel_id}: {k}")

    # Build merged probabilities
    merged = {}
    for k in scenarios:
        merged[k] = new_probs.get(k, scenarios[k]["prob"])

    # Enforce sum-to-1
    total = sum(merged.values())
    if abs(total - 1.0) > 0.01:
        raise ValueError(f"Sub-model probs sum to {total:.4f}, must be ~1.0")

    # Normalize
    for k in merged:
        merged[k] = round(merged[k] / total, 4)

    # === GOVERNOR HARD GATE ===
    # Use the same hallucination checklist as posteriors — sub-model shifts
    # are posterior shifts on a nested question, same epistemic discipline applies.
    # Pass current posteriors unchanged so governor checks run without
    # flagging a phantom shift on the main model.
    current_posteriors = {
        k: v["posterior"] for k, v in topic["model"]["hypotheses"].items()
    }
    proposal_check = check_update_proposal(
        topic, current_posteriors, evidence_refs=evidence_refs, reason=reason
    )

    if not proposal_check["passed"]:
        raise GovernanceError(
            f"Sub-model update ({submodel_id}) blocked by governance: "
            f"{', '.join(proposal_check['failures'])}",
            failures=proposal_check["failures"],
            warnings=proposal_check["warnings"],
        )

    # Non-critical warnings: log them
    if proposal_check["warnings"]:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"GOVERNANCE WARNING on sub-model update ({submodel_id}): "
                     f"{', '.join(proposal_check['warnings'])}. "
                     f"Reason: {reason}"),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "PROPOSED",
            "effectiveWeight": 0.5,
        })

    # Require evidence refs for large shifts
    max_shift = max(abs(merged[k] - scenarios[k]["prob"]) for k in merged)
    if max_shift > 0.10 and evidence_refs is None:
        raise ValueError(
            f"Major sub-model shift ({max_shift:.0%}) requires evidence_refs."
        )

    # Record old values for audit trail
    old_probs = {k: scenarios[k]["prob"] for k in scenarios}

    # Apply
    for k in merged:
        scenarios[k]["prob"] = merged[k]

    # Log the update as governor-enriched evidence
    shifts = []
    for k in merged:
        delta = merged[k] - old_probs[k]
        if abs(delta) >= 0.005:
            shifts.append(f"{k}: {old_probs[k]:.0%}->{merged[k]:.0%}")
    shift_str = ", ".join(shifts) if shifts else "no change"

    add_evidence(topic, {
        "tag": "INTEL",
        "text": (f"SUB-MODEL UPDATE ({submodel_id}): {shift_str}. "
                 f"Reason: {reason}"),
        "provenance": "DERIVED",
        "posteriorImpact": "MODERATE",
    })

    return topic


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def fire_indicator(topic: dict, indicator_id: str,
                   note: str = None, tier: str = None) -> dict:
    """
    Mark an indicator as FIRED. Auto-detects tier if not specified.
    Returns the topic with updated indicator and suggested posterior effect.
    """
    found = False
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            if ind["id"] == indicator_id:
                ind["status"] = "FIRED"
                ind["firedDate"] = _now_iso()
                if note:
                    ind["note"] = note
                found = True
                tier = tier or tier_key
                break
        if found:
            break

    if not found:
        raise ValueError(f"Indicator not found: {indicator_id}")

    # Update classification
    topic["meta"]["classification"] = compute_classification(topic)

    return topic


def partial_indicator(topic: dict, indicator_id: str, note: str) -> dict:
    """Mark an indicator as PARTIAL (partially confirmed)."""
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            if ind["id"] == indicator_id:
                ind["status"] = "PARTIAL"
                if note:
                    ind["note"] = note
                topic["meta"]["classification"] = compute_classification(topic)
                return topic
    raise ValueError(f"Indicator not found: {indicator_id}")


def compute_classification(topic: dict) -> str:
    """Determine ROUTINE/ELEVATED/ALERT from indicator state."""
    tiers = topic["indicators"]["tiers"]

    # Tier 1 FIRED → ALERT
    for ind in tiers.get("tier1_critical", []):
        if ind["status"] == "FIRED":
            return "ALERT"

    # Tier 1 PARTIAL or Tier 2 FIRED → ELEVATED
    for ind in tiers.get("tier1_critical", []):
        if ind["status"] == "PARTIAL":
            return "ELEVATED"
    for ind in tiers.get("tier2_strong", []):
        if ind["status"] == "FIRED":
            return "ELEVATED"

    return "ROUTINE"


def get_indicator_summary(topic: dict) -> dict:
    """Return counts of fired/partial/not_fired per tier."""
    summary = {}
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        counts = {"FIRED": 0, "PARTIAL": 0, "NOT_FIRED": 0}
        for ind in indicators:
            counts[ind.get("status", "NOT_FIRED")] += 1
        summary[tier_key] = counts
    return summary


# ---------------------------------------------------------------------------
# Evidence Log
# ---------------------------------------------------------------------------

def add_evidence(topic: dict, entry: dict) -> dict:
    """
    Add an evidence entry to the log with governor enrichment.

    Required fields: tag, text
    Optional: provenance, source, posteriorImpact

    Governor enrichment (auto-computed, caller can override):
      - ledger: FACT or DECISION (from tag classification)
      - claimState: PROPOSED/SUPPORTED/CONTESTED/INVALIDATED
      - effectiveWeight: 0.0-1.0 (from claim state)
    """
    if "tag" not in entry or "text" not in entry:
        raise ValueError("Evidence entry requires 'tag' and 'text'")

    full_entry = {
        "time": _now_iso(),
        "tag": entry["tag"],
        "text": entry["text"],
        "provenance": entry.get("provenance", "OBSERVED"),
        "source": entry.get("source"),
        "posteriorImpact": entry.get("posteriorImpact", "NONE"),
    }

    # Deduplication: don't add if identical text exists in last 10 entries
    recent = topic.get("evidenceLog", [])[-10:]
    for existing in recent:
        if existing.get("text") == full_entry["text"]:
            return topic  # Skip duplicate

    # Governor enrichment: classify and weight the evidence
    evidence_log = topic.get("evidenceLog", [])
    full_entry["ledger"] = entry.get("ledger") or classify_evidence(full_entry)
    full_entry["claimState"] = entry.get("claimState") or assess_claim_state(
        full_entry, evidence_log
    )
    full_entry["effectiveWeight"] = entry.get("effectiveWeight") or get_effective_weight(
        full_entry, evidence_log
    )

    topic.setdefault("evidenceLog", []).append(full_entry)
    return topic


def _add_evidence_raw(topic: dict, entry: dict) -> dict:
    """
    Append a pre-built evidence entry directly (no enrichment, no dedup).
    Used internally by governance warnings to avoid recursion.
    """
    topic.setdefault("evidenceLog", []).append(entry)
    return topic


# ---------------------------------------------------------------------------
# Data Feeds
# ---------------------------------------------------------------------------

def update_feed(topic: dict, feed_id: str, value, as_of: str = None) -> dict:
    """Update a data feed value."""
    if feed_id not in topic.get("dataFeeds", {}):
        raise ValueError(f"Unknown feed: {feed_id}")
    topic["dataFeeds"][feed_id]["value"] = value
    topic["dataFeeds"][feed_id]["asOf"] = as_of or _now_iso()
    return topic


# ---------------------------------------------------------------------------
# Briefing Generation
# ---------------------------------------------------------------------------

def generate_brief(topic: dict, mode: str = "routine",
                   developments: list[str] = None) -> str:
    """Generate a structured briefing markdown string with governance health."""
    meta = topic["meta"]
    model = topic["model"]
    hypotheses = model["hypotheses"]

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H%M UTC")

    # Header
    lines = [
        f"# {meta['title']} BRIEF — {timestamp}",
        f"## Classification: {meta['classification']}",
        f"## Day {meta.get('dayCount', '?')} since tracking began",
        "",
    ]

    # Developments
    lines.append("### BREAKING / NEW DEVELOPMENTS")
    if developments:
        for d in developments:
            lines.append(f"- {d}")
    else:
        recent = topic.get("evidenceLog", [])[-10:]
        if recent:
            for e in reversed(recent):
                lines.append(f"- **[{e['tag']}]** {e['text']}")
        else:
            lines.append("- No new developments.")
    lines.append("")

    # Indicator status
    lines.append("### INDICATOR STATUS")
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        fired = [i for i in indicators if i["status"] in ("FIRED", "PARTIAL")]
        if fired:
            tier_label = tier_key.upper().replace("_", " ")
            for ind in fired:
                lines.append(f"- **{tier_label}** [{ind['status']}] {ind['desc']}")
                if ind.get("note"):
                    lines.append(f"  - {ind['note']}")
    if not any(i["status"] != "NOT_FIRED"
               for inds in topic["indicators"]["tiers"].values()
               for i in inds):
        lines.append("- All indicators quiet.")
    lines.append("")

    # Data feeds
    if topic.get("dataFeeds"):
        lines.append("### DATA FEEDS")
        for fid, feed in topic["dataFeeds"].items():
            baseline_note = ""
            if feed.get("baseline") and feed.get("value"):
                try:
                    pct = ((feed["value"] - feed["baseline"]) / feed["baseline"]) * 100
                    direction = "+" if pct > 0 else ""
                    baseline_note = f" ({direction}{pct:.1f}% vs baseline)"
                except (TypeError, ZeroDivisionError):
                    pass
            lines.append(
                f"- **{feed['label']}**: {feed['value']} {feed.get('unit', '')}"
                f"{baseline_note}"
            )
        lines.append("")

    # Posteriors
    lines.append("### POSTERIORS")
    parts = []
    for k, h in hypotheses.items():
        parts.append(f"{k}={h['posterior']:.0%}")
    ev = model.get("expectedValue", "?")
    eu = model.get("expectedUnit", "")
    lines.append(" | ".join(parts) + f" | E[{eu}]={ev}")

    # Check if posteriors changed in this session
    history = model.get("posteriorHistory", [])
    if len(history) >= 2:
        prev = history[-2]
        curr = history[-1]
        changed = any(abs(curr.get(k, 0) - prev.get(k, 0)) > 0.005
                       for k in hypotheses)
        if changed:
            lines.append(f"**UPDATED** — {curr.get('note', 'see evidence log')}")
        else:
            lines.append("**HELD** — no new indicators")
    else:
        lines.append("**HELD** — no new indicators")
    lines.append("")

    # Epistemic Health (from embedded governance snapshot)
    gov = topic.get("governance")
    if gov:
        lines.append("### EPISTEMIC HEALTH")
        rt = gov.get("rt", {})
        lines.append(
            f"- **Status**: {gov.get('health', '?')} | "
            f"R_t={rt.get('rt', '?'):.2f} ({rt.get('regime', '?')}) | "
            f"Entropy={gov.get('entropy', 0):.2f}/{gov.get('maxEntropy', 0):.2f} "
            f"({gov.get('uncertaintyRatio', 0):.0%} uncertainty)"
        )
        fresh = gov.get("evidenceFreshness", {})
        if fresh:
            lines.append(
                f"- **Evidence**: {fresh.get('fresh', '?')} fresh / "
                f"{fresh.get('stale', '?')} stale / "
                f"{fresh.get('total', '?')} total"
            )
        admissibility = gov.get("hypothesisAdmissibility", {})
        inadmissible_h = [k for k, v in admissibility.items() if v == "INADMISSIBLE"]
        marginal_h = [k for k, v in admissibility.items() if v == "MARGINAL"]
        if inadmissible_h:
            lines.append(f"- **WARNING**: Inadmissible hypotheses: {', '.join(inadmissible_h)}")
        if marginal_h:
            lines.append(f"- **NOTE**: Marginal hypotheses: {', '.join(marginal_h)}")
        gov_issues = gov.get("issues", [])
        if gov_issues:
            for issue in gov_issues:
                lines.append(f"- {issue}")
        lines.append("")

    # Sub-models
    if topic.get("subModels"):
        lines.append("### SUB-MODELS")
        for sm_key, sm in topic["subModels"].items():
            if "scenarios" in sm:
                lines.append(f"**{sm_key}**:")
                for sk, sv in sm["scenarios"].items():
                    lines.append(f"  - {sv.get('label', sk)}: {sv.get('prob', '?'):.0%}")
        lines.append("")

    # Watchpoints
    if topic.get("watchpoints"):
        lines.append("### KEY WATCHPOINTS NEXT 12-24H")
        for wp in topic["watchpoints"]:
            lines.append(f"- **{wp['time']}** — {wp['event']}")
            if wp.get("watch"):
                lines.append(f"  - {wp['watch']}")
        lines.append("")

    return "\n".join(lines)


def save_brief(topic: dict, brief_text: str) -> str:
    """Save a briefing to disk. Returns the file path."""
    slug = topic["meta"]["slug"]
    brief_dir = BRIEFS_DIR / slug
    brief_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y-%m-%d-%H%M") + ".md"
    path = brief_dir / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(brief_text)
    return str(path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_topic(topic: dict) -> dict:
    """
    Validate a topic state file.
    Raises ValueError on structural issues.
    Returns admissibility report for informational use.
    """
    if "meta" not in topic:
        raise ValueError("Topic missing 'meta' section")
    if "model" not in topic:
        raise ValueError("Topic missing 'model' section")
    if "hypotheses" not in topic["model"]:
        raise ValueError("Topic missing 'model.hypotheses'")
    if "indicators" not in topic:
        raise ValueError("Topic missing 'indicators' section")

    meta = topic["meta"]
    for field in ("slug", "title", "question", "resolution"):
        if field not in meta:
            raise ValueError(f"Topic meta missing '{field}'")

    # Validate posteriors sum to ~1.0
    hypotheses = topic["model"]["hypotheses"]
    total = sum(h["posterior"] for h in hypotheses.values())
    if abs(total - 1.0) > 0.02:
        raise ValueError(f"Posteriors sum to {total:.4f}, expected ~1.0")

    # Governor: admissibility check (non-blocking on load)
    admissibility = validate_hypotheses(topic)
    inadmissible = [k for k, v in admissibility.items()
                    if v["grade"] == "INADMISSIBLE"]
    if inadmissible:
        warnings.warn(
            f"Topic '{meta.get('slug', '?')}' has INADMISSIBLE hypotheses: "
            f"{', '.join(inadmissible)}. Consider revising.",
            stacklevel=2,
        )

    return admissibility


# ---------------------------------------------------------------------------
# Day Count
# ---------------------------------------------------------------------------

def update_day_count(topic: dict) -> dict:
    """Update the dayCount based on startDate."""
    start = topic["meta"].get("startDate")
    if start:
        start_date = datetime.fromisoformat(start).date()
        today = datetime.now(timezone.utc).date()
        topic["meta"]["dayCount"] = (today - start_date).days
    return topic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base, modifying base in place."""
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _empty_topic() -> dict:
    return {
        "meta": {
            "slug": "",
            "title": "",
            "question": "",
            "resolution": "",
            "created": "",
            "lastUpdated": "",
            "status": "ACTIVE",
            "dayCount": 0,
            "startDate": "",
            "classification": "ROUTINE",
        },
        "model": {
            "hypotheses": {},
            "expectedValue": 0,
            "expectedUnit": "",
            "posteriorHistory": [],
        },
        "subModels": {},
        "indicators": {
            "tiers": {
                "tier1_critical": [],
                "tier2_strong": [],
                "tier3_suggestive": [],
                "anti_indicators": [],
            }
        },
        "actorModel": {
            "description": "",
            "actors": {},
            "methodology": [
                "ACTIONS OVER RHETORIC",
                "TAG EVERYTHING",
                "DON'T FRONT-RUN",
                "SOCIALIZATION DETECTION",
            ],
        },
        "evidenceLog": [],
        "dataFeeds": {},
        "watchpoints": [],
        "governance": None,
    }


# ---------------------------------------------------------------------------
# Dashboard Snapshot Generation
# ---------------------------------------------------------------------------

def generate_dashboard(topic: dict, event_label: str = None) -> str:
    """
    Generate a standalone HTML dashboard snapshot with topic state baked in.
    Returns the saved file path.
    """
    slug = topic["meta"]["slug"]
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d-%H%M")
    label = event_label or "snapshot"
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)

    # Read the dashboard template
    template_path = Path(__file__).parent / "dashboard.html"
    template = template_path.read_text(encoding="utf-8")

    # Inject state directly into the HTML so it works standalone
    state_json = json.dumps(topic, ensure_ascii=False, indent=2)

    # Build governance report if available
    gov_json = "null"
    try:
        from governor import governance_report
        gov_json = json.dumps(governance_report(topic), ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Replace the dynamic loading with baked-in state
    injection = f"""<script>
// --- BAKED-IN STATE (snapshot: {timestamp} / {label}) ---
const SNAPSHOT_MODE = true;
const SNAPSHOT_STATE = {state_json};
const SNAPSHOT_GOV = {gov_json};
const SNAPSHOT_META = {{
  generated: "{now.isoformat(timespec='seconds')}",
  event: "{label}",
  slug: "{slug}"
}};
</script>"""

    # Replace the init script block to use baked-in data
    snapshot_init = """<script>
// --- Snapshot overrides ---
if (typeof SNAPSHOT_MODE !== 'undefined' && SNAPSHOT_MODE) {
  // Override fetch-based loading with baked-in state
  async function loadTopics() {
    const sel = document.getElementById('topicSelect');
    sel.innerHTML = '<option value="">Select topic...</option>';
    const opt = document.createElement('option');
    opt.value = SNAPSHOT_META.slug;
    opt.textContent = SNAPSHOT_STATE.meta.title;
    opt.selected = true;
    sel.appendChild(opt);
    switchTopic(SNAPSHOT_META.slug);
  }
  async function switchTopic(slug) {
    if (!slug) return;
    currentSlug = slug;
    state = SNAPSHOT_STATE;
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('mainGrid').style.display = '';
    document.getElementById('topicHeader').style.display = '';
    render();
    if (SNAPSHOT_GOV) { govData = SNAPSHOT_GOV; renderGovernance(); renderVoI(); }
  }
  // Add snapshot banner
  document.addEventListener('DOMContentLoaded', function() {
    const banner = document.createElement('div');
    banner.style.cssText = 'background:#1e293b;border-bottom:2px solid #f59e0b;padding:8px 24px;font-size:12px;color:#f59e0b;text-align:center;letter-spacing:1px;';
    banner.textContent = 'SNAPSHOT: ' + SNAPSHOT_META.event + ' — ' + SNAPSHOT_META.generated;
    document.body.insertBefore(banner, document.body.firstChild);
    loadTopics();
  });
}
</script>"""

    # Insert injection before </head> and snapshot_init before </body>
    html = template.replace("</head>", injection + "\n</head>")
    html = html.replace("</body>", snapshot_init + "\n</body>")

    # Save to dashboards/{slug}/{timestamp}-{label}.html
    out_dir = DASHBOARDS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{timestamp}-{safe_label}.html"
    out_path = out_dir / filename
    out_path.write_text(html, encoding="utf-8")

    return str(out_path)


def list_dashboards(slug: str = None) -> list[dict]:
    """List generated dashboard snapshots, optionally filtered by topic slug."""
    results = []
    if not DASHBOARDS_DIR.exists():
        return results
    search_dirs = [DASHBOARDS_DIR / slug] if slug else DASHBOARDS_DIR.iterdir()
    for d in search_dirs:
        if not d.is_dir():
            continue
        topic_slug = d.name
        for p in sorted(d.glob("*.html"), reverse=True):
            results.append({
                "slug": topic_slug,
                "filename": p.name,
                "path": str(p),
                "url": f"/dashboards/{topic_slug}/{p.name}",
            })
    return results


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python engine.py <command> [args]")
        print("Commands: list, show <slug>, brief <slug>, validate <slug>, govern <slug>")
        print("          scaffold <slug>, dashboard <slug> [event-label], dashboards [slug]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        for t in list_topics():
            status_icon = {"ACTIVE": "+", "RESOLVED": "=", "SUSPENDED": "-"}.get(
                t["status"], "?"
            )
            cls_color = {"ALERT": "!", "ELEVATED": "*", "ROUTINE": " "}.get(
                t["classification"], " "
            )
            gov_health = t.get("governanceHealth") or "?"
            print(f"  [{status_icon}]{cls_color} {t['slug']:20s} {t['title']:30s} [{gov_health}]")

    elif cmd == "govern" and len(sys.argv) > 2:
        from governor import governance_report
        topic = load_topic(sys.argv[2])
        update_day_count(topic)
        r = governance_report(topic)
        print(f"\n  {topic['meta']['title']} — Epistemic Health: {r['health']}")
        print(f"  R_t={r['rt']['rt']:.3f} ({r['rt']['regime']}) | "
              f"Entropy={r['entropy']:.3f}/{r['max_entropy']:.3f} ({r['uncertainty_ratio']:.0%})")
        if r["issues"]:
            for issue in r["issues"]:
                print(f"  ! {issue}")
        print()

    elif cmd == "show" and len(sys.argv) > 2:
        topic = load_topic(sys.argv[2])
        meta = topic["meta"]
        model = topic["model"]
        gov = topic.get("governance", {})
        print(f"\n  {meta['title']}")
        print(f"  {meta['question']}")
        print(f"  Status: {meta['status']} | Classification: {meta['classification']}"
              f" | Health: {gov.get('health', 'not computed')}")
        print(f"  Day {meta['dayCount']} | Last updated: {meta['lastUpdated']}")
        print()
        for k, h in model["hypotheses"].items():
            bar = "#" * int(h["posterior"] * 40)
            print(f"  {k} {h['label']:15s} {h['posterior']:5.1%} {bar}")
        ev = model.get("expectedValue", "?")
        eu = model.get("expectedUnit", "")
        print(f"\n  E[{eu}] = {ev}")
        print()
        summary = get_indicator_summary(topic)
        for tier, counts in summary.items():
            fired = counts["FIRED"] + counts["PARTIAL"]
            total = sum(counts.values())
            print(f"  {tier}: {fired}/{total} fired")

    elif cmd == "brief" and len(sys.argv) > 2:
        topic = load_topic(sys.argv[2])
        update_day_count(topic)
        print(generate_brief(topic))

    elif cmd == "validate" and len(sys.argv) > 2:
        try:
            load_topic(sys.argv[2])
            print(f"  {sys.argv[2]}: valid")
        except (ValueError, FileNotFoundError) as e:
            print(f"  {sys.argv[2]}: INVALID — {e}")

    elif cmd == "scaffold" and len(sys.argv) > 2:
        slug = sys.argv[2]
        existing = TOPICS_DIR / f"{slug}.json"
        if existing.exists():
            print(f"  Topic already exists: {slug}")
            sys.exit(1)
        path = scaffold_topic(slug)
        print(f"  Scaffolded: {path}")
        print(f"  Next steps:")
        print(f"    1. Edit topics/{slug}.json — fill in question, hypotheses, indicators")
        print(f"    2. python engine.py validate {slug}")
        print(f"    3. python engine.py govern {slug}")
        print(f"    4. python engine.py dashboard {slug} initial")
        print(f"  See PROTOCOL.md for full instructions.")

    elif cmd == "dashboard" and len(sys.argv) > 2:
        topic = load_topic(sys.argv[2])
        update_day_count(topic)
        label = sys.argv[3] if len(sys.argv) > 3 else None
        path = generate_dashboard(topic, event_label=label)
        print(f"  Dashboard saved: {path}")

    elif cmd == "dashboards":
        slug = sys.argv[2] if len(sys.argv) > 2 else None
        dashes = list_dashboards(slug)
        if not dashes:
            print("  No dashboards generated yet.")
        for d in dashes:
            print(f"  {d['slug']:20s} {d['filename']}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
