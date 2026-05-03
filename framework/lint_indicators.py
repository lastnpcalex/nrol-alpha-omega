"""
lint_indicators — mechanical lint for proposed indicator additions.

Used during the cleanup-indicator-sweep workflow. Before committing
proposed indicators, runs a battery of mechanical checks and surfaces
warnings/blockers. The lint is deterministic — no LLM calls. Output
feeds the operator review and the eventual commit gate.

Public:
  propose_indicators_lint(topic, proposed_indicators, flagged_evidence_ids=None) -> dict

Lint checks:
  - phantom_precision: indicator's max LR / min LR > 20
  - lr_too_certain: any LR >= 0.99 or <= 0.01 (sanity gate would reject)
  - lr_normalized_max_too_high: max LR before scaling > 0.95 warning (engine
    auto-caps but worth flagging)
  - direction_consistency: indicator's LR direction agrees with description
  - cluster_suspicion: multiple proposed indicators that look like they
    cover the same observable (suggest causal_event_id)
  - compound_projection: simulate firing all proposed indicators against
    the flagged evidence (in chronological order) — if the resulting
    posterior trajectory hits clamp, warn
  - direction_drift: proposed indicators systematically favor one
    hypothesis (warning that this set could replicate the saturation
    pattern that pegged 17 topics)
"""

import math
from typing import Optional


# Severity levels
PASS = "pass"
WARNING = "warning"
BLOCKER = "blocker"


def _check_phantom_precision(ind: dict) -> Optional[dict]:
    """Max LR / Min LR > 20 → blocker."""
    lrs = ind.get("likelihoods")
    if not lrs:
        return None
    vals = [v for v in lrs.values() if isinstance(v, (int, float)) and v > 0]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if lo > 0 and hi / lo > 20:
        return {
            "severity": BLOCKER,
            "check": "phantom_precision",
            "indicator_id": ind.get("id"),
            "message": f"max/min LR ratio {hi/lo:.2f} exceeds 20 (phantom_precision)",
        }
    return None


def _check_lr_too_certain(ind: dict) -> list:
    """Any LR >= 0.99 or <= 0.01 → blocker (engine sanity gate would reject)."""
    out = []
    lrs = ind.get("likelihoods")
    if not lrs:
        return out
    for k, v in lrs.items():
        if not isinstance(v, (int, float)):
            continue
        if v >= 0.99:
            out.append({
                "severity": BLOCKER,
                "check": "lr_too_certain",
                "indicator_id": ind.get("id"),
                "message": f"P(E|{k}) = {v} — engine sanity gate rejects >= 0.99. Use <= 0.95.",
            })
        elif v <= 0.01:
            out.append({
                "severity": BLOCKER,
                "check": "lr_too_certain",
                "indicator_id": ind.get("id"),
                "message": f"P(E|{k}) = {v} — engine sanity gate rejects <= 0.01. Use >= 0.05.",
            })
    return out


def _check_lr_normalized_max_too_high(ind: dict) -> Optional[dict]:
    """Max LR > 0.95 → warning (engine auto-caps but worth flagging)."""
    lrs = ind.get("likelihoods")
    if not lrs:
        return None
    vals = [v for v in lrs.values() if isinstance(v, (int, float))]
    if not vals:
        return None
    hi = max(vals)
    if 0.95 < hi < 0.99:
        return {
            "severity": WARNING,
            "check": "lr_normalized_max_too_high",
            "indicator_id": ind.get("id"),
            "message": f"max LR = {hi:.3f} — engine will auto-cap to 0.95. Consider authoring with explicit cap.",
        }
    return None


def _bayes_step(prior: dict, lrs: dict) -> dict:
    """One Bayesian update step. No clamp. Pure math."""
    keys = list(prior.keys())
    unnorm = {k: prior[k] * lrs.get(k, 1.0) for k in keys}
    total = sum(unnorm.values())
    if total <= 0:
        return dict(prior)
    return {k: v / total for k, v in unnorm.items()}


