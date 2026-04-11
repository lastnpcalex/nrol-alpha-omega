#!/usr/bin/env python3
"""
NRL-Alpha Omega — Red Team (Devil's Advocate) System
=======================================================

For each proposed posterior shift, finds the strongest counterevidence
and builds a contrarian case. Used as a pre-commit sanity check before
accepting posterior updates.

Three functions drive the analysis:
  generate_red_team()        — full contrarian report for a set of proposed posteriors
  score_counterevidence()    — rank evidence arguing against a given direction
  compute_devil_advocate_score() — 0-1 strength of the counter-case

No external dependencies — Python stdlib only.

Usage:
    from framework.red_team import generate_red_team, format_red_team_challenge

    red = generate_red_team(topic, proposed_posteriors)
    print(format_red_team_challenge(red))
"""

import re
import sys
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECENCY_WINDOW_DAYS = 7      # entries within this window get 2x weight
RECENCY_MULTIPLIER = 2.0     # boost for recent entries
SHIFT_THRESHOLD = 0.001      # ignore shifts smaller than this

# String posteriorImpact → numeric magnitude for heuristic inference
IMPACT_MAGNITUDE = {
    "MAJOR": 0.10,
    "MODERATE": 0.05,
    "MINOR": 0.02,
    "NONE": 0.0,
}

# ---------------------------------------------------------------------------
# Tag direction hints — topic-configurable with fallback defaults
# ---------------------------------------------------------------------------
# These map tags to per-hypothesis direction hints. Positive = argues for
# "longer/more/worse" hypotheses, negative = "shorter/less/better".
#
# Topics can override via topic["tagConfig"]["directionHints"].
# The defaults below are for conflict/geopolitical topics (the original
# use case). For other topic types, use get_direction_hints(topic) which
# checks the topic first, then falls back.

# Conflict / Geopolitical default (Hormuz, wars, crises)
_CONFLICT_DIRECTION_HINTS = {
    "KINETIC": {"H3": +1, "H4": +1, "H2": -1},
    "FORCE":   {"H3": +1, "H4": +1, "H2": -1},
    "DIPLO":   {"H2": +1, "H1": +1, "H3": -1},
    "ECON":    {"H3": +1, "H4": +1},
    "EVENT":   {},
    "RHETORIC": {},
    "INTEL":   {},
    "ANALYSIS": {},
    "DATA":    {},
    "OSINT":   {},
}

# Science / Replication default (LK-99, cold fusion, etc.)
_SCIENCE_DIRECTION_HINTS = {
    "EXPERIMENTAL": {"H1": +1, "H2": +1},  # lab results push toward confirmation
    "SCIENTIFIC":   {"H1": +1, "H2": +1},  # papers push toward confirmation
    "TECHNICAL":    {"H1": +1},             # engineering details confirm feasibility
    "EDITORIAL":    {},                      # ignore
    "RHETORIC":     {},                      # ignore
    "DATA":         {},                      # neutral — depends on content
    "EVENT":        {},
    "CORPORATE":    {},
}

# Election / Political default
_ELECTION_DIRECTION_HINTS = {
    "POLL":       {},    # neutral — direction from numbers
    "POLITICAL":  {},    # neutral
    "LEGAL":      {},    # neutral — court rulings can go either way
    "JUDICIAL":   {},
    "LEGISLATIVE": {},
    "CORPORATE":  {},    # endorsements
    "SOCIAL":     {},    # protests, movements
    "RHETORIC":   {},    # ignore
    "DATA":       {},
    "EVENT":      {},
}

# AI / Technology default
_TECH_DIRECTION_HINTS = {
    "TECHNICAL":   {"H1": +1, "H2": +1},  # benchmarks/demos push toward "sooner"
    "SCIENTIFIC":  {"H1": +1},             # papers confirm feasibility
    "CORPORATE":   {},                      # neutral — depends on content
    "REGULATORY":  {"H3": +1, "H4": +1},  # regulation slows things down
    "MARKET":      {},                      # neutral
    "RHETORIC":    {},
    "DATA":        {},
    "EVENT":       {},
}

