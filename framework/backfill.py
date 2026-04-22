#!/usr/bin/env python3
"""
NRL-Alpha Omega -- Historical Backfill & Outcome Scoring
========================================================

Backfill resolved topics with historical evidence, then score sources
against known outcomes. The goal: a source that was bullish on LK-99
being a superconductor (it wasn't) should carry that miss into future
science topics via the source database.

Two complementary scoring mechanisms:

1. **Text-overlap resolution** (existing: source_ledger.scan_for_resolutions)
   Finds confirmation/refutation pairs within the evidence log based on
   noun overlap. Good for live topics where ground truth isn't known yet.

2. **Outcome-based scoring** (new: score_against_outcome)
   When a topic resolves, we know which hypothesis won. Every evidence
   entry that pushed toward the wrong hypothesis is a miss for that source.
   Every entry that pushed toward the right hypothesis is a hit. This is
   strictly stronger than text-overlap because we have ground truth.

Usage:
    # Backfill evidence with historical timestamps
    python framework/backfill.py load --topic calibration-lk99-superconductor --file timeline.json

    # Score sources against known outcome
    python framework/backfill.py score --topic calibration-lk99-superconductor --winner H3

    # Full pipeline: backfill + score + calibrate + ingest into source_db
    python framework/backfill.py full --topic calibration-lk99-superconductor --winner H3 --file timeline.json

No external dependencies -- Python stdlib + engine/framework modules.
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import load_topic, add_evidence, save_topic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_posterior_impact(impact_str) -> dict:
    """
    Parse a posteriorImpact value into hypothesis direction signals.

    Accepts schemaVersion 1 strings or schemaVersion 2 structured dicts.

    String examples:
        "H1 +5pp, H3 -3pp"         -> {"H1": 1, "H3": -1}
        "H3 +10pp - smoking gun"    -> {"H3": 1}
        "NONE"                      -> {}
    Dict example (schemaVersion 2):
        {"indicatorId": "t2_x", "lrApplied": {...}, "outcome": "FIRED"}
        -> {"indicatorId": "t2_x"} direction signals extracted from lrApplied keys
    """
    # schemaVersion 2: structured dict
    if isinstance(impact_str, dict):
        signals = {}
        lr_applied = impact_str.get("lrApplied", {})
        if lr_applied:
            max_lr = max(lr_applied.values()) if lr_applied else 1.0
            min_lr = min(lr_applied.values()) if lr_applied else 1.0
            for h, lr in lr_applied.items():
                if lr >= max_lr * 0.8:
                    signals[h] = 1
                elif lr <= min_lr * 1.2 and lr < 0.5:
                    signals[h] = -1
        return signals

    if not impact_str or impact_str == "NONE":
        return {}

    signals = {}
    # Match patterns like "H1 +5pp", "H3 -10pp", "H2 +3pp"
    for match in re.finditer(r"(H\d+)\s*([+-])\s*\d+\s*pp", impact_str):
        h = match.group(1)
        direction = 1 if match.group(2) == "+" else -1
        signals[h] = direction

    # Also catch "H3 confirmed", "H3 dominant", etc.
    for match in re.finditer(r"(H\d+)\s+(?:confirmed|dominant|wins|correct)", impact_str, re.IGNORECASE):
        signals[match.group(1)] = 1

    return signals


# ---------------------------------------------------------------------------
# Backfill evidence
# ---------------------------------------------------------------------------

def backfill_evidence(topic: dict, timeline: list) -> dict:
    """
    Add historical evidence entries with their actual timestamps.

    Each timeline entry is a dict with at minimum:
        tag, text, time (ISO 8601)

    Optional fields: source, provenance, posteriorImpact, claimState

    Returns the updated topic (also mutated in place).
    """
    added = 0
    skipped = 0

    for entry in timeline:
        if "tag" not in entry or "text" not in entry:
            skipped += 1
            continue

        if "time" not in entry:
            skipped += 1
            continue

        pre_count = len(topic.get("evidenceLog", []))
        topic = add_evidence(topic, entry)
        post_count = len(topic.get("evidenceLog", []))

        if post_count > pre_count:
            added += 1
        else:
            skipped += 1  # dedup caught it

    return {
        "added": added,
        "skipped": skipped,
        "total": len(topic.get("evidenceLog", [])),
    }


# ---------------------------------------------------------------------------
# Outcome-based source scoring
# ---------------------------------------------------------------------------

def score_against_outcome(topic: dict, winning_hypothesis: str,
                          losing_hypotheses: list = None) -> dict:
    """
    Score every evidence entry against the known outcome.

    For each entry with a parseable posteriorImpact:
    - If it pushed TOWARD the winner  -> CONFIRMED (source was right)
    - If it pushed AGAINST the winner -> REFUTED (source was wrong)
    - If it pushed toward a loser     -> REFUTED
    - If neutral/unparseable          -> SKIPPED

    Records results in topic["outcomeScoring"] for audit trail,
    and appends to topic["sourceCalibration"]["ledger"] for trust updates.

    Args:
        winning_hypothesis: e.g. "H3"
        losing_hypotheses: e.g. ["H1", "H2"] (auto-inferred if None)

    Returns summary dict.
    """
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    if winning_hypothesis not in hypotheses:
        raise ValueError(f"Unknown hypothesis: {winning_hypothesis}")

    if losing_hypotheses is None:
        losing_hypotheses = [h for h in hypotheses if h != winning_hypothesis]

    evidence = topic.get("evidenceLog", [])
    if not evidence:
        return {"scored": 0, "confirmed": 0, "refuted": 0, "skipped": 0}

    # Ensure sourceCalibration exists
    if "sourceCalibration" not in topic:
        topic["sourceCalibration"] = {"ledger": [], "effectiveTrust": {}}
    cal = topic["sourceCalibration"]

    scored = 0
    confirmed = 0
    refuted = 0
    skipped = 0
    scoring_log = []

    for i, entry in enumerate(evidence):
        # Skip auto-generated deadline elimination entries — they have no source
        # and attributing the posterior drop to an evidence source corrupts calibration.
        if entry.get("ledger") == "DECISION" and "DEADLINE ELIMINATION" in entry.get("text", ""):
            skipped += 1
            continue

        impact_str = entry.get("posteriorImpact", "NONE")
        signals = _parse_posterior_impact(impact_str)

        if not signals:
            skipped += 1
            continue

        source = entry.get("source", "")
        if not source:
            skipped += 1
            continue

        # Determine if this entry helped or hurt
        # Positive signal for winner = CONFIRMED
        # Negative signal for winner OR positive for loser = REFUTED
        winner_signal = signals.get(winning_hypothesis, 0)
        loser_signals = [signals.get(h, 0) for h in losing_hypotheses]
        max_loser_positive = max(loser_signals) if loser_signals else 0

        resolution = None
        reason = ""

        if winner_signal > 0:
            resolution = "CONFIRMED"
            reason = f"Pushed toward {winning_hypothesis} (correct)"
        elif winner_signal < 0:
            resolution = "REFUTED"
            reason = f"Pushed against {winning_hypothesis} (incorrect)"
        elif max_loser_positive > 0:
            # Pushed toward a losing hypothesis
            boosted_losers = [h for h in losing_hypotheses if signals.get(h, 0) > 0]
            resolution = "REFUTED"
            reason = f"Pushed toward {'+'.join(boosted_losers)} (lost)"
        else:
            skipped += 1
            continue

        scored += 1
        if resolution == "CONFIRMED":
            confirmed += 1
        else:
            refuted += 1

        record = {
            "timestamp": _now_iso(),
            "evidence_index": i,
            "resolution": resolution,
            "source": source,
            "tag": entry.get("tag", ""),
            "reason": reason,
            "winning_hypothesis": winning_hypothesis,
            "original_text_snippet": entry.get("text", "")[:120],
            "scoring_method": "outcome",
        }
        cal["ledger"].append(record)
        scoring_log.append(record)

    # Store scoring metadata
    topic["outcomeScoring"] = {
        "winningHypothesis": winning_hypothesis,
        "losingHypotheses": losing_hypotheses,
        "scoredAt": _now_iso(),
        "summary": {
            "scored": scored,
            "confirmed": confirmed,
            "refuted": refuted,
            "skipped": skipped,
        },
        "log": scoring_log,
    }

    return {
        "scored": scored,
        "confirmed": confirmed,
        "refuted": refuted,
        "skipped": skipped,
        "ledger_entries_added": len(scoring_log),
    }


# ---------------------------------------------------------------------------
# Update source trust from outcome scoring
# ---------------------------------------------------------------------------

def update_trust_from_outcomes(topic: dict) -> dict:
    """
    After outcome scoring, recompute effective trust for all sources.

    Uses the same Bayesian update as source_ledger but applies
    outcome-based resolutions (stronger signal than text-overlap).

    Returns dict of trust changes.
    """
    from framework.source_ledger import extract_sources, compute_effective_trust

    cal = topic.get("sourceCalibration", {})

    # Collect all sources from outcome scoring
    all_sources = set()
    for entry in topic.get("evidenceLog", []):
        src = entry.get("source", "")
        if src:
            for s in extract_sources(src):
                all_sources.add(s)

    trust_changes = {}
    for src in sorted(all_sources):
        old_trust = cal.get("effectiveTrust", {}).get(src)
        new_trust = compute_effective_trust(topic, src)
        cal.setdefault("effectiveTrust", {})[src] = new_trust

        if old_trust is not None and abs(new_trust - old_trust) > 0.001:
            trust_changes[src] = {
                "old": old_trust,
                "new": new_trust,
                "delta": round(new_trust - old_trust, 4),
            }
        elif old_trust is None:
            trust_changes[src] = {
                "old": None,
                "new": new_trust,
                "delta": None,
            }

    return trust_changes


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def full_backfill_pipeline(topic_slug: str, winning_hypothesis: str,
                           timeline: list = None) -> dict:
    """
    Complete backfill pipeline:
    1. Load topic
    2. Backfill evidence (if timeline provided)
    3. Score against outcome
    4. Update source trust
    5. Run auto_calibrate for text-overlap resolutions
    6. Ingest into cross-topic source database
    7. Save topic

    Returns comprehensive summary.
    """
    from framework.source_ledger import auto_calibrate
    from framework.source_db import load_db, save_db, ingest_from_topic

    topic = load_topic(topic_slug)
    summary = {"topic": topic_slug, "winner": winning_hypothesis}

    # Step 1: Backfill evidence
    if timeline:
        backfill_result = backfill_evidence(topic, timeline)
        summary["backfill"] = backfill_result

    # Step 2: Score against outcome
    score_result = score_against_outcome(topic, winning_hypothesis)
    summary["outcome_scoring"] = score_result

    # Step 3: Update trust from outcomes
    trust_changes = update_trust_from_outcomes(topic)
    summary["trust_changes"] = trust_changes

    # Step 4: Text-overlap calibration (complementary to outcome scoring)
    cal_result = auto_calibrate(topic)
    summary["auto_calibrate"] = cal_result

    # Step 5: Save topic
    save_topic(topic)

    # Step 6: Ingest into cross-topic source database
    topic = load_topic(topic_slug)  # reload with governance snapshot
    db = load_db()
    ingest_result = ingest_from_topic(db, topic)
    save_db(db)
    summary["source_db_ingest"] = ingest_result

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="NRL-AO Historical Backfill & Outcome Scoring"
    )
    parser.add_argument("command", choices=["load", "score", "full", "rescore"],
                        help="load=backfill evidence, score=outcome scoring, "
                             "full=complete pipeline, rescore=re-run scoring on existing evidence")
    parser.add_argument("--topic", required=True, help="Topic slug")
    parser.add_argument("--winner", help="Winning hypothesis (e.g. H3)")
    parser.add_argument("--file", help="Timeline JSON file for backfill")
    parser.add_argument("--clear", action="store_true",
                        help="Clear existing evidence before backfill")
    args = parser.parse_args()

    if args.command == "load":
        if not args.file:
            parser.error("--file required for load")
        topic = load_topic(args.topic)
        if args.clear:
            topic["evidenceLog"] = []
        with open(args.file, "r", encoding="utf-8") as f:
            timeline = json.load(f)
        result = backfill_evidence(topic, timeline)
        save_topic(topic)
        print(f"Backfill: {result}")

    elif args.command == "score":
        if not args.winner:
            parser.error("--winner required for score")
        topic = load_topic(args.topic)
        result = score_against_outcome(topic, args.winner)
        trust = update_trust_from_outcomes(topic)
        save_topic(topic)
        print(f"Scoring: {result}")
        if trust:
            print(f"Trust changes:")
            for src, change in trust.items():
                print(f"  {src}: {change}")

    elif args.command == "full":
        if not args.winner:
            parser.error("--winner required for full")
        timeline = None
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                timeline = json.load(f)
        result = full_backfill_pipeline(args.topic, args.winner, timeline)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "rescore":
        if not args.winner:
            parser.error("--winner required for rescore")
        topic = load_topic(args.topic)
        # Clear previous outcome scoring
        topic.pop("outcomeScoring", None)
        cal = topic.get("sourceCalibration", {})
        cal["ledger"] = [r for r in cal.get("ledger", [])
                         if r.get("scoring_method") != "outcome"]
        result = score_against_outcome(topic, args.winner)
        trust = update_trust_from_outcomes(topic)
        save_topic(topic)
        print(f"Rescored: {result}")
        if trust:
            print("Trust changes:")
            for src, change in trust.items():
                print(f"  {src}: {change}")
