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
# 1. R_t — SEARCH PRIORITY SCORE
#
#   R_t is an entropy-weighted attention-allocation heuristic. It tells the
#   operator which hypotheses most urgently need fresh evidence. It is NOT
#   a pure information-theoretic derivation — it uses Shannon entropy as a
#   component but does not model the domain's actual volatility or the
#   expected rate of posterior change.
#
#   For each hypothesis H_i:
#     - entropy_contribution = -p_i * log2(p_i)  (how much info this H carries)
#     - time_decay = log2(1 + delay_hours / 24)   (log-scaled staleness)
#     - evidence_recency = weighted count of recent evidence (24h=3x, 72h=1x)
#     - R_t(H_i) = entropy_contribution * time_decay / evidence_recency
#
#   The entropy term ensures low-probability tail hypotheses get flagged
#   when stale — a 5% hypothesis that hasn't been checked carries more
#   surprise value if true than a 50% hypothesis. But the time_decay is
#   a log-scaled proxy for domain volatility, not a model of it. A fast-
#   moving conflict and a slow-moving geological process get the same
#   staleness curve unless the operator tunes rtConfig thresholds.
#
#   Regime thresholds are configurable per-topic via topic["rtConfig"]
#   and can be calibrated against Brier score history.
#
#   High R_t → this hypothesis needs fresh evidence NOW (search priority)
#   Low R_t  → well-evidenced and recently updated, can wait
# ===========================================================================

# Default R_t regime thresholds. Topics can override via rtConfig.
_RT_DEFAULTS = {
    "safe": 0.1,
    "elastic": 0.3,
    "dangerous": 1.0,
    # above dangerous = RUNAWAY
}