# Presets by topic type
DIRECTION_HINT_PRESETS = {
    "conflict": _CONFLICT_DIRECTION_HINTS,
    "science": _SCIENCE_DIRECTION_HINTS,
    "election": _ELECTION_DIRECTION_HINTS,
    "tech": _TECH_DIRECTION_HINTS,
}

# Fallback (used when no topic is available)
TAG_DIRECTION_HINTS = _CONFLICT_DIRECTION_HINTS


def get_direction_hints(topic: dict | None = None) -> dict:
    """
    Get tag direction hints, checking topic config first.

    Priority:
    1. topic["tagConfig"]["directionHints"] (explicit per-topic overrides)
    2. DIRECTION_HINT_PRESETS[topic["meta"]["topicType"]] (preset for topic type)
    3. TAG_DIRECTION_HINTS (conflict default)
    """
    if topic is None:
        return TAG_DIRECTION_HINTS

    # Check for explicit overrides
    tc = topic.get("tagConfig", {})
    if "directionHints" in tc:
        return tc["directionHints"]

    # Check for topic type preset
    topic_type = topic.get("meta", {}).get("topicType", "")
    if topic_type in DIRECTION_HINT_PRESETS:
        return DIRECTION_HINT_PRESETS[topic_type]

    return TAG_DIRECTION_HINTS


# Escalation/de-escalation tag sets — also topic-configurable
_DEFAULT_ESCALATION_TAGS = {"KINETIC", "FORCE", "ECON"}
_DEFAULT_DEESCALATION_TAGS = {"DIPLO"}


def get_escalation_tags(topic: dict | None = None) -> tuple:
    """Return (escalation_tags, deescalation_tags) for a topic."""
    if topic is not None:
        tc = topic.get("tagConfig", {})
        esc = tc.get("escalationTags")
        deesc = tc.get("deescalationTags")
        if esc is not None or deesc is not None:
            return (set(esc or []), set(deesc or []))
    return (_DEFAULT_ESCALATION_TAGS, _DEFAULT_DEESCALATION_TAGS)


# Text keywords for inferring direction when posteriorImpact is a string.
# Universal — these work across most topic types.
DIRECTION_KEYWORDS = {
    "positive": ("ceasefire", "peace", "agreement", "withdrawal", "resumed",
                 "reopened", "de-escalat", "negoti", "diplomacy", "talks",
                 "convoy", "escort", "deal", "confirmed", "replicated",
                 "verified", "passed", "approved", "breakthrough", "success",
                 "resolved", "settled", "won", "achieved", "demonstrated"),
    "negative": ("escalat", "attack", "strike", "destroy", "blockade",
                 "mine", "seized", "offensive", "expand", "toll", "closed",
                 "closure", "fortif", "missile", "drone lost", "failed",
                 "retracted", "debunked", "rejected", "denied", "collapsed",
                 "fraud", "fabricat", "unreplicable", "defeated", "stalled"),
}


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

def _parse_time(ts: str) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _is_recent(ts: str, days: int = RECENCY_WINDOW_DAYS) -> bool:
    """True if timestamp falls within the last `days` days."""
    dt = _parse_time(ts)
    if dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return dt >= cutoff


def _recency_weight(ts: str) -> float:
    """Return RECENCY_MULTIPLIER if recent, else 1.0."""
    return RECENCY_MULTIPLIER if _is_recent(ts) else 1.0


# ---------------------------------------------------------------------------
# Evidence analysis helpers
# ---------------------------------------------------------------------------

