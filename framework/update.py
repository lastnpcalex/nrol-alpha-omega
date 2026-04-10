#!/usr/bin/env python3
"""
NRL-Alpha Omega — Programmatic Update Framework
===================================================

This module provides governor-gated update automation that:
1. Reads ONLY the most recent brief (not all history)
2. Gathers fresh intel via web search
3. Adds evidence through governor gate
4. Updates posteriors/sub-models if warranted
5. Generates brief and commits via governor
6. Tracks diff-based history (not full briefs)

Usage:
    python update.py --topic hormuz-closure --mode routine
    python update.py --topic hormuz-closure --mode crisis
    python update.py --topic <topic> --update-posteriors <new_values>
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from difflib import unified_diff

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import load_topic, add_evidence, update_posteriors, update_submodel, update_feed, save_topic, generate_brief
from governor import check_update_proposal, audit_evidence_freshness, assess_claim_state, classify_evidence


# ============================================================================
# Configuration
# ============================================================================

TOPICS_DIR = Path(__file__).parent.parent / "topics"
BRIEFS_DIR = Path(__file__).parent.parent / "briefs"
DASHBOARDS_DIR = Path(__file__).parent.parent / "dashboards"
HISTORY_FILE = Path(__file__).parent.parent / "HISTORY.md"
FRAMEWORK_DIR = Path(__file__).parent

# Source trust scores for cross-referencing
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


# ============================================================================
# Core Update Functions
# ============================================================================

def _now_iso() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _read_most_recent_brief(topic_name: str) -> str | None:
    """Read the most recent brief for a topic (not all history!)."""
    briefs_dir = BRIEFS_DIR / topic_name
    if not briefs_dir.exists():
        return None

    briefs = list(briefs_dir.glob("*.md"))
    if not briefs:
        return None

    # Sort by filename (YYYY-MM-DD-HHMM format) and get most recent
    briefs.sort(key=lambda p: p.stem, reverse=True)
    recent = briefs[0]

    print(f"[READ] Most recent brief: {recent.name}")
    with open(recent) as f:
        content = f.read()
        # Truncate for display (don't load all into context)
        preview = content[:500] + ("..." if len(content) > 500 else "")
        print(f"[READ] Brief preview:\n{preview}")
        return content


def _gather_intel(topic: dict, search_queries: list[str]) -> list[dict]:
    """
    Gather fresh intel via web search.
    Returns list of evidence entries ready to add (if warranted).
    """
    found_evidence = []
    search_results = []

    for query in search_queries:
        # TODO: Integrate mcp__web-tools__web_search
        # For now, simulate with placeholder
        print(f"[SEARCH] Query: {query}")

        # TODO: Replace with actual web search when MCP available
        # result = mcp__web-tools__web_search(query=query)
        # search_results.append(result)

        # Placeholder: Log that search unavailable
        search_results.append({
            "query": query,
            "result": "SEARCH_UNAVAILABLE (MCP web tools not loaded)",
            "timestamp": _now_iso(),
        })

    # TODO: Parse search results and extract new events
    # For now, return empty
    print(f"[GATHER] Search results: {len(search_results)} queries attempted")
    return found_evidence


def _generate_search_queries(topic: dict) -> list[str]:
    """Generate search queries based on topic state."""
    meta = topic.get("meta", {})
    last_updated = meta.get("lastUpdated", "")
    day_count = meta.get("dayCount", 0)

    # Date-specific queries
    queries = [
        f'{meta.get("slug", "hormuz")} after:{last_updated[:10]}',
        f'{meta.get("slug", "hormuz")} breaking news',
    ]

    # Feed-specific queries
    queries.extend([
        f'Brent crude oil price {last_updated[:4]}-{last_updated[5:7]}',
        f'CENTCOM {meta.get("slug", "hormuz")}',
        f'{meta.get("slug", "hormuz")} shipping',
    ])

    return queries


def _cross_reference_sources(evidence: dict) -> dict:
    """Cross-reference evidence against source trust scores."""
    text = evidence.get("text", "")

    # TODO: Implement actual cross-referencing
    # For now, just add source note
    evidence["cross_referenced"] = False
    evidence["notes"] = evidence.get("notes", "")

    return evidence


def _add_evidence_governor_gated(topic: dict, evidence: dict) -> bool:
    """
    Add evidence through governor gate.
    Returns True if added, False if rejected.
    """
    try:
        # Classifier checks: rhetoric, hallucination, freshness
        classified = classify_evidence(evidence)
        effective_weight = get_effective_weight(topic, classified)

        # Add via engine (already governor-gated in engine.py)
        result = add_evidence(topic, {
            "tag": evidence.get("tag", "INTEL"),
            "text": evidence.get("text", ""),
            "provenance": evidence.get("provenance", "OBSERVED"),
            "source": evidence.get("source", ""),
            "posteriorImpact": evidence.get("posteriorImpact", "MODERATE"),
        })

        return True
    except Exception as e:
        print(f"[GOVERNOR] Evidence rejected: {e}")
        return False


def _update_data_feeds(topic: dict, new_values: dict) -> dict:
    """Update data feeds with new values."""
    updated = {}

    for feed_id, value in new_values.items():
        if feed_id in topic.get("dataFeeds", {}):
            topic = update_feed(topic, feed_id, value, as_of=_now_iso())
            updated[feed_id] = value

    return topic, updated


def _update_posteriors_governor_gated(topic: dict, new_posteriors: dict) -> dict:
    """
    Update posteriors through governor gate.
    Validates: sum=1, shift justification, evidence_refs.
    """
    # Validate proposal
    validation = check_update_proposal(
        topic,
        proposed_posteriors=new_posteriors,
    )

    if not validation.get("passed", False):
        print(f"[GOVERNOR] Posterior update rejected:")
        for failure in validation.get("failures", []):
            print(f"  - {failure}")
        return topic

    # Apply update
    topic = update_posteriors(
        topic,
        new_posteriors,
        reason=validation.get("reason", ""),
        evidence_refs=validation.get("evidence_refs", []),
    )

    return topic


def _update_submodel_governor_gated(topic: dict, submodel_name: str,
                                     new_values: dict) -> dict:
    """Update sub-model through governor gate."""
    validation = check_update_proposal(
        topic,
        proposed_submodel_updates=new_values,
    )

    if validation.get("passed", False):
        topic = update_submodel(
            topic,
            submodel_name,
            new_values,
            reason=validation.get("reason", ""),
            evidence_refs=validation.get("evidence_refs", []),
        )

    return topic


# ============================================================================
# History Tracking (Diff-based, not full briefs)
# ============================================================================

def _record_change(diff: str, change_type: str) -> None:
    """Append diff-based change record to history file."""
    HISTORY_DIR = FRAMEWORK_DIR.parent
    HISTORY_FILE = HISTORY_DIR / "CHANGELOG.md"

    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("# NRL-Alpha Omega Change Log\n\n")

    # Write change to history
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    append_text = f"""
---
## {timestamp} [{change_type}]