def _simulate_compound(prior: dict, indicators: list) -> dict:
    """Simulate firing all proposed indicators (in order) against the prior.
    Returns final posterior + max H reached at any point."""
    current = dict(prior)
    max_h_seen = max(current, key=lambda k: current[k])
    max_p_seen = current[max_h_seen]
    for ind in indicators:
        lrs = ind.get("likelihoods")
        if not lrs:
            continue
        current = _bayes_step(current, lrs)
        cur_max_h = max(current, key=lambda k: current[k])
        if current[cur_max_h] > max_p_seen:
            max_p_seen = current[cur_max_h]
            max_h_seen = cur_max_h
    return {"final": current, "max_h": max_h_seen, "max_p": max_p_seen}


def _check_compound_projection(topic: dict, proposed: list) -> list:
    """Simulate compound effect on the topic's current prior.
    Warn if proposed set would peg posterior, blocker if extreme."""
    out = []
    hyps = topic.get("model", {}).get("hypotheses", {})
    if not hyps:
        return out
    prior = {k: h.get("posterior", 1.0/len(hyps)) for k, h in hyps.items()}
    sim = _simulate_compound(prior, proposed)
    final = sim["final"]
    max_h = max(final, key=lambda k: final[k])
    max_p = final[max_h]
    if max_p > 0.95:
        out.append({
            "severity": BLOCKER,
            "check": "compound_projection",
            "message": (
                f"Firing all {len(proposed)} proposed indicators on current prior "
                f"would peg {max_h} at {max_p:.3f}. This replicates the saturation pattern "
                f"that pegged 17 topics. Reduce LR magnitudes or add anti-indicators."
            ),
            "details": {"final": final, "prior": prior},
        })
    elif max_p > 0.85:
        out.append({
            "severity": WARNING,
            "check": "compound_projection",
            "message": (
                f"Firing all {len(proposed)} proposed indicators would push {max_h} to "
                f"{max_p:.3f} — high but below clamp. Confirm this is the intended trajectory."
            ),
            "details": {"final": final, "prior": prior},
        })
    return out


def _check_direction_drift(proposed: list) -> Optional[dict]:
    """If all proposed indicators systematically favor one hypothesis, warn."""
    if len(proposed) < 3:
        return None
    max_dirs = []
    for ind in proposed:
        lrs = ind.get("likelihoods")
        if not lrs:
            continue
        max_h = max(lrs, key=lambda k: lrs[k])
        max_dirs.append(max_h)
    if not max_dirs:
        return None
    # If all proposed point at the same hypothesis as their max, flag
    unique = set(max_dirs)
    if len(unique) == 1 and len(max_dirs) >= 3:
        return {
            "severity": WARNING,
            "check": "direction_drift",
            "message": (
                f"All {len(max_dirs)} proposed indicators favor {max_dirs[0]} as "
                f"max LR. Compounding will systematically push posterior toward {max_dirs[0]}. "
                f"This is the pattern that pegged 17 topics. Confirm intended, "
                f"or add anti-indicators favoring other hypotheses."
            ),
        }
    return None


def _check_cluster_suspicion(proposed: list) -> Optional[dict]:
    """If multiple indicators look thematically similar (text overlap), suggest causal_event_id."""
    if len(proposed) < 2:
        return None
    try:
        from framework.topic_search import _embed_score
    except ImportError:
        return None

    # Build text for each proposed
    texts = []
    for ind in proposed:
        text = " ".join([
            ind.get("id", "") or "",
            ind.get("desc", "") or "",
        ])
        texts.append(text)

    # Pairwise similarity
    suspect_pairs = []
    for i in range(len(texts)):
        scores = _embed_score(texts[i], [t for j, t in enumerate(texts) if j != i])
        if scores is None:
            return None
        for j_offset, score in enumerate(scores):
            j = j_offset if j_offset < i else j_offset + 1
            if i < j and score > 0.6:
                # Check if either has a causal_event_id; if so, fine if same
                ce_i = proposed[i].get("causal_event_id")
                ce_j = proposed[j].get("causal_event_id")
                if ce_i and ce_j and ce_i == ce_j:
                    continue
                suspect_pairs.append({
                    "ind_a": proposed[i].get("id"),
                    "ind_b": proposed[j].get("id"),
                    "similarity": float(score),
                    "ind_a_event_id": ce_i,
                    "ind_b_event_id": ce_j,
                })

    if not suspect_pairs:
        return None
    return {
        "severity": WARNING,
        "check": "cluster_suspicion",
        "message": (
            f"{len(suspect_pairs)} pairs of proposed indicators look thematically similar "
            f"(could fire on same underlying event). Consider assigning shared causal_event_id "
            f"to enable de-correlation when they fire together."
        ),
        "details": {"pairs": suspect_pairs},
    }



