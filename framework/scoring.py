#!/usr/bin/env python3
"""
NRL-Alpha Omega — Prediction Scoring Module
============================================

Brier-score-based calibration tracking for the Bayesian estimation engine.

1. Snapshot posteriors after every update (with entropy).
2. Record ground-truth outcomes when a topic resolves.
3. Compute Brier scores for all historical snapshots.
4. Generate calibration reports and health status.
5. Backfill snapshots from existing posteriorHistory entries.

Pure stdlib — no external dependencies.
"""

import sys
import math
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from governor import compute_entropy


# ============================================================================
# Helpers
# ============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _ensure_scoring_block(topic: dict) -> dict:
    """Initialize topic['predictionScoring'] if missing."""
    if "predictionScoring" not in topic:
        topic["predictionScoring"] = {
            "snapshots": [],
            "outcomes": [],
            "brierScores": [],
        }
    ps = topic["predictionScoring"]
    for key in ("snapshots", "outcomes", "brierScores"):
        if key not in ps:
            ps[key] = []
    return ps


def _extract_posteriors(topic: dict) -> dict:
    """Pull {H1: p1, H2: p2, ...} from current hypotheses."""
    return {
        hid: h["posterior"]
        for hid, h in topic["model"]["hypotheses"].items()
    }


# ============================================================================
# 1. Snapshot Posteriors
# ============================================================================

def snapshot_posteriors(topic: dict, trigger: str = "manual") -> dict:
    """
    Record current posteriors as a timestamped snapshot.

    Parameters
    ----------
    topic : dict
        Live topic state.
    trigger : str
        What caused this snapshot ("manual", "posterior_update", "backfill").

    Returns
    -------
    dict
        The snapshot that was appended.
    """
    ps = _ensure_scoring_block(topic)
    posteriors = _extract_posteriors(topic)
    entropy = compute_entropy(topic)

    snapshot = {
        "timestamp": _now_iso(),
        "trigger": trigger,
        "posteriors": posteriors,
        "entropy": round(entropy, 4),
    }
    ps["snapshots"].append(snapshot)
    return snapshot


# ============================================================================
# 2. Record Outcome
# ============================================================================

def record_outcome(topic: dict, resolved_hypothesis: str, note: str = "") -> dict:
    """
    Record which hypothesis was correct, then score all snapshots.

    Parameters
    ----------
    topic : dict
        Live topic state.
    resolved_hypothesis : str
        The hypothesis key that turned out correct (e.g. "H3").
    note : str
        Optional annotation.

    Returns
    -------
    dict
        The outcome record that was appended.
    """
    ps = _ensure_scoring_block(topic)
    hypotheses = topic["model"]["hypotheses"]

    if resolved_hypothesis not in hypotheses:
        raise ValueError(
            f"Unknown hypothesis '{resolved_hypothesis}'. "
            f"Valid keys: {list(hypotheses.keys())}"
        )

    outcome = {
        "timestamp": _now_iso(),
        "resolved": resolved_hypothesis,
        "label": hypotheses[resolved_hypothesis]["label"],
        "note": note,
    }
    ps["outcomes"].append(outcome)

    # Score all snapshots against this outcome
    score_all_snapshots(topic)

    return outcome


# ============================================================================
# 2b. Expired Hypotheses (Interim Scoring)
# ============================================================================

def check_expired_hypotheses(topic: dict) -> list:
    """
    Auto-detect hypotheses that have expired based on midpoint + unit vs dayCount.

    A hypothesis expires when dayCount > midpoint_days * 1.5 (generous buffer).
    Example: H1 "<6 weeks" has midpoint 4 weeks = 28 days, expires at day 42.

    Returns list of expired hypothesis dicts.
    """
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    day_count = topic.get("meta", {}).get("dayCount", 0)

    expired = []
    for h_key, h_data in hypotheses.items():
        midpoint = h_data.get("midpoint", 0)
        unit = h_data.get("unit", "weeks")

        # Convert midpoint to days
        if unit == "weeks":
            midpoint_days = midpoint * 7
        elif unit == "months":
            midpoint_days = midpoint * 30
        elif unit == "days":
            midpoint_days = midpoint
        else:
            continue

        # Expire at 1.5x the midpoint (generous buffer)
        expiry_day = midpoint_days * 1.5

        if day_count > expiry_day and h_data.get("posterior", 0) > 0.001:
            expired.append({
                "hypothesis": h_key,
                "label": h_data.get("label", ""),
                "midpoint_days": midpoint_days,
                "expired_at_day": int(expiry_day),
                "current_day": day_count,
                "current_posterior": h_data.get("posterior", 0),
                "status": "EXPIRED",
            })

    return expired