def _get_posterior_impact(entry: dict, topic: dict | None = None) -> dict:
    """
    Extract posteriorImpact as {hypothesis_key: float}.

    Handles both dict format (explicit) and string format
    (MAJOR/MODERATE/MINOR/NONE) via text heuristic inference.
    When using heuristics, direction is inferred from tag + text keywords.

    If topic is provided, uses topic-specific direction hints and
    escalation/de-escalation tag sets. Otherwise falls back to defaults.
    """
    impact = entry.get("posteriorImpact")
    if isinstance(impact, dict):
        return impact

    # String fallback
    if not isinstance(impact, str):
        return {}

    # Try parsing explicit "H1 +5pp, H3 -3pp" format first
    explicit_signals = {}
    for match in re.finditer(r"(H\d+)\s*([+-])\s*(\d+)\s*pp", impact):
        h = match.group(1)
        sign = 1 if match.group(2) == "+" else -1
        magnitude_pp = int(match.group(3)) / 100.0  # convert pp to decimal
        explicit_signals[h] = sign * magnitude_pp
    if explicit_signals:
        return explicit_signals

    # Fall back to MAJOR/MODERATE/MINOR/NONE + text keyword heuristic
    magnitude = IMPACT_MAGNITUDE.get(impact.upper(), 0.0)
    if magnitude == 0.0:
        return {}

    tag = entry.get("tag", "")
    text_lower = entry.get("text", "").lower()

    # Count keyword hits for direction inference
    pos_score = sum(1 for kw in DIRECTION_KEYWORDS["positive"] if kw in text_lower)
    neg_score = sum(1 for kw in DIRECTION_KEYWORDS["negative"] if kw in text_lower)

    # Get tag-based direction hints (topic-aware)
    hints_dict = get_direction_hints(topic)
    hints = hints_dict.get(tag, {})

    if not hints and pos_score == 0 and neg_score == 0:
        return {}  # Cannot infer direction — no signal

    # Determine text-based direction: positive keywords → shorter timeline (-1),
    # negative keywords → longer timeline (+1)
    if pos_score > neg_score:
        text_direction = -1
    elif neg_score > pos_score:
        text_direction = +1
    else:
        text_direction = 0  # ambiguous — use tag hints only

    # Tags that normally indicate escalation vs de-escalation (topic-aware)
    _ESCALATION_TAGS, _DEESCALATION_TAGS = get_escalation_tags(topic)

    result = {}
    if hints:
        for h_key, base_dir in hints.items():
            # Tag hints provide per-hypothesis direction.
            # Text keywords can FLIP hints if they contradict the tag's nature:
            #   KINETIC + positive text (ceasefire) → flip escalation hints
            #   DIPLO + negative text (talks fail) → flip de-escalation hints
            if text_direction != 0:
                if tag in _ESCALATION_TAGS and text_direction < 0:
                    direction = -base_dir  # positive text contradicts escalation tag
                elif tag in _DEESCALATION_TAGS and text_direction > 0:
                    direction = -base_dir  # negative text contradicts diplomacy tag
                else:
                    direction = base_dir  # text confirms tag's natural direction
            else:
                direction = base_dir
            result[h_key] = direction * magnitude
    elif text_direction != 0:
        # No tag hints but we have keyword direction — apply generically
        result = {
            "H1": -text_direction * magnitude,
            "H2": -text_direction * magnitude * 0.5,
            "H3": text_direction * magnitude,
            "H4": text_direction * magnitude * 0.5,
        }

    return result


def _entry_argues_direction(entry: dict, hypothesis_key: str,
                            topic: dict | None = None) -> float | None:
    """
    Return the direction this entry argues for the given hypothesis.
    Positive = argues UP, negative = argues DOWN, None = no opinion.
    """
    impact = _get_posterior_impact(entry, topic=topic)
    return impact.get(hypothesis_key)


def _effective_weight(entry: dict) -> float:
    """Get the effective weight of an evidence entry, defaulting to 0.5."""
    w = entry.get("effectiveWeight")
    if isinstance(w, (int, float)) and not math.isnan(w):
        return max(0.0, min(1.0, float(w)))
    return 0.5


def _claim_state(entry: dict) -> str:
    """Get the claim state, defaulting to PROPOSED."""
    return entry.get("claimState", "PROPOSED")


# Claim states that weaken evidence (contested / invalidated / retracted)
_WEAK_STATES = {"CONTESTED", "INVALIDATED", "RETRACTED", "SUPERSEDED"}

