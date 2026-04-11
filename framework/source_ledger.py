#!/usr/bin/env python3
"""
NRL-Alpha Omega — Source Ledger (Claim Resolution Tracker)
==========================================================

Tracks claim outcomes across the evidence log and adjusts source trust
scores via Bayesian updating. Works with the existing SOURCE_TRUST dict
in calibrate.py and stores calibration state inside the topic itself
at topic["sourceCalibration"].

Functions:
    extract_sources       — parse compound source strings into lists
    scan_for_resolutions  — find confirmation/refutation pairs in evidence
    resolve_claim         — record a resolution in the calibration ledger
    compute_effective_trust — Bayesian-updated trust for a source
    auto_calibrate        — full scan + resolve + trust update pipeline
"""

import sys
import re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from framework.calibrate import SOURCE_TRUST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_calibration(topic: dict) -> dict:
    """Ensure topic has sourceCalibration structure."""
    if "sourceCalibration" not in topic:
        topic["sourceCalibration"] = {"ledger": [], "effectiveTrust": {}}
    cal = topic["sourceCalibration"]
    if "ledger" not in cal:
        cal["ledger"] = []
    if "effectiveTrust" not in cal:
        cal["effectiveTrust"] = {}
    return topic


def _extract_nouns(text: str) -> set:
    """
    Extract significant nouns/tokens from evidence text for overlap comparison.
    Strips common filler words and returns lowercase tokens >= 4 chars.
    """
    stop_words = {
        "that", "this", "with", "from", "have", "been", "were", "will",
        "would", "could", "should", "about", "after", "before", "their",
        "there", "these", "those", "than", "then", "into", "also", "some",
        "more", "most", "very", "just", "only", "over", "such", "each",
        "which", "when", "what", "where", "while", "does", "doing",
        "being", "having", "between", "through", "during", "against",
        "since", "until", "update", "evidence", "reported", "reports",
        "according", "confirmed", "sources", "source", "note", "notes",
    }
    tokens = re.findall(r"[A-Za-z]{4,}", text.lower())
    return {t for t in tokens if t not in stop_words}


def _get_source_trust(source_name: str) -> float:
    """Look up base trust, trying exact match then case-insensitive."""
    if source_name in SOURCE_TRUST:
        return SOURCE_TRUST[source_name]
    for key, val in SOURCE_TRUST.items():
        if key.lower() == source_name.lower():
            return val
    return 0.50  # default for unknown sources


# ---------------------------------------------------------------------------
# 5) extract_sources
# ---------------------------------------------------------------------------

def extract_sources(source_string: str) -> list:
    """
    Parse compound source strings into individual source names.

    Examples:
        "AP+Guardian+Bloomberg cross-ref Apr 11" -> ["AP", "Guardian", "Bloomberg"]
        "Reuters/AP" -> ["Reuters", "AP"]
        "CENTCOM" -> ["CENTCOM"]
        "Al Jazeera + Mehr News" -> ["Al Jazeera", "Mehr News"]
        None -> []
    """
    if not source_string:
        return []

    # Strip trailing date/context info like "cross-ref Apr 11", "via X", etc.
    cleaned = re.sub(r"\b(cross-ref|via|per|citing)\b.*$", "", source_string, flags=re.IGNORECASE).strip()

    # Split on + or /
    parts = re.split(r"\s*[+/]\s*", cleaned)

    result = []
    for part in parts:
        part = part.strip()
        if part:
            result.append(part)

    return result


# ---------------------------------------------------------------------------
# 1) scan_for_resolutions
# ---------------------------------------------------------------------------

