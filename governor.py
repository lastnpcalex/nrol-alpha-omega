"""
NRL-Alpha Omega — Epistemic Governor

Agent Governor-derived epistemic safeguards for the Bayesian estimator.
Implements: R_t scoring, dual-ledger, claim lifecycle, admissibility gating,
VoI query prioritization, and hallucination failure mode detection.

Built on patterns from @unpingable's Agent Governor framework:
  https://github.com/unpingable/agent_governor

Key adaptations:
  - R_t = PD/E control equation → evidence freshness scoring
  - Dual-ledger (facts/ vs decisions/) → evidence classification
  - Epistemic stack claim lifecycle → evidence state tracking
  - Admissibility gating → hypothesis quality validation
  - VoI prioritization → search query ranking
  - 10 hallucination failure modes → posterior update pre-commit checks
  - Monotonic constraint compiler → constraint chain auditing

Thank you to unpingable for making the framework public.
"""

import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


# ===========================================================================
# 1. R_t EVIDENCE FRESHNESS SCORING
#
#   Adapted from Governor's R_t = (P * D) / E
#   Original: Risk = (Privilege × Delay) / Evidence
#   Ours:     Staleness = (Prior_Strength × Hours_Since_Update) / Evidence_Count
#
#   High R_t → this topic/hypothesis needs fresh evidence NOW
#   Low R_t  → well-evidenced and recently updated, can wait
# ===========================================================================

def compute_rt(topic: dict) -> dict:
    """
    Compute R_t (evidence staleness risk) for each hypothesis.

    Returns dict of {hypothesis_key: {rt, regime, priority_rank}} sorted by
    urgency. Higher R_t = more urgent need for fresh evidence.

    Regimes (from Governor):
      SAFE:      R_t < 0.3  — well-evidenced, recently updated
      ELASTIC:   0.3-1.0    — normal operating range
      DANGEROUS: 1.0-3.0    — stale, needs attention
      RUNAWAY:   > 3.0      — critically stale, prioritize immediately
    """
    model = topic["model"]
    hypotheses = model["hypotheses"]
    evidence_log = topic.get("evidenceLog", [])
    last_updated = topic["meta"].get("lastUpdated", "")

    # Hours since last update
    delay_hours = _hours_since(last_updated) if last_updated else 168  # default 1 week

    # Count evidence entries in last 24h, 72h, and total
    now = datetime.now(timezone.utc)
    evidence_24h = sum(1 for e in evidence_log
                       if _parse_time(e.get("time")) and
                       (now - _parse_time(e["time"])).total_seconds() < 86400)
    evidence_72h = sum(1 for e in evidence_log
                       if _parse_time(e.get("time")) and
                       (now - _parse_time(e["time"])).total_seconds() < 259200)
    evidence_total = len(evidence_log)

    results = {}
    for k, h in hypotheses.items():
        # Prior strength = how concentrated is the posterior?
        # A 50% posterior has more "weight" than a 5% one
        prior_strength = h["posterior"]

        # Evidence quality: recent evidence counts more
        # E_t = (evidence_24h * 3 + evidence_72h * 1 + 1) to avoid div-by-zero
        evidence_quality = evidence_24h * 3.0 + evidence_72h * 1.0 + 1.0

        # R_t = (P_t * D_t) / E_t
        rt = (prior_strength * delay_hours) / evidence_quality

        # Determine regime
        if rt < 0.3:
            regime = "SAFE"
        elif rt < 1.0:
            regime = "ELASTIC"
        elif rt < 3.0:
            regime = "DANGEROUS"
        else:
            regime = "RUNAWAY"

        results[k] = {
            "rt": round(rt, 3),
            "regime": regime,
            "prior_strength": round(prior_strength, 4),
            "delay_hours": round(delay_hours, 1),
            "evidence_quality": round(evidence_quality, 1),
        }

    # Add priority ranking (highest R_t = rank 1)
    ranked = sorted(results.items(), key=lambda x: -x[1]["rt"])
    for rank, (k, v) in enumerate(ranked, 1):
        results[k]["priority_rank"] = rank

    return results


def compute_topic_rt(topic: dict) -> dict:
    """
    Compute aggregate R_t for the whole topic.
    Uses the maximum hypothesis R_t as the topic score.
    """
    hypothesis_rt = compute_rt(topic)
    if not hypothesis_rt:
        return {"rt": 0, "regime": "SAFE"}

    max_entry = max(hypothesis_rt.values(), key=lambda x: x["rt"])
    return {
        "rt": max_entry["rt"],
        "regime": max_entry["regime"],
        "worst_hypothesis": [k for k, v in hypothesis_rt.items()
                             if v["rt"] == max_entry["rt"]][0],
        "per_hypothesis": hypothesis_rt,
    }


# ===========================================================================
# 2. DUAL LEDGER — Facts vs Decisions
#
#   From Governor's facts/ vs decisions/ ledgers:
#   - Facts: empirical observations, auto-invalidate when contradicted
#   - Decisions: normative choices (posterior updates), require explicit supersession
#
#   Every evidence entry is classified as FACT or DECISION.
#   Facts can be VERIFIED/UNVERIFIED/STALE/INVALIDATED.
#   Decisions are ACTIVE or SUPERSEDED.
# ===========================================================================

FACT_TAGS = {"EVENT", "KINETIC", "DATA", "ECON", "FORCE", "DIPLO", "POLL"}
DECISION_TAGS = {"INTEL", "RHETORIC"}  # analysis / interpretation