# Claim states that strengthen evidence
_STRONG_STATES = {"SUPPORTED", "OBSERVED", "CONFIRMED"}


# ---------------------------------------------------------------------------
# Core: score_counterevidence
# ---------------------------------------------------------------------------

def score_counterevidence(topic: dict, hypothesis_key: str,
                          direction: str, scan_compacted: bool = True) -> list:
    """
    Find and rank evidence entries arguing against the given direction
    for a specific hypothesis.

    Args:
        topic: full topic dict
        hypothesis_key: e.g. "H1", "H3"
        direction: "UP" or "DOWN" — the proposed shift direction
        scan_compacted: if True, also scan key_claims from compactedEvidence

    Returns:
        Sorted list (strongest first) of dicts.

    Strategy:
      - If direction is UP: find entries with negative posteriorImpact for this
        hypothesis, or entries with CONTESTED/INVALIDATED claim states that the
        shift might rely on. Also find entries supporting competing hypotheses.
      - If direction is DOWN: find recent SUPPORTED/OBSERVED entries that argue
        UP for this hypothesis (i.e., evidence the shift ignores).
    """
    evidence_log = list(topic.get("evidenceLog", []))

    # Extend with compacted key_claims for fuller counter-case analysis
    if scan_compacted:
        for compact_rec in topic.get("compactedEvidence", []):
            for kc in compact_rec.get("key_claims", []):
                evidence_log.append(kc)

    counter = []

    for idx, entry in enumerate(evidence_log):
        impact = _get_posterior_impact(entry, topic=topic)
        entry_direction = impact.get(hypothesis_key)
        ew = _effective_weight(entry)
        recency = _recency_weight(entry.get("time", ""))
        cs = _claim_state(entry)

        scored_weight = 0.0
        argues_for = None

        if direction == "UP":
            # --- Counter: evidence that argues DOWN for this hypothesis ---
            if entry_direction is not None and entry_direction < 0:
                scored_weight = ew * recency * abs(entry_direction)
                argues_for = _find_beneficiary(impact, hypothesis_key)

            # Evidence supporting this hypothesis but in a weak claim state
            # (the UP shift may rely on contested/invalidated evidence)
            elif entry_direction is not None and entry_direction > 0 and cs in _WEAK_STATES:
                scored_weight = ew * recency * entry_direction * 0.5
                argues_for = hypothesis_key + " (weakened)"

            # Evidence that strongly supports a competing hypothesis
            elif entry_direction is None or entry_direction == 0:
                best_competitor = _strongest_competitor(impact, hypothesis_key)
                if best_competitor is not None:
                    comp_key, comp_val = best_competitor
                    if comp_val > 0:
                        scored_weight = ew * recency * comp_val * 0.3
                        argues_for = comp_key

        elif direction == "DOWN":
            # --- Counter: recent evidence that argues UP (shift ignores it) ---
            if entry_direction is not None and entry_direction > 0:
                # Only count if claim state is strong
                if cs in _STRONG_STATES:
                    scored_weight = ew * recency * entry_direction
                    argues_for = hypothesis_key
                else:
                    scored_weight = ew * recency * entry_direction * 0.3
                    argues_for = hypothesis_key + f" ({cs})"

        if scored_weight > 0.01 and argues_for is not None:
            counter.append({
                "index": idx,
                "text": entry.get("text", "")[:200],
                "weight": round(scored_weight, 4),
                "argues_for": argues_for,
                "claim_state": cs,
                "time": entry.get("time", ""),
            })

    # Sort by weight descending
    counter.sort(key=lambda x: x["weight"], reverse=True)
    return counter


def _find_beneficiary(impact: dict, excluded_key: str) -> str:
    """Find the hypothesis that benefits most from this entry, excluding one."""
    best_key = None
    best_val = 0.0
    for k, v in impact.items():
        if k == excluded_key:
            continue
        if isinstance(v, (int, float)) and v > best_val:
            best_val = v
            best_key = k
    return best_key or "competing"