def _check_shape_fields(ind: dict) -> list:
    """Enforce the shape field and its required co-fields."""
    out = []
    ind_id = ind.get("id", "unknown")
    shape = ind.get("shape")
    
    if not shape:
        out.append({
            "severity": BLOCKER,
            "check": "shape_field_required",
            "indicator_id": ind_id,
            "message": "Indicator missing 'shape' field. Must be one of 'single_observation', 'per_event_member', 'ladder_rung'."
        })
        return out
        
    valid_shapes = {"single_observation", "per_event_member", "ladder_rung"}
    if shape not in valid_shapes:
        out.append({
            "severity": BLOCKER,
            "check": "shape_field_invalid",
            "indicator_id": ind_id,
            "message": f"Invalid shape '{shape}'. Must be one of {valid_shapes}."
        })
        
    if shape == "per_event_member":
        if not ind.get("causal_event_id"):
            out.append({
                "severity": BLOCKER,
                "check": "per_event_needs_causal_id",
                "indicator_id": ind_id,
                "message": "'per_event_member' shape requires a shared 'causal_event_id'."
            })
            
    elif shape == "ladder_rung":
        if not ind.get("ladder_group") or not ind.get("ladder_step"):
            out.append({
                "severity": BLOCKER,
                "check": "ladder_needs_group_and_step",
                "indicator_id": ind_id,
                "message": "'ladder_rung' shape requires 'ladder_group' and 'ladder_step' fields."
            })
            
    return out

def _check_ladder_coherence(topic: dict, proposed: list) -> list:
    """Check ladder group coherence (direction, clamp, continuity)."""
    out = []
    
    # Collect all indicators (existing + proposed)
    all_inds = []
    if topic:
        for t in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
            all_inds.extend(topic.get("indicators", {}).get("tiers", {}).get(t, []))
        all_inds.extend(topic.get("indicators", {}).get("anti_indicators", []))
    
    # Merge existing and proposed, preferring proposed if ID matches
    merged_inds = {ind.get("id"): ind for ind in all_inds if isinstance(ind, dict) and ind.get("id")}
    for ind in proposed:
        if isinstance(ind, dict) and ind.get("id"):
            merged_inds[ind.get("id")] = ind
            
    # Group by ladder_group
    groups = {}
    for ind in merged_inds.values():
        if ind.get("shape") == "ladder_rung" and ind.get("ladder_group"):
            groups.setdefault(ind["ladder_group"], []).append(ind)
            
    for group_name, rungs in groups.items():
        # Sort by step
        try:
            rungs.sort(key=lambda x: int(x.get("ladder_step", 0)))
        except ValueError:
            pass # Malformed step, ignore for sorting
            
        steps = [int(x.get("ladder_step", 0)) for x in rungs if str(x.get("ladder_step", "")).isdigit()]
        
        # Step continuity warning
        if steps and len(steps) > 1:
            for i in range(len(steps) - 1):
                if steps[i+1] - steps[i] != 1:
                    out.append({
                        "severity": WARNING,
                        "check": "ladder_step_continuity",
                        "message": f"Ladder group '{group_name}' has a gap in steps: {steps[i]} -> {steps[i+1]}."
                    })
                    break
                    
        # Collect directions and compute cumulative product
        dirs = set()
        cum_prod = {} # H_key -> float
        
        for rung in rungs:
            lrs = rung.get("likelihoods", {})
            if not lrs:
                continue
                
            # Direction coherence
            max_h = max(lrs, key=lambda k: lrs[k])
            dirs.add(max_h)
            
            # Incremental magnitude warning
            hi = max(v for v in lrs.values() if isinstance(v, (int, float)))
            lo = min(v for v in lrs.values() if isinstance(v, (int, float)))
            if hi >= 0.9 or lo <= 0.1:
                out.append({
                    "severity": WARNING,
                    "check": "ladder_incremental_magnitude",
                    "indicator_id": rung.get("id"),
                    "message": f"Ladder rung has extreme LR ({hi:.2f} or {lo:.2f}). Ladder LRs should be incremental updates, not absolute."
                })
                
            # Cumulative
            for k, v in lrs.items():
                if isinstance(v, (int, float)):
                    cum_prod[k] = cum_prod.get(k, 1.0) * v
                    
        if len(dirs) > 1:
            out.append({
                "severity": BLOCKER,
                "check": "ladder_direction_coherence",
                "message": f"Ladder group '{group_name}' is direction-incoherent. Rungs point to different max hypotheses: {dirs}."
            })
            
        # Cumulative clamp check
        for k, v in cum_prod.items():
            if v >= 0.99 or v <= 0.01:
                out.append({
                    "severity": BLOCKER,
                    "check": "ladder_cumulative_clamp",
                    "message": f"Ladder group '{group_name}' exceeds cumulative clamp on {k} (prod = {v:.4f}). Ensure rungs are incremental."
                })
                
    return out