# Evidence TTLs by tag (hours before evidence becomes STALE)
EVIDENCE_TTL = {
    "EVENT": 72,      # events are facts but context shifts
    "KINETIC": 48,    # kinetic events: fast-moving
    "DATA": 168,      # market/statistical data: 1 week
    "ECON": 168,      # economic data: 1 week
    "FORCE": 24,      # force positions: very perishable
    "DIPLO": 168,     # diplomatic: slower decay
    "RHETORIC": 24,   # rhetoric: extremely perishable (talk is cheap)
    "INTEL": 72,      # analysis: medium decay
    "POLL": 168,      # polling data: 1 week
    "POLICY": 720,    # policy decisions: 30 days
}


def classify_evidence(entry: dict) -> str:
    """Classify an evidence entry as FACT or DECISION."""
    tag = entry.get("tag", "")
    if tag in FACT_TAGS:
        return "FACT"
    return "DECISION"


def audit_evidence_freshness(topic: dict) -> dict:
    """
    Audit all evidence for staleness. Returns a report with counts
    and lists of stale entries.

    Evidence freshness states:
      FRESH:       within TTL
      STALE:       past TTL, may no longer be accurate
      INVALIDATED: explicitly contradicted by newer evidence
    """
    now = datetime.now(timezone.utc)
    evidence = topic.get("evidenceLog", [])

    report = {
        "total": len(evidence),
        "fresh": 0,
        "stale": 0,
        "unknown_time": 0,
        "stale_entries": [],
        "by_tag": {},
    }

    for i, entry in enumerate(evidence):
        tag = entry.get("tag", "MISC")
        ttl_hours = EVIDENCE_TTL.get(tag, 72)

        entry_time = _parse_time(entry.get("time"))
        if not entry_time:
            report["unknown_time"] += 1
            continue

        age_hours = (now - entry_time).total_seconds() / 3600

        if age_hours > ttl_hours:
            report["stale"] += 1
            report["stale_entries"].append({
                "index": i,
                "tag": tag,
                "age_hours": round(age_hours, 1),
                "ttl_hours": ttl_hours,
                "text": entry["text"][:80],
                "ledger": classify_evidence(entry),
            })
        else:
            report["fresh"] += 1

        # Per-tag breakdown
        if tag not in report["by_tag"]:
            report["by_tag"][tag] = {"fresh": 0, "stale": 0}
        if age_hours > ttl_hours:
            report["by_tag"][tag]["stale"] += 1
        else:
            report["by_tag"][tag]["fresh"] += 1

    return report


# ===========================================================================
# 3. CLAIM LIFECYCLE
#
#   From Governor's epistemic stack: PROPOSED → SUPPORTED → CONTESTED → INVALIDATED
#   Applied to evidence entries and hypotheses.
#
#   Evidence lifecycle:
#     PROPOSED    — single source, unconfirmed
#     SUPPORTED   — corroborated by independent source
#     CONTESTED   — contradicted by credible counter-evidence
#     INVALIDATED — definitively shown false
#
#   These states affect how much weight evidence carries in posterior updates.
# ===========================================================================

CLAIM_STATES = ["PROPOSED", "SUPPORTED", "CONTESTED", "INVALIDATED"]
CLAIM_WEIGHTS = {
    "PROPOSED": 0.5,     # half weight — single source
    "SUPPORTED": 1.0,    # full weight — corroborated
    "CONTESTED": 0.2,    # low weight — disputed
    "INVALIDATED": 0.0,  # zero weight — known false
}


def assess_claim_state(entry: dict, evidence_log: list) -> str:
    """
    Assess the lifecycle state of an evidence entry based on
    corroboration/contradiction in the rest of the log.

    Uses provenance to determine initial state:
      OBSERVED → starts as SUPPORTED (direct observation)
      RETRIEVED → starts as PROPOSED (needs corroboration)
      USER_PROVIDED → starts as SUPPORTED (trusted source)
      DERIVED → starts as PROPOSED (analytical claim)

    Override: predictions about the future are always PROPOSED regardless
    of provenance. A trusted source saying "X will happen" is still a
    prediction, not an observation.
    """
    provenance = entry.get("provenance", "RETRIEVED")
    tag = entry.get("tag", "")
    text_lower = entry.get("text", "").lower()

    # Prediction override: future-tense claims are PROPOSED regardless of source.
    # A prediction is not an observation — trust the source, not the forecast.
    if _is_prediction(text_lower, tag):
        # Still check for corroboration/contradiction below,
        # but start from PROPOSED instead of SUPPORTED
        pass
    elif provenance in ("OBSERVED", "USER_PROVIDED"):
        # Direct observations and user-provided facts start as SUPPORTED
        return "SUPPORTED"

    # Check for corroboration: another entry with similar content from different source
    entry_text_lower = entry.get("text", "").lower()
    entry_source = entry.get("source", "")

    corroborated = False
    contradicted = False

    for other in evidence_log:
        if other is entry:
            continue
        other_text = other.get("text", "").lower()
        other_source = other.get("source", "")

        # Skip same source
        if entry_source and other_source == entry_source:
            continue

        # Simple similarity check: shared significant words
        entry_words = set(w for w in entry_text_lower.split() if len(w) > 4)
        other_words = set(w for w in other_text.split() if len(w) > 4)
        overlap = len(entry_words & other_words)

        if overlap >= 3:
            # Check for contradiction markers
            contradiction_markers = ["denied", "denies", "false", "incorrect",
                                     "not true", "contradicts", "refuted"]
            if any(m in other_text for m in contradiction_markers):
                contradicted = True
            else:
                corroborated = True

    if contradicted:
        return "CONTESTED"
    if corroborated:
        return "SUPPORTED"
    return "PROPOSED"