def _strongest_competitor(impact: dict, excluded_key: str) -> tuple | None:
    """Find the hypothesis with the largest positive impact, excluding one."""
    best = None
    best_val = 0.0
    for k, v in impact.items():
        if k == excluded_key:
            continue
        if isinstance(v, (int, float)) and v > best_val:
            best_val = v
            best = (k, v)
    return best


# ---------------------------------------------------------------------------
# Core: compute_devil_advocate_score
# ---------------------------------------------------------------------------

def compute_devil_advocate_score(counterevidence: list,
                                 proposed_shift: float) -> float:
    """
    Compute the strength of the counter-case relative to the proposal.

    0 = no counter-case at all
    1 = counter-case as strong as (or stronger than) the proposal

    Formula:
        counter_sum = sum of counterevidence weights
        score = counter_sum / (counter_sum + abs(proposed_shift) * scale_factor)

    The scale_factor (10.0) normalizes so that a typical shift of 0.05
    with moderate counterevidence lands in a meaningful range.
    """
    if not counterevidence:
        return 0.0

    counter_sum = sum(e["weight"] for e in counterevidence)
    shift_magnitude = abs(proposed_shift) if proposed_shift != 0 else 0.001

    # Scale factor: a shift of 0.05 with counterevidence summing to 0.5
    # should score ~0.5 (even match). 10x normalizes the shift into the
    # same magnitude as summed counterevidence weights.
    SCALE = 10.0
    denominator = counter_sum + shift_magnitude * SCALE

    if denominator == 0:
        return 0.0

    return round(min(1.0, counter_sum / denominator), 4)


# ---------------------------------------------------------------------------
# Core: generate_red_team
# ---------------------------------------------------------------------------

def generate_red_team(topic: dict, proposed_posteriors: dict) -> dict:
    """
    Full red-team analysis for a set of proposed posterior values.

    Args:
        topic: full topic dict (with current posteriors in topic["model"]["hypotheses"])
        proposed_posteriors: dict of {hypothesis_key: proposed_new_posterior}
            e.g. {"H1": 0.01, "H2": 0.20, "H3": 0.50, "H4": 0.25, "H5": 0.04}

    Returns:
        {
            "contrarian_direction": {key: float, ...},
            "counterevidence": [...top entries across all hypotheses...],
            "unfired_indicators": [...],
            "devil_advocate_score": float,
            "challenge": str,
        }
    """
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    indicators = topic.get("indicators", {}).get("tiers", {})

    contrarian = {}
    all_counter = []
    per_hypothesis_scores = []

    for h_key, h_data in hypotheses.items():
        current = h_data.get("posterior", 0.0)
        proposed = proposed_posteriors.get(h_key)
        if proposed is None:
            continue

        shift = proposed - current
        if abs(shift) < SHIFT_THRESHOLD:
            continue

        direction = "UP" if shift > 0 else "DOWN"
        # Contrarian direction: argue the opposite
        contrarian[h_key] = round(-shift, 6)

        # Gather counterevidence
        counter = score_counterevidence(topic, h_key, direction)
        for c in counter:
            c["target_hypothesis"] = h_key
            c["proposed_direction"] = direction
        all_counter.extend(counter)

        # Per-hypothesis devil's advocate score
        da_score = compute_devil_advocate_score(counter, shift)
        per_hypothesis_scores.append((h_key, da_score, abs(shift)))

    # Sort all counterevidence by weight, take top entries
    all_counter.sort(key=lambda x: x["weight"], reverse=True)
    top_counter = all_counter[:15]

    # Find unfired indicators that would support the contrarian case
    unfired = _find_unfired_indicators(indicators, hypotheses,
                                       proposed_posteriors, contrarian)

    # Aggregate devil's advocate score: weighted average by shift magnitude
    total_shift = sum(s for _, _, s in per_hypothesis_scores)
    if total_shift > 0 and per_hypothesis_scores:
        agg_score = sum(score * mag for _, score, mag in per_hypothesis_scores) / total_shift
    else:
        agg_score = 0.0
    agg_score = round(agg_score, 4)

    # Generate challenge paragraph
    challenge = _build_challenge(contrarian, top_counter, unfired, agg_score,
                                 hypotheses, proposed_posteriors)

    # Check if inference was used (any entry had string posteriorImpact)
    has_string_impact = any(
        isinstance(e.get("posteriorImpact"), str)
        for e in topic.get("evidenceLog", [])
    )

    return {
        "contrarian_direction": contrarian,
        "counterevidence": top_counter,
        "unfired_indicators": unfired,
        "devil_advocate_score": agg_score,
        "challenge": challenge,
        "inference_mode": "text_heuristic" if has_string_impact else "explicit",
    }


