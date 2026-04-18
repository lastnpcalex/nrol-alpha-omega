"""
NROL-AO Extrapolation Pipeline

The mechanical sweep: runs per-topic prediction sweeps, Fréchet-bound
refreshes, trust-weighted Z recomputation. Does NOT make LLM calls.
The agent layer (Haiku/Sonnet/Opus) lives in a separate module and
calls into these helpers.

The update button in model.html invokes run_mechanical_sweep() directly
(fast, local) OR run_agent_sweep() (slow, token-heavy, via Loom.send).
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone

_REPO = str(Path(__file__).parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from engine import load_topic, save_topic, extract_posteriors
from framework.scoring import (
    sweep_conditional_predictions, conditional_calibration_report,
)
from framework.dependencies import compute_implied_posteriors
from framework import extrapolation_db as edb


TOPICS_DIR = Path(__file__).parent.parent / "topics"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_frechet_bounds(p_a: float, p_b: float) -> dict:
    """
    Fréchet-Hoeffding bounds for the joint probability of two events.

    P_max = min(P(A), P(B))               (perfect positive correlation)
    P_min = max(0, P(A) + P(B) - 1)       (perfect negative correlation)
    P_indep = P(A) * P(B)                 (independence)

    Returns {p_min, p_indep, p_max, width}.
    width is (p_max - p_min), a measure of how much correlation assumption matters.
    """
    p_min = max(0.0, p_a + p_b - 1.0)
    p_indep = p_a * p_b
    p_max = min(p_a, p_b)
    return {
        "p_min": round(p_min, 4),
        "p_indep": round(p_indep, 4),
        "p_max": round(p_max, 4),
        "width": round(p_max - p_min, 4),
    }


def compute_junction_entropy(cpt_row: dict, downstream_keys: list) -> dict:
    """
    For a junction defined by upstream hypothesis → downstream posterior distribution,
    compute the entropy of the downstream distribution. Low entropy = this junction
    strongly constrains the downstream (focused beam). High entropy = diffuse fan.

    Returns {entropy, max_entropy, uncertainty_ratio, peak_key, peak_prob}.
    """
    import math
    probs = [cpt_row.get(k, 0) for k in downstream_keys if isinstance(cpt_row.get(k), (int, float))]
    probs = [p for p in probs if p > 0]
    if not probs:
        return {"entropy": 0, "max_entropy": 0, "uncertainty_ratio": 0,
                "peak_key": None, "peak_prob": 0}

    entropy = -sum(p * math.log2(p) for p in probs)
    max_entropy = math.log2(len(downstream_keys)) if len(downstream_keys) > 1 else 1
    ur = entropy / max_entropy if max_entropy > 0 else 0

    peak_key = None
    peak_prob = 0
    for k in downstream_keys:
        v = cpt_row.get(k)
        if isinstance(v, (int, float)) and v > peak_prob:
            peak_prob = v
            peak_key = k

    return {
        "entropy": round(entropy, 4),
        "max_entropy": round(max_entropy, 4),
        "uncertainty_ratio": round(ur, 3),
        "peak_key": peak_key,
        "peak_prob": round(peak_prob, 4),
    }


def compute_trust_band(topic: dict) -> str:
    """
    Determine which Z-band (FOREGROUND, MID, BACKGROUND) a topic belongs to
    based on effective-weight-averaged source trust across its evidence.

    Uses the effectiveWeight field already computed by add_evidence().
    """
    evidence = topic.get("evidenceLog", [])
    if not evidence:
        return "MID"  # default if no evidence yet

    # Effective-weight average of source trust
    total_weight = 0
    weighted_trust = 0
    for e in evidence[-50:]:  # recent evidence dominates
        w = e.get("effectiveWeight", 0.5)
        # effectiveWeight already incorporates source trust and claim state
        weighted_trust += w * w  # weight by weight gives quadratic weighting toward strong evidence
        total_weight += w

    if total_weight == 0:
        return "MID"

    avg_trust = weighted_trust / total_weight

    if avg_trust >= 0.80:
        return "FOREGROUND"
    elif avg_trust >= 0.50:
        return "MID"
    else:
        return "BACKGROUND"


def compute_path_trust(upstream_topic: dict, downstream_topic: dict) -> str:
    """
    A path through a junction is as strong as its weakest evidence.
    Returns the minimum trust band between upstream and downstream.
    """
    band_order = {"FOREGROUND": 3, "MID": 2, "BACKGROUND": 1}
    reverse = {3: "FOREGROUND", 2: "MID", 1: "BACKGROUND"}
    u_band = compute_trust_band(upstream_topic)
    d_band = compute_trust_band(downstream_topic)
    return reverse[min(band_order[u_band], band_order[d_band])]


def build_world_model() -> dict:
    """
    Assemble the full operator model for the model.html visualization.

    Returns:
      {
        topics: [{slug, posteriors, hypothesis_keys, trust_band, ...}, ...],
        junctions: [{
          upstream_slug, upstream_hypothesis,
          downstream_slug, downstream_hypothesis,
          joint_bounds: {p_min, p_indep, p_max, width},
          cpt_row: {...},
          junction_entropy: {...},
          path_trust_band: "FOREGROUND" | "MID" | "BACKGROUND"
        }, ...],
        predictions_by_topic: {slug: [conditionalPrediction, ...], ...},
        calibration_by_topic: {slug: conditional_calibration_report},
        generated_at: iso
      }
    """
    result = {
        "topics": [],
        "junctions": [],
        "predictions_by_topic": {},
        "calibration_by_topic": {},
        "generated_at": _now_iso(),
    }

    if not TOPICS_DIR.exists():
        return result

    # Load all topics
    all_topics = {}
    for path in TOPICS_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
            slug = t.get("meta", {}).get("slug", path.stem)
            all_topics[slug] = t
        except (json.JSONDecodeError, OSError):
            continue

    # Build topic nodes
    for slug, t in all_topics.items():
        meta = t.get("meta", {})
        hyps = t.get("model", {}).get("hypotheses", {})
        h_keys = list(hyps.keys())
        posteriors = {k: hyps[k]["posterior"] for k in h_keys}

        # Creation date from first posteriorHistory entry
        history = t.get("model", {}).get("posteriorHistory", [])
        created_date = history[0].get("date", meta.get("created", ""))[:10] if history else ""

        # Resolution/expiry date from meta
        resolution_date = meta.get("resolutionDeadline", "") or meta.get("horizon", "")

        result["topics"].append({
            "slug": slug,
            "title": meta.get("title", slug),
            "status": meta.get("status", "ACTIVE"),
            "classification": meta.get("classification", "ROUTINE"),
            "posteriors": posteriors,
            "hypothesis_keys": h_keys,
            "hypothesis_labels": {k: hyps[k].get("label", k) for k in h_keys},
            "trust_band": compute_trust_band(t),
            "created_date": created_date,
            "resolution_date": resolution_date,
            "day_count": meta.get("dayCount", 0),
            "last_updated": meta.get("lastUpdated", ""),
        })

        # Predictions for this topic
        result["predictions_by_topic"][slug] = t.get("conditionalPredictions", [])

        # Calibration report
        try:
            result["calibration_by_topic"][slug] = conditional_calibration_report(t)
        except Exception:
            result["calibration_by_topic"][slug] = {"status": "error"}

    # Build junctions from CPT edges
    for down_slug, t in all_topics.items():
        for dep in t.get("dependencies", {}).get("upstream", []):
            up_slug = dep.get("slug")
            conditionals = dep.get("conditionals")
            if not conditionals or up_slug not in all_topics:
                continue

            upstream = all_topics[up_slug]
            up_hyps = upstream.get("model", {}).get("hypotheses", {})
            down_hyps = t.get("model", {}).get("hypotheses", {})
            down_keys = list(down_hyps.keys())

            path_band = compute_path_trust(upstream, t)

            for up_h, cpt_row in conditionals.items():
                if not isinstance(cpt_row, dict):
                    continue
                up_prob = up_hyps.get(up_h, {}).get("posterior", 0)

                # Compute junction entropy for this CPT row
                je = compute_junction_entropy(cpt_row, down_keys)

                # For each downstream hypothesis, create an edge with Fréchet bounds
                for down_h in down_keys:
                    cond_prob = cpt_row.get(down_h)
                    if not isinstance(cond_prob, (int, float)) or cond_prob < 0.01:
                        continue
                    down_prob = down_hyps.get(down_h, {}).get("posterior", 0)

                    # Joint probability bounds
                    # The "joint" here is P(upstream_H AND this_downstream_H).
                    # We compute bounds using current marginals.
                    bounds = compute_frechet_bounds(up_prob, down_prob)

                    result["junctions"].append({
                        "upstream_slug": up_slug,
                        "upstream_hypothesis": up_h,
                        "downstream_slug": down_slug,
                        "downstream_hypothesis": down_h,
                        "upstream_prob": round(up_prob, 4),
                        "downstream_prob": round(down_prob, 4),
                        "conditional_prob": round(cond_prob, 4),
                        "joint_bounds": bounds,
                        "junction_entropy": je,
                        "path_trust_band": path_band,
                        "narrative": cpt_row.get("narrative", ""),
                    })

    return result


def run_mechanical_sweep(topic_slugs: list = None) -> dict:
    """
    Run the no-LLM mechanical sweep:
    - Sweep conditional predictions (auto-score/void/suspend)
    - Refresh Fréchet bounds and junction entropy
    - Recompute trust bands
    - Update calibration reports

    This is fast and cheap. Button-triggered, no token cost.

    Returns summary of what changed.
    """
    results = {
        "started_at": _now_iso(),
        "topics_processed": 0,
        "predictions_swept": {"scored": 0, "voided": 0, "suspended": 0, "still_active": 0},
        "topics_updated": [],
        "errors": [],
    }

    if not TOPICS_DIR.exists():
        return results

    target_slugs = topic_slugs
    if not target_slugs:
        target_slugs = [p.stem for p in TOPICS_DIR.glob("*.json") if not p.stem.startswith("_")]

    for slug in target_slugs:
        try:
            t = load_topic(slug)
            # Only sweep if topic has predictions
            if t.get("conditionalPredictions"):
                summary = sweep_conditional_predictions(t)
                for k in results["predictions_swept"]:
                    results["predictions_swept"][k] += summary.get(k, 0)
                if any(v > 0 for k, v in summary.items() if k != "still_active"):
                    save_topic(t)
                    results["topics_updated"].append(slug)
            results["topics_processed"] += 1
        except Exception as e:
            results["errors"].append({"slug": slug, "error": str(e)})

    results["finished_at"] = _now_iso()
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NROL-AO Extrapolation Pipeline")
    sub = parser.add_subparsers(dest="cmd")

    sweep = sub.add_parser("sweep", help="Run mechanical sweep (no LLM)")
    sweep.add_argument("--slugs", nargs="*", help="Topic slugs (default: all)")

    world = sub.add_parser("world", help="Build and print world model")

    frechet = sub.add_parser("frechet", help="Compute Fréchet bounds")
    frechet.add_argument("p_a", type=float)
    frechet.add_argument("p_b", type=float)

    args = parser.parse_args()

    if args.cmd == "sweep":
        r = run_mechanical_sweep(args.slugs)
        print(json.dumps(r, indent=2, default=str))
    elif args.cmd == "world":
        w = build_world_model()
        # Summary only
        print(f"Topics: {len(w['topics'])}")
        print(f"Junctions: {len(w['junctions'])}")
        total_preds = sum(len(p) for p in w["predictions_by_topic"].values())
        print(f"Predictions: {total_preds}")
    elif args.cmd == "frechet":
        print(json.dumps(compute_frechet_bounds(args.p_a, args.p_b), indent=2))
    else:
        parser.print_help()