def get_effective_weight(entry: dict, evidence_log: list,
                         topic: dict = None) -> float:
    """
    Get the effective weight of an evidence entry for posterior updates.

    Weight = claimState_weight * source_trust_factor

    source_trust_factor priority:
      1. sourceCalibration.effectiveTrust (Bayesian-updated)
      2. SOURCE_TRUST base dict (from calibrate.py)
      3. 0.5 fallback for unknown sources

    Result clamped to [0.05, 1.0].
    """
    state = entry.get("claimState") or assess_claim_state(entry, evidence_log)
    claim_weight = CLAIM_WEIGHTS.get(state, 0.5)

    # Source trust factor — only applied when topic is available
    source_trust_factor = 1.0
    source_str = entry.get("source") or ""

    if source_str and topic is not None:
        try:
            from framework.source_ledger import extract_sources
            from framework.calibrate import SOURCE_TRUST
        except ImportError:
            # Framework not available — skip trust adjustment
            return claim_weight

        # Get effective trust from calibration, or fall back to base
        cal = topic.get("sourceCalibration", {})
        effective_trust = cal.get("effectiveTrust", {})

        trust_values = []
        for src in extract_sources(source_str):
            t = effective_trust.get(src)
            if t is not None:
                trust_values.append(t)
            else:
                base_t = SOURCE_TRUST.get(src)
                if base_t is not None:
                    trust_values.append(base_t)
                else:
                    trust_values.append(0.5)

        if trust_values:
            # Use minimum trust among all sources (conservative)
            source_trust_factor = min(trust_values)

    weight = claim_weight * source_trust_factor
    return max(0.05, min(1.0, round(weight, 4)))


# ===========================================================================
# 4. ADMISSIBILITY GATING — Hypothesis Quality Validation
#
#   From Governor's admissibility gating: evaluates claims by
#   setpoint clarity (how well-defined) and observability (can it be verified).
#
#   A well-formed hypothesis must have:
#     1. Clear midpoint (quantifiable)
#     2. Defined unit of measurement
#     3. Observable resolution criterion (from the topic)
#     4. Falsifiability (at least one indicator that could disprove it)
#     5. Distinguishability (different enough from adjacent hypotheses)
# ===========================================================================

def validate_hypotheses(topic: dict) -> dict:
    """
    Run admissibility gating on all hypotheses.
    Returns a quality report with pass/fail per criterion.
    """
    hypotheses = topic["model"]["hypotheses"]
    resolution = topic["meta"].get("resolution", "")
    indicators = topic.get("indicators", {}).get("tiers", {})

    # Flatten all indicators
    all_indicators = []
    for tier_inds in indicators.values():
        all_indicators.extend(tier_inds)

    anti_indicators = indicators.get("anti_indicators", [])

    report = {}

    for k, h in hypotheses.items():
        checks = {
            "has_label": bool(h.get("label")),
            "has_midpoint": h.get("midpoint") is not None and h["midpoint"] != 0,
            "has_unit": bool(h.get("unit")),
            "resolution_defined": bool(resolution),
            "has_supporting_indicators": False,
            "has_contrary_indicators": False,
            "distinguishable": True,
            "prior_nonzero": h.get("posterior", 0) > 0,
            "prior_not_certain": h.get("posterior", 0) < 0.99,
        }

        # Check if any indicator specifically mentions this hypothesis or its range
        label_lower = h.get("label", "").lower()
        for ind in all_indicators:
            effect = (ind.get("posteriorEffect") or "").lower()
            if k.lower() in effect or label_lower in effect:
                checks["has_supporting_indicators"] = True
                break

        for ind in anti_indicators:
            effect = (ind.get("posteriorEffect") or "").lower()
            if k.lower() in effect or label_lower in effect:
                checks["has_contrary_indicators"] = True
                break

        # Distinguishability: midpoints should be >20% apart
        for k2, h2 in hypotheses.items():
            if k2 == k:
                continue
            if h.get("midpoint") and h2.get("midpoint"):
                try:
                    ratio = abs(h["midpoint"] - h2["midpoint"]) / max(
                        abs(h["midpoint"]), abs(h2["midpoint"]), 1
                    )
                    if ratio < 0.2:
                        checks["distinguishable"] = False
                except (TypeError, ZeroDivisionError):
                    pass

        # Overall score
        passed = sum(1 for v in checks.values() if v)
        total = len(checks)
        grade = "ADMISSIBLE" if passed >= 7 else "MARGINAL" if passed >= 5 else "INADMISSIBLE"

        report[k] = {
            "checks": checks,
            "passed": passed,
            "total": total,
            "grade": grade,
            "setpoint_clarity": "HIGH" if checks["has_midpoint"] and checks["has_unit"] else "LOW",
            "observability": "HIGH" if checks["resolution_defined"] and checks["has_supporting_indicators"] else "LOW",
            "falsifiability": "YES" if checks["has_contrary_indicators"] else "NO",
        }

    return report


# ===========================================================================
# 5. VALUE OF INFORMATION (VoI) — Query Prioritization
#
#   From Governor's VoI pattern: prioritize questions that would most
#   reduce posterior uncertainty.
#
#   For each search query, estimate: if this query returns a positive result,
#   how much would posteriors change? Rank by expected information gain.
#
#   Entropy-based: queries that could discriminate between close hypotheses
#   are more valuable than queries confirming what we already know.
# ===========================================================================

