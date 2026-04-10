#!/usr/bin/env python3
"""
NRL-Alpha Omega — Lint Module
Linting functions for failure mode detection.
"""

import sys
import datetime
from datetime import datetime, timezone
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine import load_topic

# Source trust scores
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

FAILURE_MODES = [
    {
        "id": "rhetoric_as_evidence",
        "name": "Rhetoric as Evidence",
        "pattern": r"(Iran|Trump|US).*will.*(attack|open|close)",
        "action": "TAG AS RHETORIC",
    },
    {
        "id": "recycled_intel",
        "name": "Recycled Intel",
        "pattern": None,
        "action": "FLAG FOR REVIEW",
    },
    {
        "id": "empty_search_not_logged",
        "name": "Empty Search Not Logged",
        "pattern": r"No new intel",
        "action": "VERIFY SEARCH PERFORMED",
    },
    {
        "id": "feed_key_mismatch",
        "name": "Feed Key Mismatch",
        "pattern": r"(brentCrude|hormuzTraffic|hormuz\.traffic)",
        "action": "CORRECT TO: {feed_id}_underscore",
    },
    {
        "id": "stale_evidence",
        "name": "Stale Evidence",
        "pattern": None,
        "action": "FLAG FOR FRESHNESS REVIEW",
    },
    {
        "id": "anchoring_bias",
        "name": "Anchoring Bias",
        "pattern": r"HOLD.*unchanged",
        "action": "REQUIRE SHIFT RATIONALE",
    },
    {
        "id": "phantom_precision",
        "name": "Phantom Precision",
        "pattern": r"\d+\.?\d{2,}",
        "action": "ROUND TO APPROPRIATE SIGNIFICANCE",
    },
]


def lint_evidence_log(topic_name, topic):
    """Lint evidence log for failure modes."""
    issues = []
    evidence_log = topic.get("evidenceLog", [])

    for i, evidence in enumerate(evidence_log):
        text = evidence.get("text", "").lower()
        for fm in FAILURE_MODES:
            if not fm["pattern"]:
                continue
            if re.search(fm["pattern"], text, re.IGNORECASE):
                issues.append({
                    "entry_index": i,
                    "failure_mode": fm["id"],
                    "failure_name": fm["name"],
                    "text_preview": evidence.get("text", "")[:100],
                    "suggested_action": fm["action"],
                    "severity": "HIGH" if "rhetoric" in fm["id"] else "MEDIUM",
                })

    freshness_issues = _check_freshness(evidence_log)
    issues.extend(freshness_issues)

    return issues


def _check_freshness(evidence_log):
    """Check for stale evidence (>7 days)."""
    from datetime import datetime, timezone
    issues = []

    for evidence in evidence_log:
        time_str = evidence.get("time", "")
        if not time_str:
            continue
        try:
            time_dt = datetime.fromisoformat(time_str.replace("+00:00", "+0000"))
            age_hours = (datetime.now(timezone.utc) - time_dt).total_seconds() / 3600
            if age_hours > 168:
                issues.append({
                    "entry_index": len([e for e in evidence_log if e.get("time")]),
                    "age_hours": round(age_hours, 1),
                    "failure_mode": "stale_evidence",
                    "suggested_action": "FLAG FOR FRESHNESS REVIEW",
                    "severity": "MEDIUM" if age_hours < 336 else "HIGH",
                })
        except (ValueError, TypeError):
            continue

    return issues


def lint_resolution_criterion(topic):
    """Check resolution criterion for epistemological completeness."""
    topic_name = topic.get("meta", {}).get("slug", "hormuz-closure")
    traffic_threshold = 40
    resolution_criterion = topic.get("resolution", "Sustained >30% of pre-war Hormuz traffic")

    issues = []
    if "toll" not in resolution_criterion.lower() and "sovereignty" not in resolution_criterion.lower():
        issues.append({
            "type": "resolution_criterion_incomplete",
            "message": f"Resolution criterion doesn't account for toll regime: {resolution_criterion}",
            "suggested_action": "Add toll regime / sovereignty dimensions",
        })

    return {"topic": topic_name, "resolution_criterion": resolution_criterion, "issues": issues,
            "status": "NEEDS_UPDATE" if issues else "ADEQUATE"}


def lint_submodels(topic):
    """Check sub-models for completeness."""
    submodels = topic.get("subModels", {})
    issues = []

    expected_submodels = ["meuMission", "trumpUltimatum", "talksTrack"]
    missing = [m for m in expected_submodels if m not in submodels]

    if missing:
        issues.append({
            "type": "submodel_missing",
            "message": f"Missing sub-models: {missing}",
            "suggested_action": f"Create sub-models: {', '.join(missing)}",
        })

    return {
        "topic": topic.get("meta", {}).get("slug", ""),
        "submodels": list(submodels.keys()),
        "issues": issues,
        "status": "NEEDS_UPDATE" if issues else "COMPLETE",
    }


def run_lint(topic_name, check_history=False):
    """Run full lint on topic."""
    topic = load_topic(topic_name)

    results = {
        "topic": topic_name,
        "timestamp": datetime.now().isoformat(),
        "lint_issues": [],
        "issues_count": 0,
    }

    evidence_issues = lint_evidence_log(topic_name, topic)
    results["lint_issues"].extend(evidence_issues)
    results["issues_count"] += len(evidence_issues)

    resolution_check = lint_resolution_criterion(topic)
    results["resolution_criterion"] = resolution_check
    results["lint_issues"].extend(resolution_check.get("issues", []))
    results["issues_count"] += len(resolution_check.get("issues", []))

    submodel_check = lint_submodels(topic)
    results["submodel_check"] = submodel_check
    results["lint_issues"].extend(submodel_check.get("issues", []))
    results["issues_count"] += len(submodel_check.get("issues", []))

    results["status"] = "CLEAN" if results["issues_count"] == 0 else "ISSUES_FOUND"

    return results


def lint_brief(brief_path):
    """Lint a brief for common issues."""
    import json
    issues = []

    with open(brief_path) as f:
        content = f.read()

    if "No new intel" in content or "No new developments" in content:
        if "searched:" not in content.lower():
            issues.append({
                "type": "empty_search_not_logged",
                "message": "Brief states 'No new intel' but doesn't document search.",
            })

    if re.search(r"brentCrude|hormuzTraffic", content, re.IGNORECASE):
        issues.append({
            "type": "feed_key_mismatch",
            "message": "Brief uses camelCase feed keys.",
        })

    return issues


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NRL-Alpha Omega lint module")
    parser.add_argument("--topic", required=True, help="Topic name")
    parser.add_argument("--check-history", action="store_true", help="Check most recent brief")

    args = parser.parse_args()

    results = run_lint(args.topic, check_history=args.check_history)
    print(json.dumps(results, indent=2))

    if results["issues_count"] > 0:
        print(f"\n[WARNING] Found {results['issues_count']} issues.")
