#!/usr/bin/env python3
"""
NRL-Alpha Omega — Source Calibration Module
==========================================

Calibrate and verify source trust scores over time:
1. Track source performance (hit rate, false positive rate)
2. Verify source claims against higher-trust sources
3. Update trust scores based on empirical calibration
4. Detect source drift (changing accuracy over time)
5. Handle new sources
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine import load_topic, add_evidence, update_feed, save_topic


# ============================================================================
# Current Source Trust Scores (calibrated baseline)
# ============================================================================

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
    "IranianEmbassy": 0.30,  # State media, lower trust
}


# ============================================================================
# Calibration State Storage
# ============================================================================

# Default calibration directory
CALIBRATION_DIR = Path(__file__).parent.parent / "calibration"


def _init_calibration_dir():
    """Initialize calibration storage directory."""
    CALIBRATION_DIR.mkdir(exist_ok=True)

# ============================================================================
# Core Calibration Functions
# ============================================================================

def verify_claim(topic_name: str, source_name: str, claim: str,
                 verification_method: str, high_trust_source: dict | None = None) -> dict:
    """
    Verify a claim from a source using higher-trust sources or cross-reference.

    Args:
        topic_name: Topic slug (for context)
        source_name: Source making the claim
        claim: The claim being verified
        verification_method: "cross_reference" or "expert" or "automated"
        high_trust_source: dict with source and claim if using higher-trust verification

    Returns:
        Verification result with calibrated data
    """
    topic = load_topic(topic_name)

    result = {
        "topic": topic_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source_name,
        "claim": claim,
        "verification_method": verification_method,
        "result": "PENDING",
        "calibration_data": {},
    }

    if verification_method == "cross_reference":
        # Cross-reference with higher-trust source
        result = _verify_cross_reference(topic, source_name, claim, high_trust_source)

    elif verification_method == "expert":
        # Expert judgment (manual override)
        result = _verify_expert_judgment(topic, source_name, claim)

    elif verification_method == "automated":
        # Automated verification (e.g., oil prices from multiple sources)
        result = _verify_automated(topic, source_name, claim)

    else:
        result["error"] = f"Unknown verification method: {verification_method}"

    # Log calibration attempt
    _log_calibration_attempt(source_name, claim, result)

    return result


def _verify_cross_reference(topic, source_name: str, claim: str,
                           high_trust_source: dict | None = None) -> dict:
    """
    Cross-reference claim with higher-trust source.
    """
    result = {
        "result": "PENDING",
        "high_trust_source": high_trust_source,
        "comparison": {},
    }

    if high_trust_source:
        high_source_name = high_trust_source.get("source", "N/A")
        high_source_claim = high_trust_source.get("claim", claim)

        # TODO: Implement actual cross-reference logic
        # For now, simulate with placeholder
        if "verified" in claim.lower():
            result["result"] = "VERIFIED"
            result["calibration_data"] = {
                "discrepancy": 0,
                "confidence": 0.9,
                "notes": "Cross-referenced and verified",
            }

        elif high_source_claim != claim:
            result["result"] = "CONFLICT"
            result["calibration_data"] = {
                "original_claim": claim,
                "high_trust_claim": high_source_claim,
                "discrepancy": abs(float(claim.split("$")[-1]) - float(high_source_claim.split("$")[-1])) if "$" in claim else "N/A",
                "confidence": 0.6,
                "notes": "Claims conflict - needs resolution",
            }

        else:
            result["result"] = "CONFLICTED"
            result["calibration_data"] = {
                "original_claim": claim,
                "high_trust_claim": high_source_claim,
                "discrepancy": "N/A",
                "confidence": 0.5,
                "notes": "Different interpretation of same facts",
            }

    result["timestamp"] = datetime.now(timezone.utc).isoformat()

    return result


def _verify_expert_judgment(topic, source_name: str, claim: str) -> dict:
    """
    Expert judgment (manual override for ambiguous cases).
    """
    # Store expert decision
    decision = {
        "source": source_name,
        "claim": claim,
        "decision": "ACCEPT" or "REJECT",
        "reasoning": "",
    }

    result = {
        "result": "MANUAL_DECISION",
        "expert_decision": decision,
    }

    return result


def _verify_automated(topic, source_name: str, claim: str) -> dict:
    """
    Automated verification (e.g., Brent price from multiple data feeds).
    """
    # Check if claim matches known data feeds
    result = {
        "result": "PENDING",
        "automated_check": {},
    }

    # TODO: Implement automated verification
    # For now, placeholder
    if "brent" in source_name.lower() or "crude" in claim.lower():
        # Compare with actual feed
        feed_value = topic.get("dataFeeds", {}).get("brent", {}).get("value", 0)
        if str(feed_value) in claim:
            result["result"] = "VERIFIED"
            result["calibration_data"] = {
                "feed_value": feed_value,
                "claimed_value": claim,
                "match": True,
            }

    return result


# ============================================================================
# Trust Score Calibration
# ============================================================================

def calibrate_source_trust(source_name: str, calibration_data: dict) -> float:
    """
    Update trust score based on calibration data.

    Uses Bayesian updating:
    - Prior: current trust score
    - Likelihood: new calibration evidence
    - Posterior: updated trust score
    """
    prior = SOURCE_TRUST.get(source_name, 0.5)  # Default 0.5 if unknown
    calibration = calibration_data.get("calibration_data", {})

    # Extract evidence from calibration
    verification_result = calibration_data.get("result", "PENDING")
    confidence = calibration.get("confidence", 0.5)

    # Evidence weight based on verification result
    if verification_result == "VERIFIED":
        evidence = confidence  # Positive evidence
    elif verification_result == "CONFLICT":
        evidence = -confidence  # Negative evidence
    elif verification_result == "CONFLICTED":
        evidence = -0.2 * confidence  # Slight negative
    elif verification_result == "PENDING":
        evidence = 0  # No new evidence
    else:
        evidence = 0

    # Bayesian update
    # Using log-odds formulation
    prior_odds = prior / (1 - prior)

    # Likelihood ratio (simplified)
    if evidence > 0:
        likelihood_ratio = 1 + 2 * evidence
    elif evidence < 0:
        likelihood_ratio = 1 + 2 * evidence
    else:
        likelihood_ratio = 1

    # Update odds
    posterior_odds = prior_odds * likelihood_ratio

    # Convert back to probability
    posterior = posterior_odds / (1 + posterior_odds)

    return max(0.01, min(0.99, posterior))  # Clip to [0.01, 0.99]


def update_trust_scores(topics: list[str]) -> dict:
    """
    Batch update trust scores based on calibration history.
    """
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_trust_before": dict(SOURCE_TRUST),
        "source_trust_after": {},
        "updates": [],
    }

    for topic_name in topics:
        topic = load_topic(topic_name)

        # Get calibration history
        history = _load_calibration_history()

        # Aggregate calibration data per source
        for source_name, data in history.items():
            prior = SOURCE_TRUST.get(source_name, 0.5)

            # Aggregate evidence
            total_evidence = 0
            for entry in data:
                verification_result = entry.get("result", "PENDING")
                confidence = entry.get("confidence", 0.5)

                if verification_result == "VERIFIED":
                    total_evidence += confidence
                elif verification_result == "CONFLICT":
                    total_evidence -= confidence
                elif verification_result == "CONFLICTED":
                    total_evidence -= 0.2 * confidence

            # Bayesian update
            posterior = calibrate_source_trust(source_name, {
                "calibration_data": {"total_evidence": total_evidence, "confidence": 1.0}
            })

            # Only update if change is significant
            if abs(posterior - prior) > 0.05:
                results["source_trust_after"][source_name] = round(posterior, 3)
                results["updates"].append({
                    "source": source_name,
                    "prior": round(prior, 3),
                    "posterior": round(posterior, 3),
                    "change": round(posterior - prior, 3),
                })

    results["source_trust_after"].update(SOURCE_TRUST)  # Keep unchanged scores

    return results


def _load_calibration_history() -> dict:
    """Load calibration history from storage."""
    history = {}

    for f in CALIBRATION_DIR.glob("calibration_*.json"):
        with open(f) as file:
            data = json.load(file)
            for source_name in data:
                if source_name not in history:
                    history[source_name] = []
                history[source_name].extend(data[source_name])

    return history


def _log_calibration_attempt(source_name: str, claim: str, result: dict) -> None:
    """Log calibration attempt to storage."""
    try:
        # Load existing history
        history_file = CALIBRATION_DIR / f"calibration_{source_name.replace(' ', '_')}.json"

        if not history_file.exists():
            with open(history_file, "w") as f:
                json.dump({source_name: []}, f)

        # Load and append
        with open(history_file) as f:
            history = json.load(f)

        if source_name not in history:
            history[source_name] = []

        history[source_name].append({
            "timestamp": result["timestamp"],
            "claim": claim,
            "result": result["result"],
            "confidence": result.get("calibration_data", {}).get("confidence", 0.5),
        })

        # Save
        with open(history_file, "w") as f:
            json.dump(history, f)

    except Exception as e:
        print(f"[CALIBRATION] Log error: {e}")


# ============================================================================
# Source Registration (new sources)
# ============================================================================

def register_source(source_name: str, initial_trust: float | None = None) -> bool:
    """
    Register a new source with initial trust score.

    Args:
        source_name: Source name
        initial_trust: Initial trust (0-1). If None, use category-based default.

    Returns:
        True if registered, False if already exists
    """
    if source_name in SOURCE_TRUST:
        return False  # Already exists

    # Determine initial trust based on source type
    if initial_trust is None:
        source_upper = source_name.upper()
        if any(word in source_upper for word in ["GOV", "MILITARY", "OFFICIAL"]):
            initial_trust = 0.85
        elif any(word in source_upper for word in ["STATE", "STATE MEDIA"]):
            initial_trust = 0.35
        elif any(word in source_upper for word in ["TARIFF", "MAGAZINE"]):
            initial_trust = 0.60
        else:
            initial_trust = 0.50  # Default

    SOURCE_TRUST[source_name] = initial_trust

    # Save updated trust scores
    trust_file = CALIBRATION_DIR / "source_trust.json"
    with open(trust_file, "w") as f:
        json.dump(SOURCE_TRUST, f, indent=2)

    print(f"[REGISTER] Registered source: {source_name} with initial trust {initial_trust}")
    return True


def add_source_category(category: str, trust_range: tuple[float, float]) -> dict:
    """
    Define a category of sources and trust range for new sources.

    Args:
        category: Category name (e.g., "government", "state_media", "magazine")
        trust_range: (min_trust, max_trust) for new sources in this category

    Returns:
        Category definition
    """
    categories = {
        "government": (0.90, 0.95),
        "military": (0.90, 0.95),
        "reuters_ap_afp": (0.88, 0.90),
        "newspaper": (0.80, 0.85),
        "financial": (0.75, 0.80),
        "general": (0.50, 0.70),
        "state_media": (0.30, 0.45),
        "tabloid": (0.40, 0.60),
    }

    if category in categories:
        trust_range = categories[category]
        print(f"[CATEGORY] Registered category: {category} with trust range {trust_range}")
    else:
        print(f"[CATEGORY] Unknown category: {category}")
        return None

    return {"category": category, "trust_range": trust_range}


# ============================================================================
# Drift Detection
# ============================================================================

def detect_source_drift(source_name: str, window_days: int = 7) -> dict:
    """
    Detect if source accuracy is drifting over time.

    Args:
        source_name: Source to check
        window_days: Number of days to look back

    Returns:
        Drift analysis results
    """
    history_file = CALIBRATION_DIR / f"calibration_{source_name.replace(' ', '_')}.json"

    if not history_file.exists():
        return {"source": source_name, "error": "No calibration history"}

    with open(history_file) as f:
        history = json.load(f)

    if source_name not in history:
        return {"source": source_name, "error": "No history"}

    entries = history[source_name]

    # Sort by timestamp
    entries.sort(key=lambda x: x["timestamp"])

    # Extract verification results and confidence
    results = [(e["result"], e.get("confidence", 0.5)) for e in entries]

    # Split into recent vs older
    total = len(results)
    recent = results[int(total * 0.3):]  # Most recent 30%
    older = results[:int(total * 0.7)]

    # Calculate hit rate for each period
    def calc_hit_rate(period_results):
        verified = sum(1 for r, c in period_results if r == "VERIFIED")
        total_in_period = sum(1 for r, _ in period_results)
        return verified / total_in_period if total_in_period else 0

    recent_hit_rate = calc_hit_rate(recent)
    older_hit_rate = calc_hit_rate(older)

    drift = recent_hit_rate - older_hit_rate

    return {
        "source": source_name,
        "recent_period": f"{len(recent)} entries",
        "older_period": f"{len(older)} entries",
        "recent_hit_rate": round(recent_hit_rate, 3),
        "older_hit_rate": round(older_hit_rate, 3),
        "drift": round(drift, 3),
        "drift_interpretation": "IMPROVING" if drift > 0.1 else "DEGRADING" if drift < -0.1 else "STABLE",
    }


# ============================================================================
# Main CLI
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NRL-Alpha Omega source calibration")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Verify a claim")
    verify_parser.add_argument("--topic", required=True)
    verify_parser.add_argument("--source", required=True)
    verify_parser.add_argument("--claim", required=True)
    verify_parser.add_argument("--method", default="cross_reference")
    verify_parser.add_argument("--high-trust-source", default=None)

    # Calibrate command
    calibrate_parser = subparsers.add_parser("calibrate", help="Batch calibrate all sources")
    calibrate_parser.add_argument("--topics", default="hormuz-closure")
    calibrate_parser.add_argument("--update-file", default=None)

    # Register command
    register_parser = subparsers.add_parser("register", help="Register new source")
    register_parser.add_argument("--source", required=True)
    register_parser.add_argument("--trust", type=float, default=None)

    # Category command
    category_parser = subparsers.add_parser("category", help="Register source category")
    category_parser.add_argument("--category", required=True)
    category_parser.add_argument("--range", required=True)

    # Drift command
    drift_parser = subparsers.add_parser("drift", help="Detect source drift")
    drift_parser.add_argument("--source", required=True)
    drift_parser.add_argument("--window-days", default=7, type=int)

    # Trust command
    trust_parser = subparsers.add_parser("trust", help="Show trust scores")
    trust_parser.add_argument("--topics", default="hormuz-closure")

    args = parser.parse_args()

    if args.command == "verify":
        result = verify_claim(args.topic, args.source, args.claim, args.method,
                             json.loads(args.high_trust_source) if args.high_trust_source else None)
        print(json.dumps(result, indent=2))

    elif args.command == "calibrate":
        topics = [args.topics] if isinstance(args.topics, str) else args.topics
        results = update_trust_scores(topics)
        print(json.dumps(results, indent=2))

    elif args.command == "register":
        register_source(args.source, args.trust)

    elif args.command == "category":
        trust_range = tuple(map(float, args.range.split(",")))
        add_source_category(args.category, trust_range)

    elif args.command == "drift":
        result = detect_source_drift(args.source, args.window_days)
        print(json.dumps(result, indent=2))

    elif args.command == "trust":
        print(json.dumps(SOURCE_TRUST, indent=2))