def compute_rt(topic: dict) -> dict:
    """
    Compute R_t (search priority score) for each hypothesis.

    Returns dict of {hypothesis_key: {rt, regime, priority_rank}} sorted by
    urgency. Higher R_t = more urgent need for fresh evidence.

    Entropy-weighted heuristic:
      R_t(H_i) = entropy_contribution(H_i) * time_decay / evidence_recency

    Regimes (configurable via topic["rtConfig"]):
      SAFE:      below safe threshold  — well-evidenced, recently updated
      ELASTIC:   safe..elastic         — normal operating range
      DANGEROUS: elastic..dangerous    — stale, needs attention
      RUNAWAY:   above dangerous       — critically stale, prioritize immediately
    """
    model = topic["model"]
    hypotheses = model["hypotheses"]
    evidence_log = topic.get("evidenceLog", [])
    last_updated = topic["meta"].get("lastUpdated", "")

    # Configurable thresholds
    rt_config = topic.get("rtConfig", _RT_DEFAULTS)
    t_safe = rt_config.get("safe", _RT_DEFAULTS["safe"])
    t_elastic = rt_config.get("elastic", _RT_DEFAULTS["elastic"])
    t_dangerous = rt_config.get("dangerous", _RT_DEFAULTS["dangerous"])

    # Hours since last update
    delay_hours = _hours_since(last_updated) if last_updated else 168

    # Logarithmic time scaling — prevents R_t from blowing up linearly
    # for topics that are simply slow-moving
    time_decay = math.log2(1.0 + delay_hours / 24.0)

    # Count evidence entries in last 24h, 72h
    now = datetime.now(timezone.utc)
    evidence_24h = sum(1 for e in evidence_log
                       if _parse_time(e.get("time")) and
                       (now - _parse_time(e["time"])).total_seconds() < 86400)
    evidence_72h = sum(1 for e in evidence_log
                       if _parse_time(e.get("time")) and
                       (now - _parse_time(e["time"])).total_seconds() < 259200)

    # Evidence recency score (recent evidence counts more, +1 to avoid div-by-zero)
    evidence_recency = evidence_24h * 3.0 + evidence_72h * 1.0 + 1.0

    results = {}
    for k, h in hypotheses.items():
        p = h["posterior"]

        # Shannon information contribution: -p * log2(p)
        # At p=0 this is 0 (no information). At p=0.5 this peaks.
        # A 5% tail hypothesis carries more entropy per unit than a 90% one —
        # this is the key fix vs the old formula.
        if p > 0:
            entropy_contribution = -p * math.log2(p)
        else:
            entropy_contribution = 0.0

        # R_t = entropy_contribution * time_decay / evidence_recency
        rt = entropy_contribution * time_decay / evidence_recency

        # Determine regime
        if rt < t_safe:
            regime = "SAFE"
        elif rt < t_elastic:
            regime = "ELASTIC"
        elif rt < t_dangerous:
            regime = "DANGEROUS"
        else:
            regime = "RUNAWAY"

        results[k] = {
            "rt": round(rt, 4),
            "regime": regime,
            "entropy_contribution": round(entropy_contribution, 4),
            "time_decay": round(time_decay, 2),
            "evidence_recency": round(evidence_recency, 1),
            "delay_hours": round(delay_hours, 1),
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

FACT_TAGS = {"EVENT", "KINETIC", "DATA", "ECON", "FORCE", "DIPLO", "POLL",
             "SCIENTIFIC", "TECHNICAL", "LEGAL", "REGULATORY", "POLITICAL",
             "SOCIAL", "ENVIRONMENTAL", "OSINT", "SIGINT", "MARKET",
             "EXPERIMENTAL", "CORPORATE", "DEMOGRAPHIC", "JUDICIAL"}
DECISION_TAGS = {"INTEL", "RHETORIC", "ANALYSIS", "EDITORIAL", "FORECAST"}

# Evidence TTLs by tag (hours before evidence becomes STALE)
# Universal tags + domain-specific tags. Topics can override via tagConfig.
#
# EPISTEMOLOGICAL NOTE ON TTLs
#
# TTLs do NOT model truth decay. A confirmed missile strike does not become
# less true after 48 hours. What decays is *relevance to live estimation*:
#
#   - FORCE positions are perishable because units move. The truth "CVN-78
#     was at 26.5°N on April 3" is permanent, but its relevance to "where
#     is CVN-78 NOW?" drops rapidly.
#   - MARKET prices are perishable because the number changes. Yesterday's
#     Brent close is a historical fact, not a current price.
#   - SCIENTIFIC findings are durable because replication takes months.
#     The finding doesn't change; its relevance window is long.
#
# Mechanically, stale evidence triggers a GOVERNANCE WARNING (not a hard
# block). The posterior update can still proceed. The effect is:
#
#   1. Governance health degrades from HEALTHY → DEGRADED → CRITICAL as
#      the ratio of stale-to-fresh evidence grows.
#   2. The operator sees which evidence categories are aging out.
#   3. R_t scoring prioritizes hypotheses whose evidence base is oldest.
#
# This is an ATTENTION ALLOCATION heuristic — "your evidence base is aging,
# consider refreshing these feeds" — not a claim that old evidence is false.
# A Bayesian purist would model per-hypothesis relevance decay curves; TTLs
# are the coarse-filter approximation that keeps the system tractable for
# an operator running update cycles, not a probabilistic graphical model.
#
EVIDENCE_TTL = {
    # === Universal (any topic) ===
    "EVENT": 72,           # something happened — context shifts
    "DATA": 168,           # quantitative measurement — 1 week
    "RHETORIC": 24,        # someone said something — perishable
    "INTEL": 72,           # non-public analysis — medium decay
    "ANALYSIS": 72,        # expert assessment — medium decay
    "EDITORIAL": 24,       # opinion — very perishable
    "FORECAST": 72,        # prediction — medium decay
    "POLICY": 720,         # policy/regulatory decision — 30 days
    "OSINT": 72,           # open source intelligence
    "SIGINT": 48,          # signals intelligence
    # === Conflict / Geopolitical ===
    "KINETIC": 48,         # military action — fast-moving
    "FORCE": 24,           # force positions — very perishable
    "DIPLO": 168,          # diplomatic — slower decay
    # === Economic / Market ===
    "ECON": 168,           # economic data — 1 week
    "MARKET": 24,          # market prices — very perishable
    # === Political / Governance ===
    "POLITICAL": 168,      # political developments — 1 week
    "POLL": 168,           # polling data — 1 week
    "LEGAL": 720,          # legal rulings — 30 days
    "REGULATORY": 720,     # regulatory actions — 30 days
    "JUDICIAL": 720,       # court decisions — 30 days
    "LEGISLATIVE": 720,    # bills, votes — 30 days
    # === Science / Technology ===
    "SCIENTIFIC": 720,     # papers, studies — slow decay
    "EXPERIMENTAL": 168,   # lab results — 1 week (replication matters)
    "TECHNICAL": 168,      # benchmarks, capabilities — 1 week
    # === Corporate / Industry ===
    "CORPORATE": 168,      # company announcements — 1 week
    "DEMOGRAPHIC": 720,    # census, population — 30 days
    # === Social / Environmental ===
    "SOCIAL": 168,         # protests, opinion, cultural — 1 week
    "ENVIRONMENTAL": 720,  # climate, resources — 30 days
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
        # Schema split: some entries store tag (singular string), others
        # tags (plural array). Read either; prefer the longest TTL among
        # array entries so a multi-tagged entry isn't punished by its
        # shortest tag.
        tag = entry.get("tag")
        if not tag:
            tags = entry.get("tags")
            if isinstance(tags, list) and tags:
                tag = max(
                    (str(t) for t in tags if t),
                    key=lambda t: EVIDENCE_TTL.get(t, 72),
                    default="MISC",
                )
            else:
                tag = "MISC"
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

    # Check for corroboration: another entry with similar content from an
    # independent source. Entries sharing an informationChain trace to the
    # same primary source and cannot corroborate each other.
    entry_text_lower = entry.get("text", "").lower()
    entry_source = entry.get("source", "")
    entry_chain = entry.get("informationChain")

    corroborated = False
    contradicted = False

    for other in evidence_log:
        if other is entry:
            continue
        other_text = other.get("text", "").lower()
        other_source = other.get("source", "")
        other_chain = other.get("informationChain")

        # Skip same source
        if entry_source and other_source == entry_source:
            continue

        # Skip same information chain — these are not independent
        if entry_chain and other_chain and entry_chain == other_chain:
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

    source_trust_factor priority (first match wins per source):
      1. Per-topic sourceCalibration.effectiveTrust (this topic's Bayesian data)
      2. Cross-topic source_db domain trust (same tag, all resolved topics)
      3. Cross-topic source_db overall trust (all domains, all topics)
      4. SOURCE_TRUST base dict (static priors from calibrate.py)
      5. 0.5 fallback for completely unknown sources

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

        # Try loading cross-topic source database
        source_db = None
        try:
            from framework.source_db import load_db
            source_db = load_db()
            # Only use if it has actual data
            if not source_db.get("sources"):
                source_db = None
        except (ImportError, Exception):
            pass

        tag = entry.get("tag", "")

        # Get effective trust from calibration, or fall back through the chain
        cal = topic.get("sourceCalibration", {})
        effective_trust = cal.get("effectiveTrust", {})

        trust_values = []
        for src in extract_sources(source_str):
            t = None

            # Priority 1: per-topic calibration
            t = effective_trust.get(src)

            # Priority 2: cross-topic domain trust (same tag)
            if t is None and source_db and tag:
                src_profile = source_db.get("sources", {}).get(src)
                if src_profile:
                    domain = src_profile.get("domains", {}).get(tag)
                    if domain and domain.get("domainTrust") is not None:
                        t = domain["domainTrust"]

            # Priority 3: cross-topic overall trust
            if t is None and source_db:
                src_profile = source_db.get("sources", {}).get(src)
                if src_profile and src_profile.get("effectiveTrust") is not None:
                    t = src_profile["effectiveTrust"]

            # Priority 4: base trust from calibrate.py
            if t is None:
                base_t = SOURCE_TRUST.get(src)
                if base_t is not None:
                    t = base_t

            # Priority 5: unknown source fallback
            if t is None:
                t = 0.5

            trust_values.append(t)

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


def compute_kl_from_prior(topic: dict) -> dict:
    """
    Compute KL divergence D_KL(current || initial_prior).

    Distinguishes "confident because evidenced" (posterior moved far from
    prior) from "confident because prior-dominated" (posterior is sharp
    but hasn't moved).

    Returns {
        "kl_divergence": float,      # nats (natural log)
        "interpretation": str,       # WELL_EVIDENCED | PRIOR_DOMINATED | MODERATE | INSUFFICIENT_HISTORY
        "current_entropy": float,    # bits
        "initial_prior": dict,       # {H1: p, H2: p, ...}
        "current_posteriors": dict,  # {H1: p, H2: p, ...}
    }
    """
    hypotheses = topic["model"]["hypotheses"]
    history = topic["model"].get("posteriorHistory", [])

    current = {k: h["posterior"] for k, h in hypotheses.items()}
    entropy = compute_entropy(topic)
    max_ent = compute_max_entropy(topic)

    # Need at least one history entry to know the initial prior
    if not history:
        return {
            "kl_divergence": 0.0,
            "interpretation": "INSUFFICIENT_HISTORY",
            "current_entropy": entropy,
            "initial_prior": {},
            "current_posteriors": current,
        }

    # Extract initial prior from first history entry.
    # Handles both formats:
    #   Flat:   {"date": "...", "H1": 0.3, "H2": 0.4, ...}
    #   Nested: {"date": "...", "posteriors": {"H1": 0.3, "H2": 0.4, ...}}
    from engine import extract_posteriors
    initial = extract_posteriors(history[0], list(hypotheses.keys()))
    for k in hypotheses:
        if k not in initial:
            initial[k] = 1.0 / len(hypotheses)

    # D_KL(current || initial) = sum( current[i] * ln(current[i] / initial[i]) )
    kl = 0.0
    for k in hypotheses:
        p = current.get(k, 0.0)
        q = initial.get(k, 0.0)
        if p > 0 and q > 0:
            kl += p * math.log(p / q)
        elif p > 0 and q == 0:
            # Infinite divergence — posterior assigns mass where prior had none
            kl = float("inf")
            break

    kl = round(kl, 4) if kl != float("inf") else float("inf")

    # Interpretation: combine entropy level with KL distance
    # Threshold 0.5: with 5 hypotheses, [0.60, 0.15, 0.10, 0.10, 0.05] has
    # ratio ~0.76; [0.85, 0.05, 0.04, 0.03, 0.03] has ratio ~0.42. The
    # cutoff targets posteriors that are meaningfully concentrated.
    low_entropy = max_ent > 0 and (entropy / max_ent) < 0.5
    if kl == float("inf"):
        interpretation = "WELL_EVIDENCED"
    elif low_entropy and kl > 0.5:
        interpretation = "WELL_EVIDENCED"
    elif low_entropy and kl < 0.1:
        interpretation = "PRIOR_DOMINATED"
    else:
        interpretation = "MODERATE"

    return {
        "kl_divergence": kl,
        "interpretation": interpretation,
        "current_entropy": entropy,
        "initial_prior": initial,
        "current_posteriors": current,
    }


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
                          reason: str = "", **kwargs) -> dict:
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

    # 2. Confidence Inflation — HARD BLOCKER (sample-size discipline).
    if has_evidence and max_shift > 0.15 and len(evidence_refs) < 2:
        results["checks"]["confidence_inflation"] = {
            "passed": False,
            "detail": f"Shift of {max_shift:.0%} from only {len(evidence_refs)} evidence ref(s) — "
                      f"large updates require ≥2 independent refs",
        }
        results["failures"].append("confidence_inflation")
        results["passed"] = False
    else:
        results["checks"]["confidence_inflation"] = {"passed": True}

    # 3. Repetition as Validation
    if has_evidence:
        unique_texts = set()
        dup_count = 0
        # Also check information chains: refs sharing a chain are one unit
        seen_chains = set()
        chain_dup_count = 0
        for ref in evidence_refs:
            for e in evidence_log:
                if e.get("time") == ref or e.get("text", "")[:50] == ref[:50]:
                    text_key = e.get("text", "")[:100]
                    if text_key in unique_texts:
                        dup_count += 1
                    unique_texts.add(text_key)
                    # Information chain check
                    chain = e.get("informationChain")
                    if chain:
                        if chain in seen_chains:
                            chain_dup_count += 1
                        seen_chains.add(chain)
        total_dups = dup_count + chain_dup_count
        if total_dups > 0:
            detail_parts = []
            if dup_count > 0:
                detail_parts.append(f"{dup_count} duplicate text(s)")
            if chain_dup_count > 0:
                detail_parts.append(f"{chain_dup_count} same-chain ref(s)")
            results["checks"]["repetition_as_validation"] = {
                "passed": False,
                "detail": f"{' + '.join(detail_parts)} used as independent evidence — "
                          f"Bayesian updates require independent observations; "
                          f"deduplicate or use informationChain to flag correlation",
            }
            results["failures"].append("repetition_as_validation")
            results["passed"] = False
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

    # 4b. Sensitivity analysis (populated by bayesian_update when lr_range used)
    sensitivity_meta = kwargs.get("sensitivity_meta", {})
    is_replay = kwargs.get("is_replay", False)
    if sensitivity_meta:
        dominant_stable = sensitivity_meta.get("dominantHypothesisStable", True)
        width = sensitivity_meta.get("maxRangeWidth", 0.0)
        lr_confidence = sensitivity_meta.get("lr_confidence", "MEDIUM")
        topic_classification = topic.get("meta", {}).get("classification", "ROUTINE")
        if not dominant_stable:
            detail = "Dominant hypothesis flips across LR range — conclusion not robust"
            # HARD BLOCK during replay (calibration tooling — strict by design)
            # OR on ALERT topics (live conclusion drives decisions).
            # On non-ALERT live updates, this stays a critical warning.
            if is_replay or topic_classification == "ALERT":
                results["checks"]["conclusion_sensitive"] = {
                    "passed": False, "detail": detail + (" [replay: hard block]" if is_replay else ""),
                }
                results["failures"].append("conclusion_sensitive")
                results["passed"] = False
            else:
                severity = "CRITICAL" if lr_confidence == "LOW" else "WARNING"
                results["checks"]["conclusion_sensitive"] = {
                    "passed": False,
                    "detail": f"{detail} [{severity}: lr_confidence={lr_confidence}]",
                }
                results["warnings"].append("conclusion_sensitive")
        elif width > 0.20:
            results["checks"]["wide_uncertainty"] = {
                "passed": False,
                "detail": f"Posterior range width {width:.2f} > 0.20 — LR estimates need grounding",
            }
            results["warnings"].append("wide_uncertainty")
        else:
            results["checks"]["conclusion_sensitive"] = {"passed": True}

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

    # 11. Red Team Override / Saturation Gate — HARD BLOCKER on saturation path.
    #
    # Two distinct checks:
    #   (a) If a red-team has been run AND devil-advocate score is high, the
    #       proposed update is dishonest about the counter-evidence — block.
    #   (b) If the proposed posterior would push max(H) past the saturation
    #       threshold (0.85) on a non-RESOLVED topic, require that a redTeam
    #       entry exists in the recent history (within 30 days). No red-team
    #       on file → block. Forces the procedural step before any topic
    #       claims near-certainty.
    SATURATION_THRESHOLD = 0.85
    REDTEAM_FRESHNESS_DAYS = 30
    try:
        history = topic["model"].get("posteriorHistory", [])
        topic_status = topic.get("meta", {}).get("status", "ACTIVE")
        proposed_max = max(proposed_posteriors.values()) if proposed_posteriors else 0.0

        # (a) Existing red-team with high devil-advocate score
        if history and "redTeam" in history[-1]:
            rt_score = history[-1]["redTeam"].get("devil_advocate_score", 0)
            if rt_score > 0.7:
                results["checks"]["red_team_override"] = {
                    "passed": False,
                    "detail": (f"Devil's advocate score {rt_score:.2f} — "
                               f"strong counterevidence: "
                               f"{history[-1]['redTeam'].get('challenge', '')[:100]}"),
                }
                results["failures"].append("red_team_override")
                results["passed"] = False
            else:
                results["checks"]["red_team_override"] = {"passed": True}
        else:
            results["checks"]["red_team_override"] = {"passed": True}

        # (b) Saturation gate — must have a recent red-team to push past 0.85
        if (topic_status not in ("RESOLVED", "ARCHIVED")
                and proposed_max > SATURATION_THRESHOLD):
            recent_redteam = False
            cutoff = datetime.now(timezone.utc).timestamp() - REDTEAM_FRESHNESS_DAYS * 86400
            for entry in reversed(history):
                rt = entry.get("redTeam")
                if not rt:
                    continue
                ts = (entry.get("timestamp") or entry.get("time")
                      or rt.get("timestamp") or entry.get("date"))
                if not ts:
                    continue
                try:
                    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    continue
                if t >= cutoff:
                    recent_redteam = True
                    break
            if not recent_redteam:
                results["checks"]["saturation_redteam_required"] = {
                    "passed": False,
                    "detail": (f"Proposed max posterior {proposed_max:.0%} > "
                               f"{int(SATURATION_THRESHOLD*100)}% saturation "
                               f"threshold, but no red-team result on file within "
                               f"{REDTEAM_FRESHNESS_DAYS} days. "
                               f"Run skills/red-team.md and record the result on "
                               f"the next posteriorHistory entry before saturating."),
                }
                results["failures"].append("saturation_redteam_required")
                results["passed"] = False
            else:
                results["checks"]["saturation_redteam_required"] = {"passed": True}
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
        from engine import extract_posteriors as _ep
        prev_p = _ep(prev, [hypothesis_key])
        curr_p = _ep(curr, [hypothesis_key])
        prev_val = prev_p.get(hypothesis_key, 0)
        curr_val = curr_p.get(hypothesis_key, 0)
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
    kl_prior = compute_kl_from_prior(topic)
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
        issues.append("CRITICAL: Near-zero uncertainty on ACTIVE topic — epistemic overconfidence. Time horizons and unresolved indicators contradict certainty.")
    # Check for point-mass posteriors (any H at 0.0 or 1.0 on active topic)
    topic_status = topic.get("meta", {}).get("status", "ACTIVE")
    if topic_status != "RESOLVED":
        _hyps = topic.get("model", {}).get("hypotheses", {})
        point_mass = [k for k, h in _hyps.items() if h.get("posterior", 0) in (0.0, 1.0)]
        if point_mass:
            issues.append(f"CRITICAL: Point-mass posteriors on ACTIVE topic ({', '.join(point_mass)}). Certainty requires RESOLVED status.")

    if kl_prior["interpretation"] == "PRIOR_DOMINATED":
        issues.append("Posterior may be prior-dominated (low KL from initial prior)")

    # Drift-flagged indicators surface from migration: they have LRs derived
    # from inflated posteriors and should be regrounded before next firing.
    try:
        from framework.lint import list_drift_flagged_indicators
        drift_inds = list_drift_flagged_indicators(topic)
        if drift_inds:
            ids = [d["indicator_id"] for d in drift_inds]
            issues.append(
                f"DRIFT-FLAGGED indicators ({len(drift_inds)}) need regrounding "
                f"before next firing: {', '.join(ids[:5])}"
                + (f" (+{len(ids)-5} more)" if len(ids) > 5 else "")
            )
    except ImportError:
        pass

    # Topic-level resolution date signals the whole topic is due for resolution.
    # Does NOT auto-resolve; operator must run skills/resolve.md to pick a winner.
    resolution_date = topic.get("meta", {}).get("resolutionDate")
    if resolution_date and topic_status not in ("RESOLVED", "ARCHIVED"):
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if str(resolution_date)[:10] < now_str:
            issues.append(
                f"CRITICAL: Topic past resolutionDate ({resolution_date}) but still "
                f"{topic_status} — run skills/resolve.md to record the winning "
                f"hypothesis and lock posteriors."
            )

    health = "HEALTHY" if len(issues) == 0 else "DEGRADED" if len(issues) <= 2 else "CRITICAL"

    alerts = build_actionable_alerts(
        topic,
        health=health,
        uncertainty=uncertainty,
        freshness=freshness,
        rt=rt,
        kl_prior=kl_prior,
    )

    return {
        "health": health,
        "issues": issues,
        "alerts": alerts,
        "rt": rt,
        "entropy": entropy,
        "max_entropy": max_entropy,
        "uncertainty_ratio": uncertainty,
        "kl_from_prior": kl_prior,
        "evidence_freshness": freshness,
        "hypothesis_admissibility": admissibility,
        "top_queries": queries[:5],
        "failure_modes": [m["id"] for m in FAILURE_MODES],
    }


# ===========================================================================
# 8b. ACTIONABLE ALERTS
#
#   Translate raw governance issues into human-legible alerts with a clear
#   lead, a call-to-action, and supporting details. Also detects the
#   "high-relevance evidence filed as NONE" pattern — a structural blind
#   spot where the tier system has no indicator for a dimension the
#   evidence is probing.
# ===========================================================================

_HIGH_RELEVANCE_TAGS = {"DATA", "EVENT", "INTEL", "ACTION", "KINETIC", "MIL", "MILITARY", "ECON", "DIPLO", "FORCE"}
_NONE_WINDOW_DAYS = 7
_NONE_ALERT_THRESHOLD = 3


def _find_none_impact_high_relevance(topic: dict, days: int = _NONE_WINDOW_DAYS) -> list:
    """Find recent high-relevance evidence filed with posteriorImpact=NONE."""
    now = datetime.now(timezone.utc)
    out = []
    for e in topic.get("evidenceLog", []) or []:
        if not isinstance(e, dict):
            continue
        imp = (e.get("posteriorImpact") or "").strip()
        if not imp.upper().startswith("NONE"):
            continue
        # Schema split: read both tag (singular) and tags (plural).
        tags = e.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        single = e.get("tag")
        if single:
            tags = list(tags) + [single]
        if not any(t in _HIGH_RELEVANCE_TAGS for t in tags):
            continue
        ts = e.get("time") or ""
        try:
            t_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t_dt.tzinfo is None:
                t_dt = t_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        age_days = (now - t_dt).total_seconds() / 86400.0
        if 0 <= age_days <= days:
            out.append({
                "id": e.get("id"),
                "age_days": round(age_days, 1),
                "tags": tags,
                "text": (e.get("text") or "")[:160],
            })
    return out


def build_actionable_alerts(topic: dict, *, health: str, uncertainty: float,
                            freshness: dict, rt: dict, kl_prior: dict) -> list:
    """Translate governance signals into lead/action/details alerts."""
    slug = topic.get("meta", {}).get("slug") or topic.get("meta", {}).get("title") or "?"
    status = topic.get("meta", {}).get("status", "ACTIVE")
    alerts = []

    # Primary: high-relevance NONE-impact evidence on a saturated/overconfident topic.
    # This is the structural blind spot: tier system has no indicator for the
    # dimension the evidence is probing, so the signal gets filed as inert.
    if status not in ("RESOLVED", "ARCHIVED"):
        none_hits = _find_none_impact_high_relevance(topic)
        saturated = uncertainty < 0.1
        if len(none_hits) >= _NONE_ALERT_THRESHOLD and (saturated or health == "CRITICAL"):
            ev_ids = [h["id"] for h in none_hits if h["id"]]
            sample_ids = ", ".join(ev_ids[:5]) + (f" (+{len(ev_ids)-5} more)" if len(ev_ids) > 5 else "")
            alerts.append({
                "severity": "REVIEW_NEEDED",
                "slug": slug,
                "signature": f"none_impact_saturation:{slug}",
                "lead": (
                    f"High-relevance evidence is being logged but not changing the model. "
                    f"{len(none_hits)} recent entries on {slug} are filed as \"no impact\" "
                    f"because no pre-registered indicator matched them — "
                    f"while the posterior is saturated (uncertainty ratio "
                    f"{uncertainty:.2f}), meaning the model can't absorb further signal "
                    f"even if warranted."
                ),
                "action": (
                    "1. Read the listed evidence entries. Decide if they represent a "
                    "dimension the current indicator tiers don't cover.\n"
                    "2. If yes: run skills/topic-design.md to add the missing "
                    "indicator(s) with pre-committed posteriorEffect, then replay.\n"
                    "3. If no: mark them reviewed and move on; the flag will clear."
                ),
                "buttons": [
                    {"label": "Review topic design", "action": "review_topic_design",
                     "primary": True,
                     "context": {"evidence_ids": ev_ids}},
                    {"label": "Mark reviewed", "action": "mark_reviewed",
                     "context": {"alert_signature": f"none_impact_saturation:{slug}"}},
                ],
                "details": {
                    "evidence_ids": ev_ids,
                    "evidence_sample": sample_ids,
                    "window_days": _NONE_WINDOW_DAYS,
                    "uncertainty_ratio": round(uncertainty, 3),
                    "health": health,
                },
            })

    # Secondary: overconfidence without the NONE-impact signal (rare, but possible).
    if status not in ("RESOLVED", "ARCHIVED") and uncertainty < 0.1 and not alerts:
        alerts.append({
            "severity": "REVIEW_NEEDED",
            "slug": slug,
            "signature": f"overconfidence:{slug}",
            "lead": (
                f"Posterior on {slug} is saturated (uncertainty ratio "
                f"{uncertainty:.2f}) but the topic is still ACTIVE. "
                f"Model confidence exceeds what outstanding indicators and time "
                f"horizons justify."
            ),
            "action": (
                "Run skills/red-team.md against the current posterior. If no "
                "genuine counter-evidence surfaces, consider whether the topic "
                "is effectively resolved and should move to RESOLVED status via "
                "skills/resolve.md."
            ),
            "buttons": [
                {"label": "Run red-team", "action": "run_red_team", "primary": True, "context": {}},
                {"label": "Mark reviewed", "action": "mark_reviewed",
                 "context": {"alert_signature": f"overconfidence:{slug}"}},
            ],
            "details": {"uncertainty_ratio": round(uncertainty, 3), "health": health},
        })

    # Stale evidence dominance
    if freshness.get("stale", 0) > freshness.get("fresh", 0) and freshness.get("total", 0) >= 20:
        alerts.append({
            "severity": "ATTENTION",
            "slug": slug,
            "signature": f"stale_evidence:{slug}",
            "lead": (
                f"Evidence base on {slug} is mostly stale "
                f"({freshness['stale']}/{freshness['total']} past TTL). "
                f"Posterior is increasingly based on aged observations."
            ),
            "action": (
                "Run skills/triage.md on recent headlines to refresh the "
                "evidence base, or run skills/news-scan.md for a sweep."
            ),
            "buttons": [
                {"label": "Mark reviewed", "action": "mark_reviewed",
                 "context": {"alert_signature": f"stale_evidence:{slug}"}},
            ],
            "details": {
                "stale": freshness["stale"],
                "fresh": freshness["fresh"],
                "total": freshness["total"],
            },
        })

    # R_t regime danger
    regime = rt.get("regime", "")
    if regime in ("DANGEROUS", "RUNAWAY"):
        alerts.append({
            "severity": "ATTENTION",
            "slug": slug,
            "signature": f"rt_regime:{slug}:{regime}",
            "lead": (
                f"R_t on {slug} is {regime} ({rt.get('rt', 0):.2f}). Evidence "
                f"base is aging faster than it is being refreshed; posterior "
                f"drift risk is elevated."
            ),
            "action": "Run skills/news-scan.md or skills/triage.md to add fresh observations.",
            "buttons": [
                {"label": "Mark reviewed", "action": "mark_reviewed",
                 "context": {"alert_signature": f"rt_regime:{slug}:{regime}"}},
            ],
            "details": {"rt": rt.get("rt"), "regime": regime, "worst_hypothesis": rt.get("worst_hypothesis")},
        })

    # Filter against operator-reviewed suppressions. A suppression with a
    # fingerprint only suppresses when the alert's current fingerprint matches
    # — versioned alerts (e.g. none_impact_saturation) re-fire when underlying
    # content changes. A suppression without a fingerprint suppresses
    # unconditionally on signature match.
    reviewed = topic.get("governance", {}).get("reviewed_alerts", []) or []
    if reviewed and alerts:
        from engine import compute_alert_fingerprint
        suppressions = {r.get("signature"): r for r in reviewed if r.get("signature")}
        kept = []
        for a in alerts:
            sup = suppressions.get(a.get("signature"))
            if sup is None:
                kept.append(a)
                continue
            sup_fp = sup.get("fingerprint")
            if sup_fp is None:
                # unconditional suppression
                continue
            cur_fp = compute_alert_fingerprint(a)
            if cur_fp == sup_fp:
                continue
            # content drifted since review — re-fire
            a = dict(a)
            a["details"] = dict(a.get("details") or {})
            a["details"]["suppression_drifted"] = True
            a["details"]["reviewed_fingerprint"] = sup_fp
            a["details"]["current_fingerprint"] = cur_fp
            kept.append(a)
        alerts = kept

    return alerts


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
_PREDICTIVE_TAGS = {"RHETORIC", "INTEL", "ANALYSIS", "EDITORIAL", "FORECAST"}


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