def record_partial_outcome(topic: dict, expired_hypothesis: str,
                           note: str = "") -> dict:
    """
    Record that a specific hypothesis has been proved wrong by time expiry.

    Unlike record_outcome(), this does NOT resolve the topic — it just says
    "this hypothesis is definitely wrong" without declaring which is right.

    Computes partial Brier component: p^2 (predicted probability of an
    event that didn't happen, squared).
    """
    ps = _ensure_scoring_block(topic)
    hypotheses = topic["model"]["hypotheses"]

    if expired_hypothesis not in hypotheses:
        raise ValueError(f"Unknown hypothesis '{expired_hypothesis}'")

    outcome = {
        "timestamp": _now_iso(),
        "type": "PARTIAL_EXPIRY",
        "expired": expired_hypothesis,
        "label": hypotheses[expired_hypothesis]["label"],
        "note": note or f"{expired_hypothesis} expired by time",
    }
    ps["outcomes"].append(outcome)

    # Compute partial Brier for each snapshot
    partial_scores = []
    for snap in ps.get("snapshots", []):
        p = snap["posteriors"].get(expired_hypothesis, 0)
        brier_component = p ** 2  # (predicted - 0)^2
        partial_scores.append({
            "timestamp": snap["timestamp"],
            "hypothesis": expired_hypothesis,
            "predicted": round(p, 4),
            "actual": 0.0,
            "brier_component": round(brier_component, 6),
        })

    if "partialBrierScores" not in ps:
        ps["partialBrierScores"] = []
    ps["partialBrierScores"].extend(partial_scores)

    return outcome


# ============================================================================
# 3. Brier Score
# ============================================================================

def compute_brier_score(posteriors_dict: dict, resolved_hypothesis: str) -> dict:
    """
    Standard Brier score: (1/N) * sum((p_i - o_i)^2).

    Parameters
    ----------
    posteriors_dict : dict
        {H1: p1, H2: p2, ...} — the forecasted probabilities.
    resolved_hypothesis : str
        Which hypothesis actually occurred.

    Returns
    -------
    dict
        {"brier": float, "per_hypothesis": {H1: float, ...}}
        Lower is better (0 = perfect, 1 = worst for binary, up to 2 for
        multi-category but practically bounded by normalization).
    """
    n = len(posteriors_dict)
    if n == 0:
        raise ValueError("Empty posteriors dict")

    per_h = {}
    total = 0.0
    for hid, p in posteriors_dict.items():
        outcome_indicator = 1.0 if hid == resolved_hypothesis else 0.0
        sq = (p - outcome_indicator) ** 2
        per_h[hid] = round(sq, 6)
        total += sq

    brier = total / n
    return {
        "brier": round(brier, 6),
        "per_hypothesis": per_h,
    }


# ============================================================================
# 4. Score All Snapshots
# ============================================================================

def score_all_snapshots(topic: dict) -> list:
    """
    Compute Brier scores for every snapshot against recorded outcomes.

    Returns
    -------
    list of dict
        Each entry: {timestamp, trigger, brier_score, per_hypothesis, outcome}.
    """
    ps = _ensure_scoring_block(topic)

    if not ps["outcomes"]:
        return []

    # Use the most recent outcome (a topic resolves once)
    latest_outcome = ps["outcomes"][-1]
    resolved = latest_outcome["resolved"]

    scores = []
    for snap in ps["snapshots"]:
        result = compute_brier_score(snap["posteriors"], resolved)
        entry = {
            "timestamp": snap["timestamp"],
            "trigger": snap.get("trigger", "unknown"),
            "brier_score": result["brier"],
            "per_hypothesis": result["per_hypothesis"],
            "outcome": resolved,
        }
        scores.append(entry)

    ps["brierScores"] = scores
    return scores


# ============================================================================
# 5. Calibration Report
# ============================================================================

def compute_calibration_report(topic: dict) -> dict:
    """
    Aggregate calibration analysis over all scored snapshots.

    Returns
    -------
    dict with keys:
        - n_snapshots: int
        - avg_brier: float
        - best: {timestamp, brier_score}
        - worst: {timestamp, brier_score}
        - trend: "improving" | "degrading" | "stable" | "insufficient_data"
        - confidence_interval: {lower, upper} or None
        - calibration: "WELL_CALIBRATED" | "ACCEPTABLE" | "POORLY_CALIBRATED"
    """
    ps = _ensure_scoring_block(topic)
    scores = ps.get("brierScores", [])

    if not scores:
        return {
            "n_snapshots": 0,
            "avg_brier": None,
            "best": None,
            "worst": None,
            "trend": "insufficient_data",
            "confidence_interval": None,
            "calibration": "insufficient_data",
        }

    briers = [s["brier_score"] for s in scores]
    n = len(briers)
    avg = sum(briers) / n

    best_idx = briers.index(min(briers))
    worst_idx = briers.index(max(briers))

    best = {"timestamp": scores[best_idx]["timestamp"], "brier_score": briers[best_idx]}
    worst = {"timestamp": scores[worst_idx]["timestamp"], "brier_score": briers[worst_idx]}

    # Trend: compare first half avg to second half avg
    if n >= 4:
        mid = n // 2
        first_half_avg = sum(briers[:mid]) / mid
        second_half_avg = sum(briers[mid:]) / (n - mid)
        delta = second_half_avg - first_half_avg
        if delta < -0.02:
            trend = "improving"
        elif delta > 0.02:
            trend = "degrading"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"

    # Confidence interval (mean +/- 1.96 * stderr) if enough data
    ci = None
    if n >= 5:
        variance = sum((b - avg) ** 2 for b in briers) / (n - 1)
        stderr = math.sqrt(variance / n)
        ci = {
            "lower": round(max(0.0, avg - 1.96 * stderr), 4),
            "upper": round(min(1.0, avg + 1.96 * stderr), 4),
        }

    # Calibration label
    if avg <= 0.25:
        calibration = "WELL_CALIBRATED"
    elif avg <= 0.4:
        calibration = "ACCEPTABLE"
    else:
        calibration = "POORLY_CALIBRATED"

    return {
        "n_snapshots": n,
        "avg_brier": round(avg, 4),
        "best": best,
        "worst": worst,
        "trend": trend,
        "confidence_interval": ci,
        "calibration": calibration,
    }