def compute_entropy(topic: dict) -> float:
    """Compute Shannon entropy of the posterior distribution (bits)."""
    hypotheses = topic["model"]["hypotheses"]
    entropy = 0.0
    for h in hypotheses.values():
        p = h["posterior"]
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 4)


def compute_max_entropy(topic: dict) -> float:
    """Maximum possible entropy (uniform distribution)."""
    n = len(topic["model"]["hypotheses"])
    return round(math.log2(n), 4) if n > 0 else 0


def compute_uncertainty_ratio(topic: dict) -> float:
    """
    Ratio of current entropy to max entropy. 1.0 = maximum uncertainty
    (uniform), 0.0 = complete certainty (one hypothesis at 100%).
    """
    max_ent = compute_max_entropy(topic)
    if max_ent == 0:
        return 0.0
    return round(compute_entropy(topic) / max_ent, 4)


def prioritize_queries(topic: dict) -> list[dict]:
    """
    Rank search queries by estimated Value of Information.

    Strategy:
    - If entropy is high (>0.8 of max) → prioritize discriminating queries
    - If entropy is low (<0.3 of max) → prioritize confirmation/disconfirmation
    - Weight unfired high-tier indicators highest (they carry the most info)
    """
    queries = topic.get("searchQueries", [])
    indicators = topic.get("indicators", {}).get("tiers", {})
    uncertainty = compute_uncertainty_ratio(topic)

    # Score unfired indicators by tier
    unfired_by_tier = {
        "tier1_critical": [],
        "tier2_strong": [],
        "tier3_suggestive": [],
    }
    for tier_key in unfired_by_tier:
        for ind in indicators.get(tier_key, []):
            if ind["status"] == "NOT_FIRED":
                unfired_by_tier[tier_key].append(ind)

    # Build prioritized query list
    prioritized = []

    # Tier 1 unfired indicators generate highest-VoI queries
    for ind in unfired_by_tier.get("tier1_critical", []):
        prioritized.append({
            "query": f"Evidence for: {ind['desc']}",
            "source": f"tier1_critical/{ind['id']}",
            "voi_score": 1.0,
            "reason": "Unfired Tier 1 indicator — would trigger ALERT and major posterior shift",
        })

    # Tier 2 next
    for ind in unfired_by_tier.get("tier2_strong", []):
        prioritized.append({
            "query": f"Evidence for: {ind['desc']}",
            "source": f"tier2_strong/{ind['id']}",
            "voi_score": 0.7,
            "reason": "Unfired Tier 2 indicator — would trigger ELEVATED",
        })

    # User-defined queries get base VoI
    for q in queries:
        prioritized.append({
            "query": q,
            "source": "searchQueries",
            "voi_score": 0.4 if uncertainty > 0.5 else 0.2,
            "reason": "Configured search query",
        })

    # Anti-indicators get boosted VoI when primary thesis is strong
    hypotheses = topic["model"]["hypotheses"]
    max_posterior = max((h["posterior"] for h in hypotheses.values()), default=0)
    if max_posterior > 0.5:
        for ind in indicators.get("anti_indicators", []):
            if ind["status"] == "NOT_FIRED":
                prioritized.append({
                    "query": f"Evidence against: {ind['desc']}",
                    "source": f"anti_indicators/{ind['id']}",
                    "voi_score": 0.6,
                    "reason": f"Primary thesis at {max_posterior:.0%} — "
                              "anti-indicator check prevents confirmation bias",
                })

    # Sort by VoI score descending
    prioritized.sort(key=lambda x: -x["voi_score"])
    return prioritized


# ===========================================================================
# 6. HALLUCINATION FAILURE MODE DETECTION
#
#   From Governor's 10 failure modes. Applied to posterior update proposals
#   as a pre-commit checklist.
# ===========================================================================

FAILURE_MODES = [
    {
        "id": "no_evidence",
        "name": "No Evidence",
        "desc": "Posterior moved without any grounding evidence",
        "severity": "CRITICAL",
    },
    {
        "id": "confidence_inflation",
        "name": "Confidence Inflation",
        "desc": "Small evidence produced disproportionately large posterior shift",
        "severity": "HIGH",
    },
    {
        "id": "repetition_as_validation",
        "name": "Repetition as Validation",
        "desc": "Same fact cited multiple times treated as multiple independent facts",
        "severity": "HIGH",
    },
    {
        "id": "stale_evidence",
        "name": "Stale Evidence",
        "desc": "Old data treated as current — evidence past its TTL",
        "severity": "MEDIUM",
    },
    {
        "id": "circular_reasoning",
        "name": "Circular Reasoning",
        "desc": "Own prior analysis cited as evidence for the same conclusion",
        "severity": "CRITICAL",
    },
    {
        "id": "scope_creep",
        "name": "Scope Creep",
        "desc": "Evidence from adjacent topic used without explicit justification",
        "severity": "MEDIUM",
    },
    {
        "id": "modal_confusion",
        "name": "Modal Confusion",
        "desc": "Treating 'could happen' as 'will happen' — possibility vs probability",
        "severity": "HIGH",
    },
    {
        "id": "citation_drift",
        "name": "Citation Drift",
        "desc": "Source says X, evidence entry says Y (subtle reframing)",
        "severity": "MEDIUM",
    },
    {
        "id": "evidence_laundering",
        "name": "Evidence Laundering",
        "desc": "Unreliable source treated as reliable through intermediary citation",
        "severity": "HIGH",
    },
    {
        "id": "quorum_failure",
        "name": "Quorum Failure",
        "desc": "Major conclusion drawn from single source without corroboration",
        "severity": "MEDIUM",
    },
    {
        "id": "rhetoric_as_evidence",
        "name": "Rhetoric as Evidence",
        "desc": "RHETORIC-tagged evidence used to justify posterior shift — "
                "actions over rhetoric violated",
        "severity": "HIGH",
    },
    {
        "id": "unresolved_contradiction",
        "name": "Unresolved Contradiction",
        "desc": "Posterior shift while HIGH-severity contradictions remain unresolved",
        "severity": "CRITICAL",
    },
    {
        "id": "discredited_source",
        "name": "Discredited Source",
        "desc": "Posterior shift relies on evidence from a source with effective trust below 0.30",
        "severity": "HIGH",
    },
    {
        "id": "red_team_override",
        "name": "Red Team Override",
        "desc": "Devil's advocate score exceeds 0.7 — strong counterevidence exists",
        "severity": "MEDIUM",
    },
]