def scan_for_resolutions(topic: dict, scan_compacted: bool = False) -> list:
    """
    Scan the evidence log for confirmation/refutation pairs.

    For each entry, check all later entries:
    - If a later entry has claimState=SUPPORTED and shares significant noun
      overlap with an earlier entry, that's a CONFIRMATION (positive signal
      for the earlier source).
    - If a later entry has claimState in {CONTESTED, INVALIDATED}, that's
      a REFUTATION (negative signal).

    Same-source pairs are SKIPPED — a source cannot confirm itself.

    Args:
        scan_compacted: if True, also include key_claims from compactedEvidence

    Returns list of resolution dicts.
    """
    topic = _ensure_calibration(topic)
    evidence = list(topic.get("evidenceLog", []))

    # Optionally extend with compacted key_claims
    if scan_compacted:
        for compact_rec in topic.get("compactedEvidence", []):
            for kc in compact_rec.get("key_claims", []):
                evidence.append(kc)

    resolutions = []
    same_source_skipped = 0

    # Pre-extract nouns and decomposed sources for each entry
    noun_cache = []
    source_cache = []
    for entry in evidence:
        noun_cache.append(_extract_nouns(entry.get("text", "")))
        source_cache.append(set(extract_sources(entry.get("source") or "")))

    # Already-resolved pairs (from ledger) — avoid duplicates
    existing_pairs = set()
    for rec in topic["sourceCalibration"]["ledger"]:
        existing_pairs.add((rec.get("evidence_index"), rec.get("confirming_index")))

    overlap_threshold = 0.30  # at least 30% noun overlap

    for i in range(len(evidence)):
        nouns_i = noun_cache[i]
        if len(nouns_i) < 2:
            continue  # too short to compare meaningfully

        sources_i = source_cache[i]
        source_i = evidence[i].get("source") or ""

        for j in range(i + 1, len(evidence)):
            # Skip already-resolved pairs
            if (i, j) in existing_pairs:
                continue

            # Same-source skip: a source cannot confirm itself
            sources_j = source_cache[j]
            if sources_i and sources_j and (sources_i & sources_j):
                same_source_skipped += 1
                continue

            nouns_j = noun_cache[j]
            if len(nouns_j) < 2:
                continue

            # Compute overlap
            intersection = nouns_i & nouns_j
            union = nouns_i | nouns_j
            if not union:
                continue
            overlap_ratio = len(intersection) / min(len(nouns_i), len(nouns_j))

            if overlap_ratio < overlap_threshold:
                continue

            claim_state_j = evidence[j].get("claimState", "")
            source_j = evidence[j].get("source") or ""

            resolution = None
            if claim_state_j == "SUPPORTED":
                resolution = "CONFIRMED"
            elif claim_state_j in ("CONTESTED", "INVALIDATED"):
                resolution = "REFUTED"

            if resolution:
                resolutions.append({
                    "earlier_index": i,
                    "later_index": j,
                    "earlier_source": source_i,
                    "later_source": source_j,
                    "resolution": resolution,
                    "overlap_ratio": round(overlap_ratio, 3),
                    "earlier_text_snippet": evidence[i].get("text", "")[:120],
                    "later_text_snippet": evidence[j].get("text", "")[:120],
                })

    # Store skip count for auditability
    topic["sourceCalibration"]["_last_scan_same_source_skipped"] = same_source_skipped

    return resolutions


# ---------------------------------------------------------------------------
# 2) resolve_claim
# ---------------------------------------------------------------------------

def resolve_claim(topic: dict, evidence_index: int, resolution: str,
                  confirming_index: int) -> dict:
    """
    Record a claim resolution in topic["sourceCalibration"]["ledger"].

    Args:
        topic: the topic dict (mutated in place)
        evidence_index: index of the original evidence entry
        resolution: one of CONFIRMED, REFUTED, PARTIAL
        confirming_index: index of the confirming/refuting entry

    Returns:
        The ledger record that was appended.
    """
    if resolution not in ("CONFIRMED", "REFUTED", "PARTIAL"):
        raise ValueError(f"Invalid resolution: {resolution}. Must be CONFIRMED, REFUTED, or PARTIAL.")

    topic = _ensure_calibration(topic)
    evidence = topic.get("evidenceLog", [])

    if evidence_index < 0 or evidence_index >= len(evidence):
        raise IndexError(f"evidence_index {evidence_index} out of range (0-{len(evidence)-1})")
    if confirming_index < 0 or confirming_index >= len(evidence):
        raise IndexError(f"confirming_index {confirming_index} out of range (0-{len(evidence)-1})")

    original = evidence[evidence_index]
    confirming = evidence[confirming_index]

    # Compute trust delta
    trust_deltas = {"CONFIRMED": +0.05, "REFUTED": -0.10, "PARTIAL": -0.02}
    trust_delta = trust_deltas[resolution]

    record = {
        "timestamp": _now_iso(),
        "evidence_index": evidence_index,
        "confirming_index": confirming_index,
        "resolution": resolution,
        "source": original.get("source", ""),
        "confirming_source": confirming.get("source", ""),
        "trust_delta": trust_delta,
        "original_text_snippet": original.get("text", "")[:120],
        "confirming_text_snippet": confirming.get("text", "")[:120],
    }

    topic["sourceCalibration"]["ledger"].append(record)
    return record