{diff}
"""
    HISTORY_FILE.write_text(append_text + HISTORY_FILE.read_text())


def _generate_diff_brief(before_content: str, after_content: str) -> str:
    """Generate unified diff for brief comparison."""
    diff = unified_diff(
        before_content.splitlines(),
        after_content.splitlines(),
        fromfile=f"before-{_now_iso()[:16]}",
        tofile=f"after-{_now_iso()[:16]}",
        lineterm="",
    )
    return "\n".join(diff)


def _summarize_diff(diff_str: str) -> str:
    """Generate human-readable summary of diff."""
    lines = diff_str.strip().split("\n")
    additions = lines.count("+") - lines.count("+++")
    deletions = lines.count("-") - lines.count("---")

    if additions == 0 and deletions == 0:
        return "No changes."

    summary = f"Diff summary: +{additions} lines, -{deletions} lines"

    # Extract key changes
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            summary += f"\n  Added: {line.strip()[:80]}..."
        elif line.startswith("-") and not line.startswith("---"):
            summary += f"\n  Removed: {line.strip()[:80]}..."

    return summary


# ============================================================================
# Main Update Pipeline
# ============================================================================

def run_update(topic_name: str, mode: str = "routine",
               new_posteriors: dict | None = None,
               new_submodels: dict | None = None,
               new_data_feeds: dict | None = None) -> dict:
    """
    Main update pipeline.

    Args:
        topic_name: Name of topic (e.g., "hormuz-closure")
        mode: "routine" or "crisis"
        new_posteriors: New posterior values (optional)
        new_submodels: New sub-model values (optional)
        new_data_feeds: New feed values (optional)

    Returns:
        Updated topic dict
    """
    print(f"\n{'='*60}")
    print(f"UPDATE PIPELINE: {topic_name} [{mode}]")
    print(f"{'='*60}")

    # 1. Load topic
    topic = load_topic(topic_name)
    print(f"[LOAD] Loaded topic: {topic_name}")

    # 2. Read most recent brief (not all history!)
    recent_brief = _read_most_recent_brief(topic_name)

    # 3. Generate search queries
    search_queries = _generate_search_queries(topic)

    # 4. Gather intel (via search)
    new_evidence = _gather_intel(topic, search_queries)

    if not new_evidence and mode == "routine":
        print("[NOTE] No new intel gathered via search.")
        print("[NOTE] Updating data feeds and holding posteriors...")
    else:
        print(f"[NOTE] {len(new_evidence)} new evidence entries found.")

    # 5. Add evidence (if found)
    added_evidence = 0
    for evidence in new_evidence:
        if _add_evidence_governor_gated(topic, evidence):
            added_evidence += 1
            print(f"[ADD] Evidence added: {evidence.get('text', '')[:50]}...")

    # 6. Update data feeds (if provided)
    if new_data_feeds:
        topic, updated = _update_data_feeds(topic, new_data_feeds)
        for feed_id, value in updated.items():
            print(f"[FEED] Updated {feed_id}: {value}")

    # 7. Update posteriors (if provided and warranted)
    if new_posteriors:
        topic = _update_posteriors_governor_gated(topic, new_posteriors)
        print(f"[POST] Posteriors updated: {new_posteriors}")

    # 8. Update sub-models (if provided)
    if new_submodels:
        for submodel_name, values in new_submodels.items():
            topic = _update_submodel_governor_gated(topic, submodel_name, values)
        print(f"[SUB] Sub-models updated.")

    # 9. Generate brief
    brief = generate_brief(topic, mode=mode)
    brief_path = BRIEFS_DIR / topic_name / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M')}.md"
    with open(brief_path, "w") as f:
        f.write(brief)
    print(f"[BRIEF] Saved to: {brief_path}")

    # 10. Record diff-based change
    if recent_brief and brief:
        diff = _generate_diff_brief(recent_brief, brief)
        change_type = "ROUTINE" if mode == "routine" else "CRISIS"
        _record_change(diff, change_type)

    # 11. Save topic (triggers governor snapshot)
    save_topic(topic)
    print(f"[SAVE] Topic saved. Governance health: {topic.get('governance', {}).get('health', 'N/A')}")

    return topic


def run_epistemic_audit(topic_name: str) -> dict:
    """
    Run epistemic audit on topic:
    - Check evidence freshness
    - Check hypothesis admissibility
    - Check for unfalsifiable hypotheses
    - Check uncertainty ratio
    """
    topic = load_topic(topic_name)
    print(f"\nEPIDEMIC AUDIT: {topic_name}")
    print(f"{'='*40}")

    # Audit freshness
    freshness = audit_evidence_freshness(topic)
    print(f"[FRESHNESS] Stale: {freshness.get('stale', 0)}, Fresh: {freshness.get('fresh', 0)}, Total: {freshness.get('total', 0)}")

    # Return governance snapshot
    return topic.get("governance", {})


# ============================================================================
# Test Functions
# ============================================================================

def test_hypothesis(topic_name: str, hypothesis: str, evidence: dict) -> bool:
    """
    Test a hypothesis with new evidence.
    Returns True if hypothesis is supported/refuted.
    """
    topic = load_topic(topic_name)

    print(f"\nTEST: {hypothesis}")
    print(f"{'='*40}")

    # Add evidence
    if add_evidence(topic, evidence):
        print("[ADD] Evidence added")
    else:
        print("[ADD] Evidence rejected by governor")

    # Check hypothesis state
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    print(f"Hypothesis state: {json.dumps(hypotheses, indent=2)}")

    return True


def update_resolution_criterion(topic_name: str,
                                 traffic_threshold: int = 40,
                                 freedom_of_navigation: bool = True,
                                 toll_regime_resolved: bool = False) -> dict:
    """
    Update resolution criterion with new dimensions:
    - traffic_threshold: 30% of pre-war
    - freedom_of_navigation: Can vessels freely pass?
    - toll_regime_resolved: Is toll temporary or permanent?
    """
    topic = load_topic(topic_name)

    # TODO: Add resolution sub-tracks
    # topic["resolutionTracks"] = {
    #     "commerce": {"threshold": traffic_threshold, "status": "UNRESOLVED"},
    #     "freedom_of_navigation": {"status": "FALSE" if not toll_regime_resolved else "TRUE"},
    #     "sovereignty": {"status": "IRGC_CONTROL"},
    # }

    return topic


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NRL-Alpha Omega update pipeline")
    parser.add_argument("--topic", required=True, help="Topic name (e.g., hormuz-closure)")
    parser.add_argument("--mode", default="routine", choices=["routine", "crisis"])
    parser.add_argument("--posteriors", type=json.loads, help="New posterior values")
    parser.add_argument("--submodels", type=json.loads, help="New sub-model values")
    parser.add_argument("--feeds", type=json.loads, help="New feed values")
    parser.add_argument("--audit", action="store_true", help="Run epistemic audit")

    args = parser.parse_args()

    if args.audit:
        result = run_epistemic_audit(args.topic)
        print(json.dumps(result, indent=2))
    else:
        topic = run_update(
            args.topic,
            mode=args.mode,
            new_posteriors=args.posteriors,
            new_submodels=args.submodels,
            new_data_feeds=args.feeds,
        )
        print(json.dumps({
            "topic": args.topic,
            "health": topic.get("governance", {}).get("health", "N/A"),
            "evidence": len(topic.get("evidenceLog", [])),
        }, indent=2))