def check_update_proposal(topic: dict, proposed_posteriors: dict[str, float],
                          evidence_refs: list[str] = None,
                          reason: str = "") -> dict:
    """
    Run the hallucination failure mode checklist against a proposed
    posterior update. Returns pass/fail per mode with explanations.

    This is a PRE-COMMIT check — run it before applying the update.
    """
    current = topic["model"]["hypotheses"]
    evidence_log = topic.get("evidenceLog", [])
    freshness = audit_evidence_freshness(topic)

    results = {
        "passed": True,
        "failures": [],
        "warnings": [],
        "checks": {},
    }

    # 1. No Evidence
    has_evidence = evidence_refs is not None and len(evidence_refs) > 0
    max_shift = max(
        abs(proposed_posteriors.get(k, current[k]["posterior"]) - current[k]["posterior"])
        for k in current
    )
    if max_shift > 0.02 and not has_evidence:
        results["checks"]["no_evidence"] = {
            "passed": False,
            "detail": f"Shift of {max_shift:.0%} with no evidence references",
        }
        results["failures"].append("no_evidence")
        results["passed"] = False
    else:
        results["checks"]["no_evidence"] = {"passed": True}

    # 2. Confidence Inflation
    if has_evidence and max_shift > 0.15 and len(evidence_refs) < 2:
        results["checks"]["confidence_inflation"] = {
            "passed": False,
            "detail": f"Shift of {max_shift:.0%} from only {len(evidence_refs)} evidence ref(s)",
        }
        results["warnings"].append("confidence_inflation")
    else:
        results["checks"]["confidence_inflation"] = {"passed": True}

    # 3. Repetition as Validation
    if has_evidence:
        unique_texts = set()
        dup_count = 0
        for ref in evidence_refs:
            for e in evidence_log:
                if e.get("time") == ref or e.get("text", "")[:50] == ref[:50]:
                    text_key = e.get("text", "")[:100]
                    if text_key in unique_texts:
                        dup_count += 1
                    unique_texts.add(text_key)
        if dup_count > 0:
            results["checks"]["repetition_as_validation"] = {
                "passed": False,
                "detail": f"{dup_count} duplicate evidence entries used as independent refs",
            }
            results["warnings"].append("repetition_as_validation")
        else:
            results["checks"]["repetition_as_validation"] = {"passed": True}
    else:
        results["checks"]["repetition_as_validation"] = {"passed": True}

    # 4. Stale Evidence
    if freshness["stale"] > freshness["fresh"] and max_shift > 0.05:
        results["checks"]["stale_evidence"] = {
            "passed": False,
            "detail": f"{freshness['stale']}/{freshness['total']} evidence entries are stale",
        }
        results["warnings"].append("stale_evidence")
    else:
        results["checks"]["stale_evidence"] = {"passed": True}

    # 5. Circular Reasoning
    reason_lower = reason.lower()
    circular_markers = ["as we noted", "as previously established",
                        "consistent with our model", "confirms our thesis",
                        "as predicted"]
    if any(m in reason_lower for m in circular_markers):
        results["checks"]["circular_reasoning"] = {
            "passed": False,
            "detail": "Update reason references own prior analysis as evidence",
        }
        results["failures"].append("circular_reasoning")
        results["passed"] = False
    else:
        results["checks"]["circular_reasoning"] = {"passed": True}

    # 6. Modal Confusion — check BOTH reason text AND referenced evidence
    modal_markers = ["could", "might", "may", "possible", "potentially",
                     "suggests", "indicates"]
    certainty_markers = ["will", "certain", "inevitable", "guaranteed",
                         "will be", "will have", "will achieve"]
    modal_fail = False
    modal_detail = ""

    # Check reason text (original behavior)
    if (any(m in reason_lower for m in modal_markers) and
            any(m in reason_lower for m in certainty_markers)):
        modal_fail = True
        modal_detail = "Reason mixes possibility and certainty language"

    # Check referenced evidence text (new: scan the evidence itself)
    if has_evidence and not modal_fail:
        for ref in evidence_refs:
            for e in evidence_log:
                if e.get("time") == ref:
                    ev_text = e.get("text", "").lower()
                    has_modal = any(m in ev_text for m in modal_markers)
                    has_certain = any(m in ev_text for m in certainty_markers)
                    if has_modal and has_certain:
                        modal_fail = True
                        modal_detail = (
                            f"Evidence text mixes possibility and certainty: "
                            f"'{e.get('text', '')[:80]}...'"
                        )
                        break
            if modal_fail:
                break

    if modal_fail:
        results["checks"]["modal_confusion"] = {
            "passed": False,
            "detail": modal_detail,
        }
        results["warnings"].append("modal_confusion")
    else:
        results["checks"]["modal_confusion"] = {"passed": True}

    # 7. Quorum Failure (single-source major shift)
    if has_evidence and max_shift > 0.10:
        sources = set()
        for ref in evidence_refs:
            for e in evidence_log:
                if e.get("time") == ref:
                    sources.add(e.get("source") or "unknown")
        if len(sources) <= 1:
            results["checks"]["quorum_failure"] = {
                "passed": False,
                "detail": f"Major shift ({max_shift:.0%}) from single source: {sources}",
            }
            results["warnings"].append("quorum_failure")
        else:
            results["checks"]["quorum_failure"] = {"passed": True}
    else:
        results["checks"]["quorum_failure"] = {"passed": True}

    # 8. Rhetoric as Evidence — ACTIONS OVER RHETORIC enforcement
    #    If any referenced evidence has tag=RHETORIC, warn that posteriors
    #    are being moved on talk rather than action.
    if has_evidence and max_shift > 0.01:
        rhetoric_refs = []
        for ref in evidence_refs:
            for e in evidence_log:
                if e.get("time") == ref and e.get("tag") in ("RHETORIC",):
                    rhetoric_refs.append(e.get("text", "")[:60])
        if rhetoric_refs:
            # Check if ALL refs are rhetoric (worse) vs some (less bad)
            all_rhetoric = len(rhetoric_refs) == len(evidence_refs)
            if all_rhetoric:
                results["checks"]["rhetoric_as_evidence"] = {
                    "passed": False,
                    "detail": (
                        f"ALL {len(rhetoric_refs)} evidence ref(s) are RHETORIC-tagged. "
                        f"Per methodology: actions over rhetoric — only verified events "
                        f"should move posteriors."
                    ),
                }
                results["warnings"].append("rhetoric_as_evidence")
            else:
                results["checks"]["rhetoric_as_evidence"] = {
                    "passed": False,
                    "detail": (
                        f"{len(rhetoric_refs)}/{len(evidence_refs)} evidence refs are "
                        f"RHETORIC-tagged. Posterior shift partially grounded in talk, "
                        f"not action."
                    ),
                }
                results["warnings"].append("rhetoric_as_evidence")
        else:
            results["checks"]["rhetoric_as_evidence"] = {"passed": True}
    else:
        results["checks"]["rhetoric_as_evidence"] = {"passed": True}

    # 9. Unresolved Contradiction — HARD BLOCK when HIGH contradictions exist
    try:
        from framework.contradictions import get_unresolved_contradictions
        unresolved = get_unresolved_contradictions(topic)
        high_sev = [c for c in unresolved if c.get("severity") == "HIGH"]
        if high_sev and max_shift > 0.02:
            results["checks"]["unresolved_contradiction"] = {
                "passed": False,
                "detail": f"{len(high_sev)} HIGH-severity contradictions unresolved — "
                          f"resolve before shifting posteriors",
            }
            results["failures"].append("unresolved_contradiction")
            results["passed"] = False
        else:
            results["checks"]["unresolved_contradiction"] = {"passed": True}
    except ImportError:
        results["checks"]["unresolved_contradiction"] = {"passed": True, "detail": "Module not available"}

    # 10. Discredited Source — warn if evidence relies on low-trust sources
    if has_evidence and max_shift > 0.02:
        try:
            cal = topic.get("sourceCalibration", {})
            effective_trust = cal.get("effectiveTrust", {})
            discredited = []
            for ref in evidence_refs:
                for e in evidence_log:
                    if e.get("time") == ref:
                        src = e.get("source", "")
                        if src:
                            from framework.source_ledger import extract_sources
                            for s in extract_sources(src):
                                trust = effective_trust.get(s)
                                if trust is not None and trust < 0.30:
                                    discredited.append(f"{s} ({trust:.2f})")
            if discredited:
                results["checks"]["discredited_source"] = {
                    "passed": False,
                    "detail": f"Evidence relies on discredited source(s): {', '.join(discredited)}",
                }
                results["warnings"].append("discredited_source")
            else:
                results["checks"]["discredited_source"] = {"passed": True}
        except ImportError:
            results["checks"]["discredited_source"] = {"passed": True, "detail": "Module not available"}
    else:
        results["checks"]["discredited_source"] = {"passed": True}

    # 11. Red Team Override — warn if devil's advocate score is very high
    # This is checked AFTER posteriors are applied (in engine.update_posteriors),
    # so here we check the most recent red team result from history
    try:
        history = topic["model"].get("posteriorHistory", [])
        if history and "redTeam" in history[-1]:
            rt_score = history[-1]["redTeam"].get("devil_advocate_score", 0)
            if rt_score > 0.7:
                results["checks"]["red_team_override"] = {
                    "passed": False,
                    "detail": f"Devil's advocate score {rt_score:.2f} — "
                              f"strong counterevidence: "
                              f"{history[-1]['redTeam'].get('challenge', '')[:100]}",
                }
                results["warnings"].append("red_team_override")
            else:
                results["checks"]["red_team_override"] = {"passed": True}
        else:
            results["checks"]["red_team_override"] = {"passed": True}
    except (KeyError, IndexError):
        results["checks"]["red_team_override"] = {"passed": True}

    # Fill remaining checks as passed (need runtime context to fully evaluate)
    for mode in FAILURE_MODES:
        if mode["id"] not in results["checks"]:
            results["checks"][mode["id"]] = {"passed": True, "detail": "Not evaluated (requires runtime context)"}

    # Compile governance overrides for audit trail
    overrides = []
    for w in results["warnings"]:
        mode = next((m for m in FAILURE_MODES if m["id"] == w), None)
        if mode:
            overrides.append({
                "id": w,
                "severity": mode["severity"],
                "detail": results["checks"].get(w, {}).get("detail", ""),
            })
    results["governance_overrides"] = overrides

    return results


