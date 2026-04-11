#!/usr/bin/env python3
"""
NRL-Alpha Omega — Programmatic Update Framework
===================================================

Two-phase update system:
  Phase 1 (agent): Gather intel via WebSearch, structure as JSON
  Phase 2 (script): Ingest evidence, update feeds/posteriors/sub-models,
                     generate brief, record diff, save topic

The agent cannot call web search from Python. So this script accepts
structured intel as JSON input — the agent's job is to search and
structure, this script's job is to validate and commit.

Usage:
    # Full update with evidence + feeds + posteriors + sub-models
    python framework/update.py --topic hormuz-closure \
        --evidence '[{"tag":"ECON","text":"Brent at 95","provenance":"OBSERVED"}]' \
        --feeds '{"brent":95.63,"wti":96.57}' \
        --posteriors '{"H1":0.005,"H2":0.18,"H3":0.53,"H4":0.285}' \
        --posterior-reason "Ceasefire not implemented..." \
        --submodels '{"meuMission":{"kharg":0.55,"larak":0.22}}' \
        --submodel-reason "Kharg not seized..."

    # Audit only
    python framework/update.py --topic hormuz-closure --audit

    # Orient only (print current state, no mutations)
    python framework/update.py --topic hormuz-closure --orient
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from difflib import unified_diff

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import (
    load_topic, add_evidence, update_posteriors, update_submodel,
    update_feed, save_topic, generate_brief, save_brief,
    GovernanceError, _add_evidence_raw,
)
from governor import (
    check_update_proposal, audit_evidence_freshness,
    assess_claim_state, classify_evidence,
)


BRIEFS_DIR = Path(__file__).parent.parent / "briefs"
CHANGELOG = Path(__file__).parent.parent / "CHANGELOG.md"

SOURCE_TRUST = {
    "CENTCOM": 0.95, "Pentagon": 0.95, "DoD": 0.95,
    "Reuters": 0.90, "AP": 0.90, "AFP": 0.90,
    "WashingtonPost": 0.85, "NewYorkTimes": 0.85,
    "Bloomberg": 0.85, "WallStreetJournal": 0.85,
    "CNN": 0.75, "BBC": 0.75, "Fox": 0.70,
    "Fortune": 0.70, "WSJ": 0.70,
    "DailyMail": 0.50, "CNBC": 0.65,
    "Al Jazeera": 0.60, "TASS": 0.50,
    "Mehr News": 0.50, "IRNA": 0.40, "ISNA": 0.40,
}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _most_recent_brief(topic_name: str) -> str | None:
    """Read most recent brief content (token-efficient: only one file)."""
    d = BRIEFS_DIR / topic_name
    if not d.exists():
        return None
    briefs = sorted(d.glob("2*.md"), key=lambda p: p.stem, reverse=True)
    if not briefs:
        return None
    try:
        return briefs[0].read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return briefs[0].read_text(encoding="utf-8", errors="replace")


def _record_diff(before: str | None, after: str, change_type: str):
    """Append unified diff to CHANGELOG.md (not full brief content)."""
    if not before:
        return
    diff_lines = list(unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile="previous", tofile="current", lineterm="",
    ))
    if not diff_lines:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n---\n## {ts} [{change_type}]\n\n```diff\n"
    entry += "\n".join(diff_lines[:80])  # cap diff length
    if len(diff_lines) > 80:
        entry += f"\n... ({len(diff_lines) - 80} more lines)\n"
    entry += "\n```\n"
    existing = CHANGELOG.read_text(encoding="utf-8", errors="replace") if CHANGELOG.exists() else "# NRL-Alpha Omega Change Log\n"
    CHANGELOG.write_text(existing + entry, encoding="utf-8")


# ============================================================================
# Orient — read-only snapshot for the agent
# ============================================================================

def orient(topic_name: str) -> dict:
    """Print current state as compact JSON. No mutations."""
    t = load_topic(topic_name)
    h = t["model"]["hypotheses"]
    feeds = {k: {"value": v.get("value"), "asOf": v.get("asOf", "")[:10]}
             for k, v in t.get("dataFeeds", {}).items()}
    log = t.get("evidenceLog", [])
    fresh = [e for e in log if e.get("time", "") and e["time"][:10] >= "2026-04-09"]
    gov = t.get("governance", {})

    state = {
        "topic": topic_name,
        "lastUpdated": t["meta"]["lastUpdated"],
        "dayCount": t["meta"]["dayCount"],
        "posteriors": {k: round(v["posterior"], 4) for k, v in h.items()},
        "expectedWeeks": t["model"]["expectedValue"],
        "feeds": feeds,
        "evidence": {"total": len(log), "fresh": len(fresh)},
        "governance": {
            "health": gov.get("health"),
            "rt": gov.get("rt", {}).get("rt"),
            "entropy": round(gov.get("entropy", 0), 3),
        },
        "last5": [
            {"time": e.get("time", "")[:16], "tag": e.get("tag"), "text": e.get("text", "")[:80]}
            for e in log[-5:]
        ],
    }
    return state


# ============================================================================
# Main update pipeline
# ============================================================================

def run_update(topic_name: str, *,
               evidence: list[dict] | None = None,
               feeds: dict | None = None,
               posteriors: dict | None = None,
               posterior_reason: str = "",
               submodels: dict | None = None,
               submodel_reason: str = "",
               mode: str = "routine",
               force: bool = False) -> dict:
    """
    Governor-gated update pipeline.

    Args:
        topic_name: topic slug
        evidence: list of evidence dicts from agent's web search
        feeds: {feed_id: value} for data feed updates
        posteriors: {H1: p, H2: p, ...} new posteriors
        posterior_reason: justification string
        submodels: {submodel_name: {scenario: prob, ...}}
        submodel_reason: justification string
        mode: "routine" or "crisis"

    Returns:
        result dict with state, governance, brief path
    """
    print(f"\n{'='*60}")
    print(f"UPDATE: {topic_name} [{mode}]")
    print(f"{'='*60}")

    # 1. Load topic + read most recent brief (one file only)
    t = load_topic(topic_name)
    prev_brief = _most_recent_brief(topic_name)
    print(f"[LOAD] {topic_name} | lastUpdated={t['meta']['lastUpdated']}")

    results = {"added": 0, "rejected": 0, "feeds_updated": [], "errors": []}

    # 2. Add evidence (governor-gated via engine.add_evidence)
    if evidence:
        for i, e in enumerate(evidence):
            try:
                add_evidence(t, e)
                results["added"] += 1
                print(f"[+] {i+1}. [{e.get('tag','')}] {e.get('text','')[:60]}...")
            except Exception as ex:
                results["rejected"] += 1
                results["errors"].append(str(ex))
                print(f"[X] {i+1}. REJECTED: {ex}")

    # 3. Update data feeds
    if feeds:
        now = _now_iso()
        for fid, val in feeds.items():
            try:
                update_feed(t, fid, val, as_of=now)
                results["feeds_updated"].append(fid)
                print(f"[FEED] {fid} = {val}")
            except ValueError as ex:
                results["errors"].append(str(ex))
                print(f"[FEED ERROR] {fid}: {ex}")

    # 3.5 Auto-calibrate source trust
    try:
        from source_ledger import auto_calibrate
        cal = auto_calibrate(t)
        if cal.get("resolutions_found", 0) > 0:
            print(f"[CAL] Source calibration: {cal['resolutions_found']} resolutions, "
                  f"{cal.get('trust_changes', 0)} trust adjustments")
            results["calibration"] = cal
    except (ImportError, Exception) as ex:
        pass  # Non-blocking

    # 3.6 Check contradictions before posterior update
    try:
        from contradictions import get_unresolved_contradictions
        unresolved = get_unresolved_contradictions(t)
        high_sev = [c for c in unresolved if c.get("severity") == "HIGH"]
        if high_sev:
            print(f"[WARN] {len(high_sev)} HIGH-severity unresolved contradictions:")
            for c in high_sev[:3]:
                print(f"  - {c.get('type', '?')}: {c.get('reason', '?')[:60]}")
            results["contradictions_warning"] = len(high_sev)
    except (ImportError, Exception):
        pass

    # 4. Update posteriors (governor hard-gates this via engine.update_posteriors)
    if posteriors:
        try:
            update_posteriors(t, posteriors,
                              reason=posterior_reason,
                              evidence_refs=[_now_iso()])
        except GovernanceError as e:
            if force and "unresolved_contradiction" in getattr(e, "failures", []):
                # Force override — create audit trail and apply manually
                print(f"[FORCE] Governance block overridden: {e}")
                _add_evidence_raw(t, {
                    "time": _now_iso(),
                    "tag": "INTEL",
                    "text": (f"GOVERNANCE FORCE OVERRIDE: {e}. "
                             f"Operator acknowledged unresolved contradictions."),
                    "provenance": "USER_PROVIDED",
                    "posteriorImpact": "NONE",
                    "ledger": "DECISION",
                    "claimState": "PROPOSED",
                    "effectiveWeight": 0.5,
                })
                # Apply posteriors directly (bypass governor)
                hypotheses = t["model"]["hypotheses"]
                total = sum(posteriors.values())
                for k in posteriors:
                    hypotheses[k]["posterior"] = round(posteriors[k] / total, 4)
                t["model"]["expectedValue"] = round(
                    sum(h["midpoint"] * h["posterior"] for h in hypotheses.values()), 2
                )
                results["force_override"] = True
            else:
                raise  # Re-raise non-forceable failures

        ev = t["model"]["expectedValue"]
        print(f"[POST] Updated | E[weeks]={ev}")

        # Red team result is embedded in posteriorHistory by engine
        try:
            history = t["model"].get("posteriorHistory", [])
            if history and "redTeam" in history[-1]:
                rt = history[-1]["redTeam"]
                score = rt.get("devil_advocate_score", 0)
                print(f"[RED TEAM] Devil's advocate score: {score:.2f}")
                if score > 0.6:
                    print(f"[RED TEAM] STRONG counter-case: {rt.get('challenge', '')[:80]}...")
                results["red_team_score"] = score
        except Exception:
            pass

    # 5. Update sub-models
    if submodels:
        for sm_name, scenarios in submodels.items():
            update_submodel(t, sm_name, scenarios,
                            reason=submodel_reason,
                            evidence_refs=[_now_iso()])
            print(f"[SUB] {sm_name} updated")

    # 6. Save topic (triggers governance snapshot)
    save_topic(t)
    gov = t.get("governance", {})
    print(f"[SAVE] Health={gov.get('health')} | Entropy={gov.get('entropy', 0):.3f}")

    # 7. Generate and save brief
    try:
        brief_text = generate_brief(t, mode=mode)
        brief_path = save_brief(t, brief_text)
        print(f"[BRIEF] {brief_path}")
    except Exception as ex:
        brief_text = None
        brief_path = None
        results["errors"].append(f"Brief generation failed: {ex}")
        print(f"[BRIEF ERROR] {ex}")

    # 8. Record diff
    if brief_text and prev_brief:
        _record_diff(prev_brief, brief_text, mode.upper())
        print(f"[DIFF] Recorded to CHANGELOG.md")

    # 9. Return summary
    h = t["model"]["hypotheses"]
    return {
        "topic": topic_name,
        "mode": mode,
        "evidence": results,
        "posteriors": {k: round(v["posterior"], 4) for k, v in h.items()},
        "expectedWeeks": t["model"]["expectedValue"],
        "governance": {
            "health": gov.get("health"),
            "entropy": round(gov.get("entropy", 0), 3),
        },
        "brief": brief_path,
        "totalEvidence": len(t.get("evidenceLog", [])),
    }


def run_epistemic_audit(topic_name: str) -> dict:
    """Run epistemic audit — read-only governance check."""
    t = load_topic(topic_name)
    freshness = audit_evidence_freshness(t)
    gov = t.get("governance", {})
    h = t["model"]["hypotheses"]

    return {
        "topic": topic_name,
        "health": gov.get("health"),
        "rt": gov.get("rt"),
        "entropy": round(gov.get("entropy", 0), 3),
        "freshness": freshness,
        "posteriors": {k: round(v["posterior"], 4) for k, v in h.items()},
        "expectedWeeks": t["model"]["expectedValue"],
        "issues": gov.get("issues", []),
    }


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NRL-Alpha Omega update pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--topic", required=True)
    parser.add_argument("--mode", default="routine", choices=["routine", "crisis"])
    parser.add_argument("--evidence", type=str, default=None,
                        help="JSON array of evidence dicts")
    parser.add_argument("--feeds", type=str, default=None,
                        help="JSON object {feed_id: value}")
    parser.add_argument("--posteriors", type=str, default=None,
                        help="JSON object {H1: p, H2: p, ...}")
    parser.add_argument("--posterior-reason", type=str, default="")
    parser.add_argument("--submodels", type=str, default=None,
                        help="JSON object {name: {scenario: prob}}")
    parser.add_argument("--submodel-reason", type=str, default="")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--orient", action="store_true")
    parser.add_argument("--backfill-scores", action="store_true",
                        help="Backfill prediction snapshots from posteriorHistory")
    parser.add_argument("--scoring-report", action="store_true",
                        help="Print calibration report")
    parser.add_argument("--resolve-outcome", type=str, default=None,
                        help="Record which hypothesis resolved (e.g., H3)")
    parser.add_argument("--compact", action="store_true",
                        help="Run evidence compaction")
    parser.add_argument("--contradictions", action="store_true",
                        help="Show unresolved contradictions")
    parser.add_argument("--auto-calibrate", action="store_true",
                        help="Run source trust auto-calibration")
    parser.add_argument("--check-expired", action="store_true",
                        help="Check for and score expired hypotheses")
    parser.add_argument("--force", action="store_true",
                        help="Acknowledge governance warnings and proceed (creates audit trail)")

    args = parser.parse_args()

    if args.orient:
        print(json.dumps(orient(args.topic), indent=2))
    elif args.audit:
        print(json.dumps(run_epistemic_audit(args.topic), indent=2))
    elif args.backfill_scores:
        from scoring import backfill_snapshots_from_history, snapshot_posteriors
        t = load_topic(args.topic)
        count = backfill_snapshots_from_history(t)
        save_topic(t)
        print(json.dumps({"backfilled": count, "total_snapshots": len(t.get("predictionScoring", {}).get("snapshots", []))}, indent=2))
    elif args.scoring_report:
        from scoring import compute_calibration_report
        t = load_topic(args.topic)
        print(json.dumps(compute_calibration_report(t), indent=2))
    elif args.resolve_outcome:
        from scoring import record_outcome
        t = load_topic(args.topic)
        record_outcome(t, args.resolve_outcome, note="CLI resolution")
        save_topic(t)
        scores = t.get("predictionScoring", {}).get("brierScores", [])
        print(json.dumps({"resolved": args.resolve_outcome, "scores_computed": len(scores)}, indent=2))
    elif args.compact:
        from compaction import auto_compact
        t = load_topic(args.topic)
        result = auto_compact(t)
        if result.get("compacted"):
            save_topic(t)
        print(json.dumps(result, indent=2))
    elif args.contradictions:
        from contradictions import get_unresolved_contradictions
        t = load_topic(args.topic)
        unresolved = get_unresolved_contradictions(t)
        print(json.dumps({"unresolved": len(unresolved), "items": unresolved}, indent=2, default=str))
    elif args.auto_calibrate:
        from source_ledger import auto_calibrate
        t = load_topic(args.topic)
        result = auto_calibrate(t)
        save_topic(t)
        print(json.dumps(result, indent=2, default=str))
    elif args.check_expired:
        from scoring import check_expired_hypotheses, record_partial_outcome
        t = load_topic(args.topic)
        expired = check_expired_hypotheses(t)
        for exp in expired:
            ps = t.get("predictionScoring", {})
            already = any(
                o.get("expired") == exp["hypothesis"]
                for o in ps.get("outcomes", [])
                if o.get("type") == "PARTIAL_EXPIRY"
            )
            if not already:
                record_partial_outcome(t, exp["hypothesis"],
                                       note=f"CLI: expired at day {exp['current_day']}")
        save_topic(t)
        print(json.dumps({"expired": expired}, indent=2, default=str))
    else:
        ev = json.loads(args.evidence) if args.evidence else None
        fd = json.loads(args.feeds) if args.feeds else None
        po = json.loads(args.posteriors) if args.posteriors else None
        sm = json.loads(args.submodels) if args.submodels else None

        result = run_update(
            args.topic,
            evidence=ev,
            feeds=fd,
            posteriors=po,
            posterior_reason=args.posterior_reason,
            submodels=sm,
            submodel_reason=args.submodel_reason,
            mode=args.mode,
            force=args.force,
        )
        print(json.dumps(result, indent=2))