# ---------------------------------------------------------------------------
# Indicator analysis
# ---------------------------------------------------------------------------

def _find_unfired_indicators(tiers: dict, hypotheses: dict,
                             proposed: dict, contrarian: dict) -> list:
    """
    Find indicators that SHOULD have fired if the proposed shift were correct,
    but haven't. These support the contrarian case.

    Checks tier1, tier2, tier3 indicator lists. An indicator with status
    NOT_FIRED or PENDING that would be expected to fire for a hypothesis
    shifting UP is a red flag.
    """
    unfired = []

    for tier_key in ("tier1", "tier2", "tier3"):
        tier_list = tiers.get(tier_key, [])
        if isinstance(tier_list, list):
            for ind in tier_list:
                status = ind.get("status", "")
                if status in ("NOT_FIRED", "PENDING"):
                    desc = ind.get("desc", ind.get("description", ind.get("label", "")))
                    # Try to figure out which hypothesis this indicator relates to
                    expected_effect = _infer_indicator_effect(ind, hypotheses, proposed)
                    if expected_effect:
                        unfired.append({
                            "tier": tier_key,
                            "desc": desc[:200],
                            "expected_effect": expected_effect,
                            "status": status,
                        })

    return unfired


def _infer_indicator_effect(indicator: dict, hypotheses: dict,
                            proposed: dict) -> str | None:
    """
    Infer which hypothesis a NOT_FIRED indicator would support.

    If the indicator has an explicit 'hypothesis' or 'affects' field, use that.
    Otherwise, check the description against hypothesis labels for keyword overlap.
    Returns a string like "H3 surge" or None if no connection found.
    """
    # Check for explicit linkage
    linked = indicator.get("hypothesis") or indicator.get("affects")
    if linked and isinstance(linked, str):
        if linked in proposed:
            return f"{linked} shift"

    # Keyword matching against hypothesis labels
    desc = (indicator.get("desc", "") + " " +
            indicator.get("description", "") + " " +
            indicator.get("label", "")).lower()

    for h_key, h_data in hypotheses.items():
        h_label = h_data.get("label", "").lower()
        # Check if indicator description mentions this hypothesis's label terms
        label_words = set(h_label.split()) - {"the", "of", "a", "an", "in", "to"}
        desc_words = set(desc.split())
        overlap = label_words & desc_words
        if len(overlap) >= 1 and h_key in proposed:
            current = hypotheses[h_key].get("posterior", 0.0)
            shift = proposed[h_key] - current
            if abs(shift) > SHIFT_THRESHOLD:
                direction = "surge" if shift > 0 else "decline"
                return f"{h_key} {direction}"

    return None


# ---------------------------------------------------------------------------
# Challenge narrative
# ---------------------------------------------------------------------------