# ===========================================================================
# 7. CONSTRAINT CHAIN AUDIT
#
#   From Governor's 11-layer monotonic constraint compiler.
#   For our purposes: trace HOW a hypothesis went from prior to current
#   posterior, showing each constraint (evidence) that narrowed it.
# ===========================================================================

def build_constraint_chain(topic: dict, hypothesis_key: str) -> list[dict]:
    """
    Build the constraint chain for a hypothesis: the sequence of
    evidence and updates that moved it from prior to current posterior.

    Each link in the chain shows: what happened, how it shifted the
    posterior, and the cumulative effect.
    """
    history = topic["model"].get("posteriorHistory", [])
    if len(history) < 2:
        return []

    chain = []
    for i in range(1, len(history)):
        prev = history[i - 1]
        curr = history[i]
        prev_val = prev.get(hypothesis_key, 0)
        curr_val = curr.get(hypothesis_key, 0)
        delta = curr_val - prev_val

        if abs(delta) < 0.001:
            continue  # no movement

        chain.append({
            "date": curr.get("date", "?"),
            "note": curr.get("note", "no reason logged"),
            "from": round(prev_val, 4),
            "to": round(curr_val, 4),
            "delta": round(delta, 4),
            "direction": "NARROWED" if abs(curr_val) < abs(prev_val) else "WIDENED",
            "cumulative": round(curr_val, 4),
        })

    return chain