def propose_indicators_lint(
    topic: dict,
    proposed_indicators: list,
    flagged_evidence_ids: Optional[list] = None,
) -> dict:
    """
    Run mechanical lint on a set of proposed indicator additions.

    Args:
        topic: topic dict (read-only — won't be modified)
        proposed_indicators: list of indicator dicts as they'd be added
                             (id, desc, posteriorEffect, likelihoods, ...)
        flagged_evidence_ids: not used yet — placeholder for future
                              "would these indicators have fired on this evidence" check

    Returns:
        {
            passed: bool,           # True if no blockers
            blockers: [...],        # must-fix issues
            warnings: [...],        # operator should review
            checks_run: [str],      # which checks executed
            summary: str,           # human-readable one-liner
        }
    """
    blockers = []
    warnings = []
    checks_run = [
        "phantom_precision", "lr_too_certain", "lr_normalized_max_too_high",
        "compound_projection", "direction_drift", "cluster_suspicion",
        "shape_field_required", "ladder_coherence"
    ]

    # Per-indicator checks
    for ind in proposed_indicators:
        if not isinstance(ind, dict):
            continue
            
        # Shape fields
        for issue in _check_shape_fields(ind):
            (blockers if issue["severity"] == BLOCKER else warnings).append(issue)
            
        if (issue := _check_phantom_precision(ind)):
            (blockers if issue["severity"] == BLOCKER else warnings).append(issue)
        for issue in _check_lr_too_certain(ind):
            (blockers if issue["severity"] == BLOCKER else warnings).append(issue)
        if (issue := _check_lr_normalized_max_too_high(ind)):
            warnings.append(issue)

    # Set-level checks
    for issue in _check_ladder_coherence(topic, proposed_indicators):
        (blockers if issue["severity"] == BLOCKER else warnings).append(issue)
        
    for issue in _check_compound_projection(topic, proposed_indicators):
        (blockers if issue["severity"] == BLOCKER else warnings).append(issue)
    if (issue := _check_direction_drift(proposed_indicators)):
        warnings.append(issue)
    if (issue := _check_cluster_suspicion(proposed_indicators)):
        warnings.append(issue)

    passed = len(blockers) == 0
    summary = (
        f"PASS — {len(warnings)} warnings, no blockers"
        if passed else
        f"BLOCKED — {len(blockers)} blocker(s), {len(warnings)} warning(s)"
    )

    return {
        "passed": passed,
        "blockers": blockers,
        "warnings": warnings,
        "checks_run": checks_run,
        "summary": summary,
        "n_proposed": len(proposed_indicators),
    }
