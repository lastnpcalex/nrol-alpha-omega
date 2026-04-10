#!/usr/bin/env python3
"""
NRL-Alpha Omega — Test Module
==============================

Systematic hypothesis testing framework:
1. Load hypothesis state
2. Gather test evidence
3. Lint test evidence (failure modes check)
4. Add evidence via governor gate
5. Update posteriors if warranted
6. Record test result

Usage:
    python test.py --topic hormuz-closure --hypothesis "resolution_achieved" --evidence "..."
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import load_topic, add_evidence, update_posteriors, save_topic
from lint import run_lint, lint_evidence_log, FAILURE_MODES


# ============================================================================
# Test Case Registry
# ============================================================================

TEST_CASES = {
    "resolution_achieved": {
        "name": "Resolution Criterion Met",
        "description": "Sustained >30% of pre-war Hormuz traffic (~40 transits/day)",
        "required_evidence": ["traffic count >40", "freedom of navigation", "no toll regime enforcement"],
        "hypotheses_affected": ["H3", "H4"],
        "evidence_weight": 1.0,
    },
    "toll_regime_active": {
        "name": "Toll Regime Operational",
        "description": "Iran charging tolls for Hormuz passage (Fortune/Daily Mail/CBS confirmation)",
        "required_evidence": ["toll collection evidence", "multiple sources"],
        "hypotheses_affected": ["H4"],
        "evidence_weight": 0.5,
    },
    "iceland_seized": {
        "name": "Kharg Island Seized",
        "description": "US forces seized and hold Kharg Island",
        "required_evidence": ["seizure confirmed", "holding confirmed"],
        "hypotheses_affected": ["H2", "H3"],
        "evidence_weight": 1.0,
    },
    "ceasefire_active": {
        "name": "Ceasefire in Effect",
        "description": "Pakistan-brokered ceasefire active (no combat operations)",
        "required_evidence": ["no combat reported", "diplomatic channels open"],
        "hypotheses_affected": ["all"],
        "evidence_weight": 0.3,
    },
    "freedom_of_navigation": {
        "name": "Freedom of Navigation",
        "description": "Vessels can freely pass without Iranian permission",
        "required_evidence": ["no toll enforcement", "Western ships transiting freely"],
        "hypotheses_affected": ["H3", "H4"],
        "evidence_weight": 1.0,
    },
}


# ============================================================================
# Test Runner
# ============================================================================

def run_test(topic_name: str, test_name: str, evidence: dict) -> dict:
    """
    Run a test case on topic.

    Args:
        topic_name: Topic slug
        test_name: Test case name (or see TEST_CASES registry)
        evidence: Evidence dict to add during test

    Returns:
        Test result dict
    """
    topic = load_topic(topic_name)
    test_case = TEST_CASES.get(test_name, {})

    # Load hypothesis state before test
    hypotheses_before = topic.get("model", {}).get("hypotheses", {})
    submodels_before = topic.get("subModels", {})

    result = {
        "topic": topic_name,
        "test": test_name,
        "timestamp": "2026-04-10T21:00:00+00:00",
        "test_case": test_case,
        "result": "PENDING",
    }

    # Step 1: Gather evidence
    print(f"[TEST] Gathering evidence for: {test_name}")
    print(f"[TEST] Test case: {test_case.get('name', 'N/A')}")

    # Step 2: Lint evidence
    print("[TEST] Linting evidence for failure modes...")
    lint_result = lint_evidence_log(topic_name, topic)
    result["lint_issues"] = lint_result

    if lint_result:
        print("[TEST] Issues found during lint:")
        for issue in lint_result:
            print(f"  - {issue['failure_name']}: {issue['suggested_action']}")
        print("[TEST] Proceeding anyway (manual override)")
    else:
        print("[TEST] Evidence passed lint check.")

    # Step 3: Add evidence via governor
    print(f"[TEST] Adding evidence: {evidence.get('text', 'N/A')[:100]}...")

    evidence_entry = {
        "tag": evidence.get("tag", "INTEL"),
        "text": evidence.get("text", ""),
        "provenance": evidence.get("provenance", "OBSERVED"),
        "source": evidence.get("source", ""),
        "posteriorImpact": evidence.get("posteriorImpact", test_case.get("evidence_weight", 0.5)),
    }

    try:
        # Add evidence (governor-gated)
        add_evidence(topic, evidence_entry)
        print("[TEST] Evidence added successfully.")
        result["result"] = "PASSED"
    except Exception as e:
        print(f"[TEST] Evidence rejected: {e}")
        result["result"] = "REJECTED"
        result["rejection_reason"] = str(e)

    # Step 4: Update posteriors if warranted
    if result["result"] == "PASSED":
        print("[TEST] Evaluating posterior shift...")

        # Get affected hypotheses
        affected = test_case.get("hypotheses_affected", [])
        print(f"[TEST] Affected hypotheses: {affected}")

        # TODO: Implement actual posterior update logic
        # For now, just update with test evidence weight
        new_posteriors = _compute_posterior_shift(
            topic,
            evidence_entry,
            affected,
        )

        if new_posteriors:
            try:
                update_posteriors(topic, new_posteriors, reason=f"Test {test_name} evidence")
                print("[TEST] Posteriors updated.")
            except Exception as e:
                print(f"[TEST] Posterior update rejected: {e}")

        # Step 5: Update sub-models if needed
        if test_name == "resolution_achieved" or test_name == "freedom_of_navigation":
            result["submodel_updates"] = _compute_submodel_updates(topic)
            print("[TEST] Sub-models would be updated if resolution achieved.")

    # Step 6: Record result
    result["hypotheses_before"] = hypotheses_before
    result["hypotheses_after"] = topic.get("model", {}).get("hypotheses", {})

    return result


def _compute_posterior_shift(topic: dict, evidence: dict, affected: list[str]) -> dict | None:
    """
    Compute posterior shift based on evidence.
    Returns dict of new posteriors if shift warranted, None otherwise.
    """
    hypotheses = topic.get("model", {}).get("hypotheses", {})

    # Simple shift logic (would be more sophisticated)
    shifts = {}
    posteriorImpact = float(evidence.get("posteriorImpact", 0.5))

    # H4 up if toll regime / resistance to resolution
    if any("toll" in k.lower() for k in TEST_CASES.keys()):
        if "H4" in affected:
            current = hypotheses.get("H4", {}).get("posterior", 0.27)
            shifts["H4"] = min(1.0, current + 0.02 * posteriorImpact)

    # H3 down if resolution achieved
    if any("resolution" in k.lower() or "freedom" in k.lower() for k in TEST_CASES.keys()):
        if "H3" in affected:
            current = hypotheses.get("H3", {}).get("posterior", 0.5)
            shifts["H3"] = max(0.01, current - 0.02 * posteriorImpact)

    # Normalize to sum to 1
    total = sum(shifts.get(h, hypotheses.get(h, {}).get("posterior", 0)) for h in hypotheses)
    remaining = 1.0 - total

    # Distribute remainder to H1-H2
    if shifts:
        shifts["H1"] = hypotheses.get("H1", {}).get("posterior", 0.01)
        shifts["H2"] = hypotheses.get("H2", {}).get("posterior", 0.22)
        shifts["H3"] = shifts.get("H3", hypotheses.get("H3", {}).get("posterior", 0.5))
        shifts["H4"] = shifts.get("H4", hypotheses.get("H4", {}).get("posterior", 0.27))

        total = sum(shifts.values())
        shifts["H1"] += remaining * 0.3
        shifts["H2"] += remaining * 0.7

    return shifts if shifts else None


def _compute_submodel_updates(topic: dict) -> dict | None:
    """
    Compute sub-model updates for resolution achievement.
    """
    submodels = topic.get("subModels", {}).get("meuMission", {}).get("scenarios", {})

    if submodels.get("kharg", {}).get("prob", 0) > 0.5:
        return {
            "meuMission": {
                "scenarios": {
                    "kharg": {"prob": 0.85},  # Seizure more likely
                    "larak": {"prob": 0.05},  # Toll regime less likely
                    "escort": {"prob": 0.05},  # Escort less likely
                    "declareVictory": {"prob": 0.05},
                    "groundEscalation": {"prob": 0.00},
                },
            }
        }

    return None


def list_test_cases() -> dict:
    """List available test cases."""
    return TEST_CASES


def run_all_tests(topic_name: str, test_names: list[str] | None = None) -> list[dict]:
    """
    Run all or specified test cases on topic.
    """
    if test_names is None:
        test_names = list(TEST_CASES.keys())

    results = []
    for test_name in test_names:
        if test_name in TEST_CASES:
            result = run_test(topic_name, test_name, {})
            results.append(result)
        else:
            print(f"[TEST] Unknown test: {test_name}")

    return results


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NRL-Alpha Omega test module")
    parser.add_argument("--topic", required=True, help="Topic name")
    parser.add_argument("--test", required=True, help="Test case name")
    parser.add_argument("--evidence", default="{}", help="Evidence JSON")
    parser.add_argument("--list", action="store_true", help="List test cases")

    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_test_cases(), indent=2))
    else:
        evidence = json.loads(args.evidence)
        result = run_test(args.topic, args.test, evidence)
        print(json.dumps(result, indent=2))