# ===========================================================================
# 8. FULL GOVERNANCE REPORT
#
#   Combine all the above into a single diagnostic for a topic.
# ===========================================================================

def governance_report(topic: dict) -> dict:
    """
    Generate a full epistemic governance report for a topic.
    Combines R_t scoring, evidence freshness, hypothesis admissibility,
    uncertainty metrics, and VoI prioritization.
    """
    rt = compute_topic_rt(topic)
    freshness = audit_evidence_freshness(topic)
    admissibility = validate_hypotheses(topic)
    entropy = compute_entropy(topic)
    max_entropy = compute_max_entropy(topic)
    uncertainty = compute_uncertainty_ratio(topic)
    queries = prioritize_queries(topic)

    # Overall health assessment
    issues = []

    if rt["regime"] in ("DANGEROUS", "RUNAWAY"):
        issues.append(f"R_t in {rt['regime']} regime ({rt['rt']:.2f}) — needs fresh evidence")

    if freshness["stale"] > freshness["fresh"]:
        issues.append(f"Majority of evidence is stale ({freshness['stale']}/{freshness['total']})")

    inadmissible = [k for k, v in admissibility.items() if v["grade"] == "INADMISSIBLE"]
    if inadmissible:
        issues.append(f"Inadmissible hypotheses: {', '.join(inadmissible)}")

    unfalsifiable = [k for k, v in admissibility.items() if v["falsifiability"] == "NO"]
    if unfalsifiable:
        issues.append(f"Unfalsifiable hypotheses (no anti-indicators): {', '.join(unfalsifiable)}")

    if uncertainty > 0.9:
        issues.append("Near-maximum uncertainty — model is not discriminating")
    elif uncertainty < 0.1:
        issues.append("Near-zero uncertainty — check for overconfidence")

    health = "HEALTHY" if len(issues) == 0 else "DEGRADED" if len(issues) <= 2 else "CRITICAL"

    return {
        "health": health,
        "issues": issues,
        "rt": rt,
        "entropy": entropy,
        "max_entropy": max_entropy,
        "uncertainty_ratio": uncertainty,
        "evidence_freshness": freshness,
        "hypothesis_admissibility": admissibility,
        "top_queries": queries[:5],
        "failure_modes": [m["id"] for m in FAILURE_MODES],
    }


# ===========================================================================
# Helpers
# ===========================================================================

# Future-tense markers that indicate a prediction rather than an observation.
# These override provenance-based trust: a trusted source making a prediction
# is still making a prediction, not reporting a fact.
_PREDICTION_MARKERS = [
    "will be", "will have", "will achieve", "will arrive", "will emerge",
    "will replace", "will disrupt", "will enable",
    "by 2026", "by 2027", "by 2028", "by 2029", "by 2030", "by 2031",
    "by 2032", "by 2033", "by 2034", "by 2035",
    "in 2 years", "in 3 years", "in 5 years", "in 10 years",
    "within a year", "within 2 years", "within 5 years",
    "expect", "predict", "forecast", "anticipate",
    "is likely to", "is expected to", "is projected to",
]

# Tags that are inherently predictive/analytical rather than factual
_PREDICTIVE_TAGS = {"RHETORIC", "INTEL"}