# ============================================================================
# 6. Calibration Health (for governance snapshot)
# ============================================================================

def get_calibration_health(topic: dict) -> str:
    """
    Returns "WELL_CALIBRATED", "ACCEPTABLE", or "POORLY_CALIBRATED".
    Falls back to "ACCEPTABLE" if no scores exist yet.
    """
    report = compute_calibration_report(topic)
    cal = report.get("calibration", "insufficient_data")
    if cal == "insufficient_data":
        return "ACCEPTABLE"
    return cal


# ============================================================================
# 7. Backfill Snapshots from posteriorHistory
# ============================================================================

def backfill_snapshots_from_history(topic: dict) -> int:
    """
    Retroactively generate snapshots from existing posteriorHistory entries.

    Skips any history entry whose date already appears in the snapshots list
    to avoid duplicates.

    Returns
    -------
    int
        Number of snapshots added.
    """
    ps = _ensure_scoring_block(topic)
    history = topic.get("model", {}).get("posteriorHistory", [])

    if not history:
        return 0

    # Collect existing snapshot timestamps (date portion) to deduplicate
    existing_dates = set()
    for snap in ps["snapshots"]:
        ts = snap.get("timestamp", "")
        existing_dates.add(ts[:10])  # YYYY-MM-DD prefix

    hypotheses = topic["model"]["hypotheses"]
    h_keys = sorted(hypotheses.keys())

    added = 0
    for entry in history:
        date_str = entry.get("date", "")
        if date_str[:10] in existing_dates:
            continue

        # Build posteriors dict from history entry
        posteriors = {}
        for hk in h_keys:
            if hk in entry:
                posteriors[hk] = entry[hk]

        if not posteriors:
            continue

        # Compute entropy from the historical posteriors
        entropy = 0.0
        for p in posteriors.values():
            if p > 0:
                entropy -= p * math.log2(p)

        snapshot = {
            "timestamp": f"{date_str}T00:00:00+00:00",
            "trigger": "backfill",
            "posteriors": posteriors,
            "entropy": round(entropy, 4),
            "note": entry.get("note", ""),
        }
        ps["snapshots"].append(snapshot)
        existing_dates.add(date_str[:10])
        added += 1

    # Sort snapshots chronologically after backfill
    ps["snapshots"].sort(key=lambda s: s["timestamp"])

    return added


# ============================================================================
# CLI entry point (for manual testing)
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NRL-AO Prediction Scoring")
    parser.add_argument("slug", help="Topic slug (e.g. hormuz-closure)")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill snapshots from posteriorHistory")
    parser.add_argument("--snapshot", action="store_true",
                        help="Take a snapshot of current posteriors")
    parser.add_argument("--resolve", metavar="HX",
                        help="Record outcome (e.g. --resolve H3)")
    parser.add_argument("--report", action="store_true",
                        help="Print calibration report")
    args = parser.parse_args()

    from engine import load_topic, save_topic

    topic = load_topic(args.slug)

    if args.backfill:
        n = backfill_snapshots_from_history(topic)
        print(f"Backfilled {n} snapshots from posteriorHistory.")

    if args.snapshot:
        snap = snapshot_posteriors(topic, trigger="manual")
        print(f"Snapshot taken: entropy={snap['entropy']}, posteriors={snap['posteriors']}")

    if args.resolve:
        outcome = record_outcome(topic, args.resolve)
        print(f"Outcome recorded: {outcome['resolved']} ({outcome['label']})")
        scores = topic["predictionScoring"]["brierScores"]
        print(f"Scored {len(scores)} snapshots.")

    if args.report:
        report = compute_calibration_report(topic)
        print(f"Calibration Report ({report['n_snapshots']} snapshots):")
        print(f"  Avg Brier:  {report['avg_brier']}")
        print(f"  Trend:      {report['trend']}")
        print(f"  Best:       {report['best']}")
        print(f"  Worst:      {report['worst']}")
        print(f"  95% CI:     {report['confidence_interval']}")
        print(f"  Health:     {report['calibration']}")

    save_topic(topic)
    print("Topic saved.")