# ---------------------------------------------------------------------------
# 3) compute_effective_trust
# ---------------------------------------------------------------------------

def _compute_domain_base_rates(topic: dict) -> dict:
    """
    Compute confirmation base rates per domain tag from the calibration ledger.

    Returns {tag: base_rate} where base_rate = confirmed / (confirmed + refuted).
    Tags with no resolved claims are omitted.
    """
    import math as _math

    cal = topic.get("sourceCalibration", {})
    evidence_log = topic.get("evidenceLog", [])
    tag_counts = {}  # {tag: {"confirmed": N, "refuted": N}}

    for record in cal.get("ledger", []):
        if record.get("flagged") == "SAME_SOURCE":
            continue
        resolution = record.get("resolution", "")
        if resolution not in ("CONFIRMED", "REFUTED"):
            continue

        # Get the tag from the evidence entry
        tag = ""
        ev_idx = record.get("evidence_index")
        if ev_idx is not None and 0 <= ev_idx < len(evidence_log):
            tag = evidence_log[ev_idx].get("tag", "")
        if not tag:
            tag = "UNKNOWN"

        if tag not in tag_counts:
            tag_counts[tag] = {"confirmed": 0, "refuted": 0}
        if resolution == "CONFIRMED":
            tag_counts[tag]["confirmed"] += 1
        else:
            tag_counts[tag]["refuted"] += 1

    base_rates = {}
    for tag, counts in tag_counts.items():
        total = counts["confirmed"] + counts["refuted"]
        # Require >= 3 resolved claims before computing a base rate.
        # With fewer, the estimate is too noisy and surprisal weighting
        # would amplify noise rather than signal.
        if total >= 3:
            base_rates[tag] = counts["confirmed"] / total

    return base_rates


def _surprisal_weight(base_rate: float, resolution: str) -> float:
    """
    Compute a surprisal-based weight for a claim resolution.

    A confirmed claim that's surprising (low base rate of confirmation in this
    domain) should give MORE trust credit. A confirmed claim that's expected
    (high base rate) should give LESS.

    Returns a multiplier in [0.5, 2.0] applied to the base LR exponent.

    For confirmations: weight = -log2(base_rate) / -log2(0.5)
        base_rate=0.99 → weight ≈ 0.014 / 1.0 ≈ 0.01 (clamped to 0.5)
        base_rate=0.50 → weight = 1.0
        base_rate=0.10 → weight ≈ 3.32 (clamped to 2.0)

    For refutations: weight = -log2(1 - base_rate) / -log2(0.5)
        (a refutation is surprising when the base rate of confirmation is HIGH)
    """
    import math as _math

    if resolution == "CONFIRMED":
        # Surprisal of this confirmation given the domain base rate
        p = max(0.01, min(0.99, base_rate))
        surprisal = -_math.log2(p)
    elif resolution == "REFUTED":
        # Surprisal of this refutation (= confirmation was expected)
        p = max(0.01, min(0.99, 1.0 - base_rate))
        surprisal = -_math.log2(p)
    else:
        return 1.0

    # Normalize: surprisal of a coin flip (p=0.5) = 1 bit → weight 1.0
    weight = surprisal / 1.0  # 1 bit baseline

    return max(0.5, min(2.0, weight))