def _is_prediction(text_lower: str, tag: str = "") -> bool:
    """
    Detect whether an evidence entry is a prediction about the future
    rather than an observation of the present or past.

    Predictions are always PROPOSED regardless of provenance because
    the source's trustworthiness doesn't make the future more certain.
    """
    # Tag-based: RHETORIC and INTEL entries with future language are predictions
    if tag in _PREDICTIVE_TAGS:
        if any(marker in text_lower for marker in _PREDICTION_MARKERS):
            return True

    # Strong future markers regardless of tag
    strong_markers = [
        "will be able to", "will have achieved", "will be achieved",
        "predict", "forecast", "by 203", "by 202",
    ]
    if any(marker in text_lower for marker in strong_markers):
        return True

    return False


def _hours_since(iso_str: str) -> float:
    """Hours elapsed since an ISO8601 timestamp."""
    try:
        dt = _parse_time(iso_str)
        if dt:
            delta = datetime.now(timezone.utc) - dt
            return delta.total_seconds() / 3600
    except (ValueError, TypeError):
        pass
    return 168.0  # default 1 week


def _parse_time(iso_str: str) -> Optional[datetime]:
    """Parse an ISO8601-ish timestamp. Returns None on failure."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from engine import load_topic, update_day_count

    if len(sys.argv) < 3:
        print("Usage: python governor.py <command> <slug>")
        print("Commands: report, rt, freshness, admissibility, entropy, voi, chain <H>")
        sys.exit(1)

    cmd = sys.argv[1]
    slug = sys.argv[2]
    topic = load_topic(slug)
    update_day_count(topic)

    if cmd == "report":
        r = governance_report(topic)
        print(f"\n  GOVERNANCE REPORT: {topic['meta']['title']}")
        print(f"  Health: {r['health']}")
        print(f"  R_t: {r['rt']['rt']:.3f} ({r['rt']['regime']})")
        print(f"  Entropy: {r['entropy']:.3f} / {r['max_entropy']:.3f} "
              f"({r['uncertainty_ratio']:.0%} uncertainty)")
        print(f"  Evidence: {r['evidence_freshness']['fresh']} fresh, "
              f"{r['evidence_freshness']['stale']} stale "
              f"/ {r['evidence_freshness']['total']} total")
        if r["issues"]:
            print(f"\n  Issues:")
            for issue in r["issues"]:
                print(f"    - {issue}")
        print(f"\n  Top VoI queries:")
        for q in r["top_queries"]:
            print(f"    [{q['voi_score']:.1f}] {q['query'][:70]}")
        print()

    elif cmd == "rt":
        rt = compute_topic_rt(topic)
        print(f"\n  R_t SCORING: {topic['meta']['title']}")
        print(f"  Aggregate: {rt['rt']:.3f} ({rt['regime']})")
        print(f"  Worst: {rt['worst_hypothesis']}")
        for k, v in rt["per_hypothesis"].items():
            bar = "#" * int(min(v["rt"] * 10, 40))
            print(f"    {k} R_t={v['rt']:.3f} [{v['regime']:10s}] {bar}")
        print()

    elif cmd == "freshness":
        r = audit_evidence_freshness(topic)
        print(f"\n  EVIDENCE FRESHNESS: {topic['meta']['title']}")
        print(f"  Fresh: {r['fresh']} | Stale: {r['stale']} | Unknown: {r['unknown_time']}")
        if r["stale_entries"]:
            print(f"\n  Stale entries:")
            for e in r["stale_entries"][:10]:
                print(f"    [{e['tag']:8s}] {e['age_hours']:.0f}h/{e['ttl_hours']}h TTL "
                      f"({e['ledger']}) {e['text']}")
        print()

    elif cmd == "admissibility":
        r = validate_hypotheses(topic)
        print(f"\n  HYPOTHESIS ADMISSIBILITY: {topic['meta']['title']}")
        for k, v in r.items():
            print(f"    {k}: {v['grade']} ({v['passed']}/{v['total']} checks)")
            print(f"       Clarity: {v['setpoint_clarity']} | "
                  f"Observable: {v['observability']} | "
                  f"Falsifiable: {v['falsifiability']}")
        print()

    elif cmd == "entropy":
        ent = compute_entropy(topic)
        max_ent = compute_max_entropy(topic)
        unc = compute_uncertainty_ratio(topic)
        print(f"\n  ENTROPY: {topic['meta']['title']}")
        print(f"  Shannon entropy: {ent:.4f} bits")
        print(f"  Max entropy:     {max_ent:.4f} bits")
        print(f"  Uncertainty:     {unc:.0%}")
        bar_len = int(unc * 40)
        print(f"  [{'#' * bar_len}{'.' * (40 - bar_len)}]")
        print()

    elif cmd == "voi":
        queries = prioritize_queries(topic)
        print(f"\n  VALUE OF INFORMATION: {topic['meta']['title']}")
        for i, q in enumerate(queries, 1):
            print(f"  {i:2d}. [{q['voi_score']:.1f}] {q['query'][:60]}")
            print(f"      {q['reason']}")
        print()

    elif cmd == "chain" and len(sys.argv) > 3:
        h_key = sys.argv[3]
        chain = build_constraint_chain(topic, h_key)
        print(f"\n  CONSTRAINT CHAIN: {h_key} in {topic['meta']['title']}")
        if not chain:
            print("  No movement recorded.")
        for link in chain:
            arrow = "v" if link["delta"] < 0 else "^"
            print(f"  {link['date']} {arrow} {link['from']:.0%} -> {link['to']:.0%} "
                  f"({link['delta']:+.0%}) | {link['note']}")
        print()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