def _build_challenge(contrarian: dict, counterevidence: list,
                     unfired: list, score: float,
                     hypotheses: dict, proposed: dict) -> str:
    """Build a one-paragraph devil's advocate challenge."""
    if not contrarian:
        return "No significant shifts proposed; no challenge needed."

    parts = []

    # Identify the largest proposed shift
    biggest_key = max(contrarian, key=lambda k: abs(contrarian[k]))
    biggest_shift = -contrarian[biggest_key]  # undo the negation
    direction_word = "increase" if biggest_shift > 0 else "decrease"
    h_label = hypotheses.get(biggest_key, {}).get("label", biggest_key)

    parts.append(
        f"The strongest case against this update centers on {biggest_key} "
        f"({h_label}), proposed to {direction_word} by "
        f"{abs(biggest_shift):.3f}."
    )

    # Cite top counterevidence
    relevant_counter = [c for c in counterevidence
                        if c.get("target_hypothesis") == biggest_key]
    if relevant_counter:
        top = relevant_counter[0]
        parts.append(
            f"Counterevidence (weight {top['weight']:.2f}): "
            f"\"{top['text'][:120]}\" "
            f"argues for {top['argues_for']} instead."
        )
    elif counterevidence:
        top = counterevidence[0]
        parts.append(
            f"Strongest counterevidence overall (weight {top['weight']:.2f}): "
            f"\"{top['text'][:120]}\" "
            f"argues for {top['argues_for']}."
        )

    # Cite unfired indicators
    if unfired:
        ind = unfired[0]
        parts.append(
            f"Additionally, a {ind['tier']} indicator (\"{ind['desc'][:80]}\") "
            f"remains {ind['status']} despite the proposed shift, "
            f"which would be expected to affect {ind['expected_effect']}."
        )

    # Score summary
    if score >= 0.7:
        parts.append(
            f"Devil's advocate score: {score:.2f} (strong counter-case). "
            f"Recommend additional evidence before accepting this shift."
        )
    elif score >= 0.4:
        parts.append(
            f"Devil's advocate score: {score:.2f} (moderate counter-case). "
            f"The shift is defensible but not uncontested."
        )
    elif score > 0:
        parts.append(
            f"Devil's advocate score: {score:.2f} (weak counter-case). "
            f"The proposed shift is well-supported."
        )
    else:
        parts.append(
            "Devil's advocate score: 0.00. No meaningful counter-case found."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_red_team_challenge(red_team: dict) -> str:
    """
    Return a human-readable string summarizing the red team challenge.

    Suitable for inclusion in a brief or console output.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("RED TEAM CHALLENGE")
    lines.append("=" * 60)

    score = red_team.get("devil_advocate_score", 0.0)
    lines.append(f"Devil's Advocate Score: {score:.2f} / 1.00")
    lines.append("")

    # Contrarian directions
    contrarian = red_team.get("contrarian_direction", {})
    if contrarian:
        lines.append("Contrarian Position (opposite of proposed shifts):")
        for h_key, delta in sorted(contrarian.items()):
            sign = "+" if delta > 0 else ""
            lines.append(f"  {h_key}: {sign}{delta:.4f}")
        lines.append("")

    # Top counterevidence
    counter = red_team.get("counterevidence", [])
    if counter:
        lines.append(f"Top Counterevidence ({len(counter)} entries):")
        for i, c in enumerate(counter[:5]):
            target = c.get("target_hypothesis", "?")
            direction = c.get("proposed_direction", "?")
            lines.append(
                f"  [{i+1}] idx={c['index']} | w={c['weight']:.3f} | "
                f"vs {target} {direction} | state={c['claim_state']}"
            )
            lines.append(f"      \"{c['text'][:100]}\"")
            lines.append(f"      argues_for: {c['argues_for']}")
        lines.append("")

    # Unfired indicators
    unfired = red_team.get("unfired_indicators", [])
    if unfired:
        lines.append(f"Unfired Indicators ({len(unfired)}):")
        for ind in unfired[:5]:
            lines.append(
                f"  [{ind['tier']}] {ind['desc'][:80]} "
                f"({ind['status']}) -> {ind['expected_effect']}"
            )
        lines.append("")

    # Challenge paragraph
    challenge = red_team.get("challenge", "")
    if challenge:
        lines.append("Challenge:")
        # Word-wrap at ~76 chars
        words = challenge.split()
        line = "  "
        for w in words:
            if len(line) + len(w) + 1 > 76:
                lines.append(line)
                line = "  " + w
            else:
                line += (" " if len(line) > 2 else "") + w
        if line.strip():
            lines.append(line)

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
