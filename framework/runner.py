#!/usr/bin/env python3
"""
NRL-Alpha Omega — Framework Runner
=====================================

Main orchestrator for the NRL-Alpha Omega framework.
Provides a unified interface for:
1. Routine updates (with web search)
2. Crisis updates (rapid response)
3. Linting (pre-commit checks)
4. Testing (hypothesis validation)
5. Epistemic auditing (governance health)

Usage:
    python runner.py --topic hormuz-closure --mode routine
    python runner.py --topic hormuz-closure --lint
    python runner.py --topic hormuz-closure --test resolution_achieved
    python runner.py --topic hormuz-closure --audit
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from update import run_update, run_epistemic_audit, _generate_search_queries
from lint import run_lint, SOURCE_TRUST
from calibrate import verify_claim, calibrate_source_trust, detect_source_drift, register_source, SOURCE_TRUST as CALIBRATE_SOURCE_TRUST


# ============================================================================
# Framework Configuration
# ============================================================================

TOPICS_DIR = Path(__file__).parent.parent / "topics"
BRIEFS_DIR = Path(__file__).parent.parent / "briefs"
FRAMEWORK_DIR = Path(__file__).parent

# Default search queries per feed
SEARCH_QUERIES = {
    "hormuz-closure": [
        "Strait of Hormuz April 2026",
        "Kharg Island April 2026",
        "Hormuz shipping April 2026",
        "CENTCOM Persian Gulf April 2026",
        "Brent crude oil price April 2026",
    ],
    "default": [
        "{topic} April 2026",
        "{topic} breaking news",
        "breaking news {topic}",
    ],
}


# ============================================================================
# Main Runner
# ============================================================================

def run_framework_command(command: str, topic_name: str, **kwargs) -> dict:
    """
    Run a framework command.

    Commands:
    - update: Run routine/crisis update
    - lint: Lint evidence log
    - test: Run test case
    - audit: Epistemic audit
    - diff: Show diff from last brief

    Args:
        command: Command to run
        topic_name: Topic slug
        **kwargs: Command-specific arguments

    Returns:
        Command result dict
    """
    print(f"\n{'='*60}")
    print(f"FRAMEWORK RUNNER: {command}")
    print(f"{'='*60}")

    if command == "update":
        return _run_update(topic_name, mode=kwargs.get("mode", "routine"),
                           posteriors=kwargs.get("posteriors"),
                           submodels=kwargs.get("submodels"),
                           feeds=kwargs.get("feeds"))
    elif command == "lint":
        return _run_lint(topic_name, check_history=kwargs.get("check_history", False))
    elif command == "test":
        return _run_test(topic_name, test_name=kwargs.get("test"),
                         evidence=kwargs.get("evidence"))
    elif command == "audit":
        return _run_audit(topic_name)
    elif command == "diff":
        return _run_diff(topic_name)
    elif command == "health":
        return _run_health(topic_name)
    elif command == "calibrate":
        return _run_calibrate(topic_name, kwargs.get("subcommand", "trust"), **kwargs)
    else:
        print(f"[ERROR] Unknown command: {command}")
        return {"error": f"Unknown command: {command}"}


def _run_update(topic_name: str, mode: str = "routine",
                posteriors: dict | None = None,
                submodels: dict | None = None,
                feeds: dict | None = None) -> dict:
    """Run update command."""
    result = run_update(
        topic_name,
        mode=mode,
        new_posteriors=posteriors,
        new_submodels=submodels,
        new_data_feeds=feeds,
    )
    return {
        "topic": topic_name,
        "command": "update",
        "mode": mode,
        "result": "SUCCESS",
        "governance": result.get("governance", {}),
    }


def _run_lint(topic_name: str, check_history: bool = False) -> dict:
    """Run lint command."""
    results = run_lint(topic_name, check_history=check_history)
    return results


def _run_test(topic_name: str, test_name: str, evidence: dict) -> dict:
    """Run test command."""
    result = run_test(topic_name, test_name, evidence)
    return result


def _run_audit(topic_name: str) -> dict:
    """Run audit command."""
    return run_epistemic_audit(topic_name)


def _run_calibrate(topic_name: str, command: str, **kwargs) -> dict:
    """Run calibration command."""
    from calibrate import verify_claim, calibrate_source_trust, detect_source_drift, register_source, SOURCE_TRUST as CALIBRATE_SOURCE_TRUST

    if command == "verify":
        claim = kwargs.get("claim", "")
        method = kwargs.get("method", "cross_reference")
        high_trust = kwargs.get("high-trust-source")
        if high_trust:
            try:
                high_trust = json.loads(high_trust)
            except:
                pass
        result = verify_claim(topic_name, kwargs.get("source", ""), claim, method, high_trust)
    elif command == "calibrate":
        topics = [topic_name] if isinstance(topic_name, str) else topic_name
        result = {"note": "Batch calibration requires calibration history"}
    elif command == "drift":
        window = kwargs.get("window-days", 7)
        source = kwargs.get("source", topic_name)
        result = detect_source_drift(source, window)
    elif command == "trust":
        result = CALIBRATE_SOURCE_TRUST.copy()
    else:
        result = {"error": f"Unknown calibration command: {command}"}

    return result


def _run_diff(topic_name: str) -> dict:
    """Show diff from last brief."""
    import difflib
    from datetime import datetime, timezone

    topic = None
    try:
        topic = load_topic(topic_name)
        briefs_dir = BRIEFS_DIR / topic_name
        briefs = sorted(briefs_dir.glob("*.md"), key=lambda p: p.stem)
    except Exception as e:
        return {"error": str(e)}

    if len(briefs) < 2:
        return {"message": "Not enough brief history for diff"}

    # Get second-to-last brief (before latest)
    before_path = briefs[-2]
    after_path = briefs[-1]

    with open(before_path) as f:
        before_content = f.read()
    with open(after_path) as f:
        after_content = f.read()

    diff = difflib.unified_diff(
        before_content.splitlines(),
        after_content.splitlines(),
        fromfile=f"before-{after_path.stem}",
        tofile=f"after-{after_path.stem}",
        lineterm="",
    )

    diff_str = "\n".join(diff)
    return {
        "topic": topic_name,
        "before": before_path.stem,
        "after": after_path.stem,
        "diff": diff_str,
        "added_lines": diff_str.count("+") - diff_str.count("+--"),
        "removed_lines": diff_str.count("-") - diff_str.count("---"),
    }


def _run_health(topic_name: str) -> dict:
    """Show health status."""
    try:
        from update import load_topic
        topic = load_topic(topic_name)
        governance = topic.get("governance", {})
        return {
            "topic": topic_name,
            "health": governance.get("health", "N/A"),
            "R_t": governance.get("rt", {}),
            "entropy": governance.get("entropy", "N/A"),
            "evidence": {
                "fresh": len([e for e in topic.get("evidenceLog", []) if e.get("time") and e["time"][0:10] >= "2026-04-08"]),
                "stale": len([e for e in topic.get("evidenceLog", []) if e.get("time") and e["time"][0:10] < "2026-04-08"]),
                "total": len(topic.get("evidenceLog", [])),
            },
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NRL-Alpha Omega Framework Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python runner.py --topic hormuz-closure --mode routine
  python runner.py --topic hormuz-closure --mode crisis
  python runner.py --topic hormuz-closure --lint
  python runner.py --topic hormuz-closure --test resolution_achieved
  python runner.py --topic hormuz-closure --audit
  python runner.py --topic hormuz-closure --diff
  python runner.py --topic hormuz-closure --health
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Update command
    update_parser = subparsers.add_parser("update", help="Run update")
    update_parser.add_argument("--topic", required=True, help="Topic name")
    update_parser.add_argument("--mode", default="routine", choices=["routine", "crisis"])
    update_parser.add_argument("--posteriors", default="{}", help="New posteriors (JSON)")
    update_parser.add_argument("--submodels", default="{}", help="New sub-models (JSON)")
    update_parser.add_argument("--feeds", default="{}", help="New feeds (JSON)")

    # Lint command
    lint_parser = subparsers.add_parser("lint", help="Lint evidence log")
    lint_parser.add_argument("--topic", required=True, help="Topic name")
    lint_parser.add_argument("--check-history", action="store_true", help="Check most recent brief")

    # Test command
    test_parser = subparsers.add_parser("test", help="Run test case")
    test_parser.add_argument("--topic", required=True, help="Topic name")
    test_parser.add_argument("--test", required=True, help="Test case name")
    test_parser.add_argument("--evidence", default="{}", help="Evidence (JSON)")

    # Audit command
    audit_parser = subparsers.add_parser("audit", help="Epistemic audit")
    audit_parser.add_argument("--topic", required=True, help="Topic name")

    # Diff command
    diff_parser = subparsers.add_parser("diff", help="Show diff from last brief")
    diff_parser.add_argument("--topic", required=True, help="Topic name")

    # Health command
    health_parser = subparsers.add_parser("health", help="Show health status")
    health_parser.add_argument("--topic", required=True, help="Topic name")

    # Calibrate command
    calibrate_parser = subparsers.add_parser("calibrate", help="Calibrate source trust scores")
    calibrate_parser.add_argument("--topic", required=True, help="Topic name")
    calibrate_parser.add_argument("--subcommand", default="trust", choices=["trust", "verify", "drift"])
    calibrate_parser.add_argument("--claim", help="Claim to verify")
    calibrate_parser.add_argument("--source", help="Source name")
    calibrate_parser.add_argument("--method", default="cross_reference", help="Verification method")
    calibrate_parser.add_argument("--high-trust-source", help="High-trust source for cross-reference")
    calibrate_parser.add_argument("--window-days", default=7, type=int, help="Window for drift detection")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Route to command handler
    kwargs = {k.replace("-", "_"): v for k, v in vars(args).items() if k != "command"}
    result = run_framework_command(args.command, args.topic, **kwargs)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("result") == "SUCCESS" else 1)