def compute_effective_trust(topic: dict, source_name: str) -> float:
    """
    Compute Bayesian-updated trust for a source with surprisal-weighted LRs.

    prior = base_trust (from SOURCE_TRUST)
    For each resolution involving this source:
        if CONFIRMED:  lr *= base_lr_hit ^ surprisal_weight
        if REFUTED:    lr *= base_lr_miss ^ surprisal_weight
        if PARTIAL:    lr *= 0.9

    surprisal_weight: a correctly predicted surprising claim (low domain base
    rate) gives MORE trust credit than a correctly predicted expected claim.
    This prevents "oil goes up in a war" from earning the same trust boost
    as "Iran will release hostages by Tuesday."

    posterior = prior * LR / (prior * LR + (1 - prior))
    Clamped to [0.05, 0.99].
    """
    topic = _ensure_calibration(topic)
    evidence_log = topic.get("evidenceLog", [])

    prior = _get_source_trust(source_name)
    lr = 1.0  # cumulative likelihood ratio

    base_lr = {"CONFIRMED": 1.2, "REFUTED": 0.7, "PARTIAL": 0.9}
    domain_rates = _compute_domain_base_rates(topic)

    for record in topic["sourceCalibration"]["ledger"]:
        # Skip flagged same-source entries
        if record.get("flagged") == "SAME_SOURCE":
            continue
        # Check if this resolution involves the source (as original source)
        record_sources = extract_sources(record.get("source", ""))
        if source_name in record_sources or source_name == record.get("source", ""):
            resolution = record.get("resolution", "")
            if resolution not in base_lr:
                continue

            if resolution == "PARTIAL":
                lr *= base_lr["PARTIAL"]
                continue

            # Get the domain tag for surprisal weighting
            tag = ""
            ev_idx = record.get("evidence_index")
            if ev_idx is not None and 0 <= ev_idx < len(evidence_log):
                tag = evidence_log[ev_idx].get("tag", "")

            if tag and tag in domain_rates:
                sw = _surprisal_weight(domain_rates[tag], resolution)
            else:
                sw = 1.0  # no base rate data → neutral weight

            # Apply: LR = base_lr ^ surprisal_weight
            import math as _math
            lr *= _math.pow(base_lr[resolution], sw)

    posterior = (prior * lr) / (prior * lr + (1.0 - prior))
    return max(0.05, min(0.99, round(posterior, 4)))


# ---------------------------------------------------------------------------
# 4) auto_calibrate
# ---------------------------------------------------------------------------

def auto_calibrate(topic: dict) -> dict:
    """
    Full calibration pipeline:
    1. Scan for resolutions (confirmation/refutation pairs)
    2. Resolve found pairs
    3. Update effectiveTrust for all sources seen in the evidence log

    Returns summary dict.
    """
    topic = _ensure_calibration(topic)

    # Step 1: scan
    found = scan_for_resolutions(topic)

    # Step 2: resolve each found pair
    new_resolutions = []
    for res in found:
        record = resolve_claim(
            topic,
            res["earlier_index"],
            res["resolution"],
            res["later_index"],
        )
        new_resolutions.append(record)

    # Step 3: collect all unique sources from evidence log
    all_sources = set()
    for entry in topic.get("evidenceLog", []):
        src = entry.get("source", "")
        if src:
            for s in extract_sources(src):
                all_sources.add(s)

    # Update effective trust for each source
    trust_updates = {}
    for src in sorted(all_sources):
        base = _get_source_trust(src)
        effective = compute_effective_trust(topic, src)
        topic["sourceCalibration"]["effectiveTrust"][src] = effective
        if abs(effective - base) > 0.001:
            trust_updates[src] = {
                "base": base,
                "effective": effective,
                "delta": round(effective - base, 4),
            }

    # Step 4: flag existing same-source ledger entries (retroactive cleanup)
    same_source_flagged = 0
    for record in topic["sourceCalibration"]["ledger"]:
        if record.get("flagged"):
            continue
        src_a = set(extract_sources(record.get("source", "")))
        src_b = set(extract_sources(record.get("confirming_source", "")))
        if src_a and src_b and (src_a & src_b):
            record["flagged"] = "SAME_SOURCE"
            same_source_flagged += 1

    same_source_skipped = topic["sourceCalibration"].get(
        "_last_scan_same_source_skipped", 0
    )

    summary = {
        "timestamp": _now_iso(),
        "pairs_scanned": len(found),
        "new_resolutions": len(new_resolutions),
        "same_source_skipped": same_source_skipped,
        "same_source_flagged": same_source_flagged,
        "total_ledger_entries": len(topic["sourceCalibration"]["ledger"]),
        "sources_tracked": len(all_sources),
        "trust_changes": trust_updates,
    }

    return summary
