"""
NRL-Alpha Omega — Generalized Epistemic Bayesian Estimator Engine

Core engine for loading, updating, and managing topic state files.
Governor integration: epistemic governance is a hard gate on mutations,
not an optional lint pass. The governor vets both data and thinking.

No external dependencies — Python stdlib + governor.py only.
"""

import json
import os
import copy
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from governor import (
    check_update_proposal,
    classify_evidence,
    assess_claim_state,
    get_effective_weight,
    validate_hypotheses,
    compute_topic_rt,
    compute_entropy,
    compute_max_entropy,
    compute_uncertainty_ratio,
    compute_kl_from_prior,
    audit_evidence_freshness,
)

TOPICS_DIR = Path(__file__).parent / "topics"
BRIEFS_DIR = Path(__file__).parent / "briefs"
DASHBOARDS_DIR = Path(__file__).parent / "dashboards"


# ---------------------------------------------------------------------------
# Governance Exception
# ---------------------------------------------------------------------------

class GovernanceError(Exception):
    """Raised when a governor check blocks an operation."""
    def __init__(self, message: str, failures: list = None, warnings: list = None):
        super().__init__(message)
        self.failures = failures or []
        self.warnings = warnings or []


# ---------------------------------------------------------------------------
# Topic I/O
# ---------------------------------------------------------------------------

def load_topic(slug: str) -> dict:
    """Load a topic state file. Raises if malformed."""
    path = TOPICS_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"Topic not found: {slug}")
    with open(path, "r", encoding="utf-8") as f:
        topic = json.load(f)
    validate_topic(topic)
    return topic


def save_topic(topic: dict) -> None:
    """Write topic state back to disk with embedded governance snapshot."""
    slug = topic["meta"]["slug"]
    topic["meta"]["lastUpdated"] = _now_iso()

    # Guard: enrich any evidence entries that bypassed the governor
    _GOVERNOR_FIELDS = ("ledger", "claimState", "effectiveWeight")
    evidence_log = topic.get("evidenceLog", [])
    enriched_count = 0
    for entry in evidence_log:
        if any(field not in entry for field in _GOVERNOR_FIELDS):
            entry["ledger"] = entry.get("ledger") or classify_evidence(entry)
            entry["claimState"] = entry.get("claimState") or assess_claim_state(
                entry, evidence_log
            )
            entry["effectiveWeight"] = entry.get("effectiveWeight") or get_effective_weight(
                entry, evidence_log, topic=topic
            )
            enriched_count += 1
    if enriched_count:
        warnings.warn(
            f"Governor guard: {enriched_count} evidence entries were missing "
            f"governor fields — enriched retroactively on save. "
            f"Use engine.add_evidence() to avoid this.",
            stacklevel=2,
        )

    # Compute and embed governance snapshot
    try:
        rt = compute_topic_rt(topic)
        entropy = compute_entropy(topic)
        max_entropy = compute_max_entropy(topic)
        uncertainty = compute_uncertainty_ratio(topic)
        freshness = audit_evidence_freshness(topic)
        admissibility = validate_hypotheses(topic)

        # Count issues (mirrors governance_report logic)
        issues = []
        if rt["regime"] in ("DANGEROUS", "RUNAWAY"):
            issues.append(f"R_t in {rt['regime']} — needs fresh evidence")
        if freshness["stale"] > freshness["fresh"]:
            issues.append(f"Majority stale evidence ({freshness['stale']}/{freshness['total']})")
        inadmissible = [k for k, v in admissibility.items() if v["grade"] == "INADMISSIBLE"]
        if inadmissible:
            issues.append(f"Inadmissible hypotheses: {', '.join(inadmissible)}")
        unfalsifiable = [k for k, v in admissibility.items() if v["falsifiability"] == "NO"]
        if unfalsifiable:
            issues.append(f"Unfalsifiable hypotheses: {', '.join(unfalsifiable)}")
        if uncertainty > 0.9:
            issues.append("Near-maximum uncertainty — model is not discriminating")
        elif uncertainty < 0.1:
            issues.append("Near-zero uncertainty — check for overconfidence")

        kl_result = compute_kl_from_prior(topic)
        if kl_result["interpretation"] == "PRIOR_DOMINATED":
            issues.append("Posterior may be prior-dominated (low KL from initial prior)")

        health = "HEALTHY" if len(issues) == 0 else "DEGRADED" if len(issues) <= 2 else "CRITICAL"

        topic["governance"] = {
            "health": health,
            "issues": issues,
            "rt": {
                "rt": rt["rt"],
                "regime": rt["regime"],
                "worst_hypothesis": rt.get("worst_hypothesis"),
            },
            "entropy": entropy,
            "maxEntropy": max_entropy,
            "uncertaintyRatio": uncertainty,
            "evidenceFreshness": {
                "fresh": freshness["fresh"],
                "stale": freshness["stale"],
                "total": freshness["total"],
            },
            "hypothesisAdmissibility": {
                k: v["grade"] for k, v in admissibility.items()
            },
            "klFromPrior": {
                "kl_divergence": kl_result["kl_divergence"],
                "interpretation": kl_result["interpretation"],
            },
            "lastComputed": _now_iso(),
        }

        # --- Epistemic improvement: calibration health ---
        try:
            from framework.scoring import get_calibration_health
            cal_health = get_calibration_health(topic)
            topic["governance"]["calibrationHealth"] = cal_health
            if cal_health == "POORLY_CALIBRATED":
                issues.append("Prediction calibration is poor (Brier > 0.4)")
                topic["governance"]["health"] = (
                    "CRITICAL" if len(issues) > 2 else "DEGRADED"
                )
                topic["governance"]["issues"] = issues
        except ImportError:
            pass

        # --- Epistemic improvement: contradiction count ---
        try:
            from framework.contradictions import get_unresolved_contradictions
            unresolved = get_unresolved_contradictions(topic)
            topic["governance"]["unresolvedContradictions"] = len(unresolved)
            if len(unresolved) > 3:
                issues.append(f"{len(unresolved)} unresolved contradictions")
                topic["governance"]["issues"] = issues
        except ImportError:
            pass

        # --- Epistemic improvement: expired hypothesis detection ---
        try:
            from framework.scoring import check_expired_hypotheses, record_partial_outcome
            expired = check_expired_hypotheses(topic)
            if expired:
                ps = topic.get("predictionScoring", {})
                for exp in expired:
                    already = any(
                        o.get("expired") == exp["hypothesis"]
                        for o in ps.get("outcomes", [])
                        if o.get("type") == "PARTIAL_EXPIRY"
                    )
                    if not already:
                        record_partial_outcome(
                            topic, exp["hypothesis"],
                            note=f"Auto-expired at day {exp['current_day']}"
                        )
                topic["governance"]["expiredHypotheses"] = [
                    {"key": e["hypothesis"], "label": e["label"],
                     "expiredAtDay": e["expired_at_day"]}
                    for e in expired
                ]
        except ImportError:
            pass

    except Exception:
        # Governor computation must never prevent saving state
        pass

    # --- Design gate check (runs on every save, embeds results) ---
    try:
        from framework.topic_design_gate import run_mechanical_checks
        gate = run_mechanical_checks(topic)
        topic.setdefault("governance", {})["designGate"] = {
            "passed": gate["passed"],
            "blockers": gate["blockers"],
            "warnings": gate["warnings"],
            "coverage": gate.get("coverage", {}).get("matrix", {}),
            "indistinguishable": [
                {"h1": p["h1"], "h2": p["h2"], "overlap": p["overlap"]}
                for p in gate.get("distinguishability", {}).get("indistinguishable", [])
            ],
            "lastChecked": _now_iso(),
        }
        if not gate["passed"]:
            topic["governance"].setdefault("issues", []).append(
                f"DESIGN GATE BLOCKED: {len(gate['blockers'])} blocker(s)"
            )
            # Warn but don't prevent save — the operator needs to fix and re-save
            warnings.warn(
                f"Topic '{slug}' has {len(gate['blockers'])} design gate blocker(s): "
                f"{'; '.join(gate['blockers'][:3])}",
                stacklevel=2,
            )
    except ImportError:
        pass  # Framework module not available
    except Exception:
        pass  # Gate check must never prevent saving state

    # --- Epistemic improvement: auto-compaction ---
    try:
        from framework.compaction import auto_compact
        compact_result = auto_compact(topic, threshold=150)
        if compact_result.get("compacted"):
            topic.setdefault("governance", {})["lastCompaction"] = _now_iso()
    except (ImportError, Exception):
        pass  # Never block save

    path = TOPICS_DIR / f"{slug}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topic, f, indent=2, ensure_ascii=False)


def list_topics() -> list[dict]:
    """Return list of {slug, title, status, classification} for all topics."""
    results = []
    if not TOPICS_DIR.exists():
        return results
    for p in sorted(TOPICS_DIR.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                t = json.load(f)
            meta = t.get("meta", {})
            gov = t.get("governance", {})
            results.append({
                "slug": meta.get("slug", p.stem),
                "title": meta.get("title", p.stem),
                "status": meta.get("status", "UNKNOWN"),
                "classification": meta.get("classification", "ROUTINE"),
                "question": meta.get("question", ""),
                "lastUpdated": meta.get("lastUpdated", ""),
                "governanceHealth": gov.get("health") if gov else None,
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def create_topic(config: dict) -> dict:
    """
    Create a new topic from a config dict. Returns the initialized state.

    Governor gate: INADMISSIBLE hypotheses block creation.
    MARGINAL hypotheses generate a warning in the evidence log.
    """
    template_path = TOPICS_DIR / "_template.json"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            topic = json.load(f)
    else:
        topic = _empty_topic()

    # Remove protocol notes from template
    topic.pop("_protocol", None)
    if "indicators" in topic:
        topic["indicators"].pop("_design_rules", None)

    # Overlay config onto template
    _deep_merge(topic, config)

    # Set defaults
    now = _now_iso()
    topic.setdefault("meta", {})
    topic["meta"].setdefault("created", now)
    topic["meta"].setdefault("lastUpdated", now)
    topic["meta"].setdefault("status", "ACTIVE")
    topic["meta"].setdefault("dayCount", 0)
    topic["meta"].setdefault("classification", "ROUTINE")

    # Structural validation
    validate_topic(topic)

    # Governor hard gate: admissibility
    admissibility = validate_hypotheses(topic)
    inadmissible = {k: v for k, v in admissibility.items()
                    if v["grade"] == "INADMISSIBLE"}
    marginal = {k: v for k, v in admissibility.items()
                if v["grade"] == "MARGINAL"}

    if inadmissible:
        details = "; ".join(
            f"{k}: {v['passed']}/{v['total']} checks "
            f"(clarity={v['setpoint_clarity']}, "
            f"observable={v['observability']}, "
            f"falsifiable={v['falsifiability']})"
            for k, v in inadmissible.items()
        )
        raise GovernanceError(
            f"Cannot create topic: INADMISSIBLE hypotheses — {details}",
            failures=list(inadmissible.keys()),
        )

    if marginal:
        for k, v in marginal.items():
            failed_checks = [c for c, passed in v["checks"].items() if not passed]
            topic.setdefault("evidenceLog", []).append({
                "time": _now_iso(),
                "tag": "INTEL",
                "text": (f"GOVERNANCE: Hypothesis {k} is MARGINAL at creation "
                         f"(failed: {', '.join(failed_checks)})"),
                "provenance": "DERIVED",
                "posteriorImpact": "NONE",
                "ledger": "DECISION",
                "claimState": "PROPOSED",
                "effectiveWeight": 0.5,
            })

    save_topic(topic)
    return topic


def scaffold_topic(slug: str) -> str:
    """
    Create a new topic scaffold from template. Does NOT validate — the user
    needs to fill in the blanks first. Returns the file path.
    """
    template_path = TOPICS_DIR / "_template.json"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            topic = json.load(f)
    else:
        topic = _empty_topic()

    # Set the slug and timestamps
    now = _now_iso()
    topic["meta"]["slug"] = slug
    topic["meta"]["created"] = now
    topic["meta"]["lastUpdated"] = now
    topic["meta"]["startDate"] = now

    # Write initial prior to history
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    if hypotheses:
        history_entry = {"date": now[:10], "note": "Prior (uniform — fill in domain knowledge)"}
        for k, h in hypotheses.items():
            history_entry[k] = h["posterior"]
        topic["model"]["posteriorHistory"] = [history_entry]

    # Save without validation (user needs to fill in)
    path = TOPICS_DIR / f"{slug}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topic, f, indent=2, ensure_ascii=False)

    return str(path)


# ---------------------------------------------------------------------------
# Bayesian Updates
# ---------------------------------------------------------------------------

def update_posteriors(topic: dict, new_posteriors: dict[str, float],
                      reason: str, evidence_refs: list[str] = None) -> dict:
    """
    Apply a posterior update with governor pre-commit gate.

    new_posteriors: {"H1": 0.30, "H2": 0.40, ...}
    reason: why the update happened
    evidence_refs: list of evidence log timestamps/IDs supporting this update

    Governor gate: runs hallucination failure mode checklist before applying.
    CRITICAL failures block the update (GovernanceError).
    Warnings are logged to the evidence log but the update proceeds.
    """
    hypotheses = topic["model"]["hypotheses"]

    # Validate all keys exist
    for k in new_posteriors:
        if k not in hypotheses:
            raise ValueError(f"Unknown hypothesis: {k}")

    # Build merged posteriors (keep old values for keys not in update)
    merged = {}
    for k in hypotheses:
        merged[k] = new_posteriors.get(k, hypotheses[k]["posterior"])

    # Enforce sum-to-1
    total = sum(merged.values())
    if abs(total - 1.0) > 0.01:
        raise ValueError(f"Posteriors sum to {total:.4f}, must be ~1.0")

    # Normalize to exactly 1.0
    for k in merged:
        merged[k] = round(merged[k] / total, 4)

    # === GOVERNOR HARD GATE: Hallucination Checklist ===
    proposal_check = check_update_proposal(
        topic, merged, evidence_refs=evidence_refs, reason=reason
    )

    if not proposal_check["passed"]:
        raise GovernanceError(
            f"Posterior update blocked by governance: "
            f"{', '.join(proposal_check['failures'])}",
            failures=proposal_check["failures"],
            warnings=proposal_check["warnings"],
        )

    # Non-critical warnings: log them but proceed
    if proposal_check["warnings"]:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"GOVERNANCE WARNING on posterior update: "
                     f"{', '.join(proposal_check['warnings'])}. "
                     f"Reason: {reason}"),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "PROPOSED",
            "effectiveWeight": 0.5,
        })

    # Belt-and-suspenders: original confidence gate
    max_shift = max(abs(merged[k] - hypotheses[k]["posterior"]) for k in merged)
    if max_shift > 0.10 and evidence_refs is None:
        raise ValueError(
            f"Major posterior shift ({max_shift:.0%}) requires evidence_refs. "
            "Provide references to supporting evidence entries."
        )

    # Apply
    for k in merged:
        hypotheses[k]["posterior"] = merged[k]

    # Recompute expected value
    topic["model"]["expectedValue"] = round(
        sum(h["midpoint"] * h["posterior"] for h in hypotheses.values()), 2
    )

    # Append to history
    history_entry = {"date": _now_iso()[:10]}
    for k in hypotheses:
        history_entry[k] = hypotheses[k]["posterior"]
    history_entry["note"] = reason

    # --- Epistemic improvement: red team challenge ---
    try:
        from framework.red_team import generate_red_team
        red_team = generate_red_team(topic, merged)
        history_entry["redTeam"] = red_team
        if red_team.get("devil_advocate_score", 0) > 0.6:
            warnings.warn(
                f"Red team score {red_team['devil_advocate_score']:.2f} — "
                f"strong counter-case exists: {red_team.get('challenge', '')[:100]}",
                stacklevel=2,
            )
    except ImportError:
        pass  # Framework module not available

    topic["model"].setdefault("posteriorHistory", []).append(history_entry)

    # --- Epistemic improvement: prediction snapshot ---
    try:
        from framework.scoring import snapshot_posteriors
        snapshot_posteriors(topic, trigger="posterior_update")
    except ImportError:
        pass  # Framework module not available

    return topic


def _resolve_evidence_weight(topic: dict, evidence_refs: list[str]) -> tuple[float, list[dict]]:
    """
    Resolve evidence_refs to log entries and compute aggregate effectiveWeight.

    Returns (aggregate_weight, resolved_entries_detail) where detail is for
    audit logging. Falls back to 1.0 if no refs resolve.

    Uses the same matching pattern as governor.check_update_proposal():
    match by entry's `time` field or first 50 chars of `text`.
    """
    evidence_log = topic.get("evidenceLog", [])
    weights = []
    detail = []

    for ref in evidence_refs:
        for e in evidence_log:
            if e.get("time") == ref or e.get("text", "")[:50] == ref[:50]:
                w = e.get("effectiveWeight", 1.0)
                weights.append(w)
                detail.append({
                    "ref": ref[:50],
                    "effectiveWeight": w,
                    "claimState": e.get("claimState"),
                })
                break  # first match per ref

    if not weights:
        return 1.0, detail

    return round(sum(weights) / len(weights), 4), detail


def bayesian_update(topic: dict, likelihoods: dict[str, float],
                    reason: str, evidence_refs: list[str],
                    operator_posteriors: dict[str, float] = None) -> dict:
    """
    Mechanistic Bayesian posterior update from explicit likelihood ratios.

    Unlike update_posteriors() (which accepts operator-supplied posteriors
    directly), this function computes posteriors via Bayes' theorem:

        P(H_i|E) = P(E|H_i) * P(H_i) / sum_j( P(E|H_j) * P(H_j) )

    likelihoods: {H1: P(E|H1), H2: P(E|H2), ...} — the probability of
        the observed evidence under each hypothesis. Values in (0, 1].
    reason: why the update happened.
    evidence_refs: list of evidence log timestamps supporting this update.
        Required (not optional) — mechanistic updates must cite evidence.
    operator_posteriors: optional. If supplied, the function computes KL
        divergence between the mechanically derived posteriors and the
        operator's intuition. Divergence > 0.05 nats logs a governance
        note. The mechanical posteriors are always the ones applied.

    Returns the mutated topic (same pattern as update_posteriors).
    Raises GovernanceError if governor pre-commit checks fail.
    """
    import math as _math

    hypotheses = topic["model"]["hypotheses"]

    # Validate: all hypothesis keys must be present
    for k in hypotheses:
        if k not in likelihoods:
            raise ValueError(
                f"Missing likelihood for hypothesis {k}. "
                "All hypotheses must have a likelihood value."
            )

    # Validate: likelihoods must be positive
    for k, l in likelihoods.items():
        if k not in hypotheses:
            raise ValueError(f"Unknown hypothesis: {k}")
        if l <= 0 or l > 1:
            raise ValueError(
                f"Likelihood for {k} is {l} — must be in (0, 1]"
            )

    # --- Mixture model attenuation ---
    # Resolve evidence_refs to their effectiveWeights and attenuate likelihoods
    # using a proper probabilistic mixture model:
    #
    #   P(E|H_i) = w * P(E|H_i, real) + (1-w) * P(E|noise)
    #
    # where P(E|noise) = mean of raw likelihoods (uninformative — same for all H,
    # so it contributes zero posterior movement after normalization).
    #
    # This is coherent: it models "with probability w, the evidence is genuine;
    # otherwise it's noise." Unlike linear interpolation toward 1.0, this
    # preserves likelihood direction at all weight levels.
    evidence_weight, weight_detail = _resolve_evidence_weight(topic, evidence_refs)

    if evidence_weight < 1.0:
        # P(E|noise) = mean likelihood (uniform across hypotheses → no update)
        noise_likelihood = sum(likelihoods.values()) / len(likelihoods)

        adjusted_likelihoods = {}
        for k, raw_l in likelihoods.items():
            adjusted_likelihoods[k] = round(
                evidence_weight * raw_l + (1.0 - evidence_weight) * noise_likelihood,
                6,
            )

        if evidence_weight < 0.3:
            _add_evidence_raw(topic, {
                "time": _now_iso(),
                "tag": "INTEL",
                "text": (f"WEIGHT ATTENUATION: bayesian_update likelihoods attenuated "
                         f"to {evidence_weight:.0%} strength via mixture model — "
                         f"evidence refs have low effectiveWeight ({weight_detail})"),
                "provenance": "DERIVED",
                "posteriorImpact": "NONE",
                "ledger": "DECISION",
                "claimState": "PROPOSED",
                "effectiveWeight": 0.5,
            })
    else:
        adjusted_likelihoods = dict(likelihoods)

    # Compute unnormalized posteriors: prior * adjusted likelihood
    unnormalized = {}
    for k, h in hypotheses.items():
        unnormalized[k] = h["posterior"] * adjusted_likelihoods[k]

    # Normalize
    total = sum(unnormalized.values())
    if total == 0:
        raise ValueError("All unnormalized posteriors are zero — likelihoods "
                         "are incompatible with current priors")

    computed = {k: round(v / total, 4) for k, v in unnormalized.items()}

    # If operator also supplied their intuition, compare
    if operator_posteriors is not None:
        op_total = sum(operator_posteriors.values())
        if abs(op_total - 1.0) > 0.01:
            raise ValueError(f"operator_posteriors sum to {op_total:.4f}")
        op_norm = {k: v / op_total for k, v in operator_posteriors.items()}

        # KL divergence: D_KL(computed || operator)
        kl = 0.0
        for k in computed:
            p = computed[k]
            q = op_norm.get(k, 0.0)
            if p > 0 and q > 0:
                kl += p * _math.log(p / q)
            elif p > 0 and q == 0:
                kl = float("inf")
                break

        if kl > 0.05:
            _add_evidence_raw(topic, {
                "time": _now_iso(),
                "tag": "INTEL",
                "text": (f"LIKELIHOOD AUDIT: operator intuition diverges from "
                         f"mechanical Bayes (KL={kl:.3f} nats). "
                         f"Mechanical: {computed}. Operator: {op_norm}. "
                         f"Applying mechanical result."),
                "provenance": "DERIVED",
                "posteriorImpact": "NONE",
                "ledger": "DECISION",
                "claimState": "PROPOSED",
                "effectiveWeight": 0.5,
            })

    # Governor hard gate — same checks as update_posteriors
    proposal_check = check_update_proposal(
        topic, computed, evidence_refs=evidence_refs, reason=reason
    )

    if not proposal_check["passed"]:
        raise GovernanceError(
            f"Bayesian update blocked by governance: "
            f"{', '.join(proposal_check['failures'])}",
            failures=proposal_check["failures"],
            warnings=proposal_check["warnings"],
        )

    if proposal_check["warnings"]:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"GOVERNANCE WARNING on bayesian_update: "
                     f"{', '.join(proposal_check['warnings'])}. "
                     f"Reason: {reason}"),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "PROPOSED",
            "effectiveWeight": 0.5,
        })

    # --- Cross-session likelihood drift detection ---
    # Compare these likelihoods against the most recent bayesian_update
    # in posteriorHistory that used the same evidence tag. If the same
    # kind of evidence produces significantly different likelihoods across
    # sessions, that's an inconsistency the operator should know about.
    history = topic["model"].get("posteriorHistory", [])
    if history:
        # Find the evidence tag for this update (from the first cited entry)
        current_tag = None
        for ref in evidence_refs:
            for e in topic.get("evidenceLog", []):
                if e.get("time") == ref or e.get("text", "")[:50] == ref[:50]:
                    current_tag = e.get("tag")
                    break
            if current_tag:
                break

        if current_tag:
            # Search recent history for a bayesian_update with likelihoods
            # on the same tag
            for prev in reversed(history[-20:]):
                prev_likelihoods = prev.get("likelihoods")
                if not prev_likelihoods:
                    continue
                # Check if the previous entry's note references the same tag
                prev_note = prev.get("note", "").upper()
                if current_tag not in prev_note:
                    continue

                # Compute max likelihood divergence
                max_divergence = 0.0
                for hk in likelihoods:
                    if hk in prev_likelihoods:
                        max_divergence = max(
                            max_divergence,
                            abs(likelihoods[hk] - prev_likelihoods[hk]),
                        )

                if max_divergence > 0.20:
                    _add_evidence_raw(topic, {
                        "time": _now_iso(),
                        "tag": "INTEL",
                        "text": (f"LIKELIHOOD DRIFT: {current_tag} evidence produced "
                                 f"likelihoods diverging by {max_divergence:.2f} from "
                                 f"previous {current_tag} update (prior: "
                                 f"{prev_likelihoods}, current: {dict(likelihoods)}). "
                                 f"Check for cross-session inconsistency."),
                        "provenance": "DERIVED",
                        "posteriorImpact": "NONE",
                        "ledger": "DECISION",
                        "claimState": "PROPOSED",
                        "effectiveWeight": 0.5,
                    })
                break  # only compare against most recent matching entry

    # Apply
    for k in computed:
        hypotheses[k]["posterior"] = computed[k]

    # Recompute expected value
    topic["model"]["expectedValue"] = round(
        sum(h["midpoint"] * h["posterior"] for h in hypotheses.values()), 2
    )

    # Append to history — include likelihoods for auditability
    history_entry = {"date": _now_iso()[:10]}
    for k in hypotheses:
        history_entry[k] = hypotheses[k]["posterior"]
    history_entry["note"] = reason
    history_entry["likelihoods"] = likelihoods
    if evidence_weight < 1.0:
        history_entry["adjustedLikelihoods"] = adjusted_likelihoods
        history_entry["evidenceWeight"] = evidence_weight
        history_entry["weightDetail"] = weight_detail

    # Red team challenge
    try:
        from framework.red_team import generate_red_team
        red_team = generate_red_team(topic, computed)
        history_entry["redTeam"] = red_team
        if red_team.get("devil_advocate_score", 0) > 0.6:
            warnings.warn(
                f"Red team score {red_team['devil_advocate_score']:.2f} — "
                f"strong counter-case exists: {red_team.get('challenge', '')[:100]}",
                stacklevel=2,
            )
    except ImportError:
        pass

    topic["model"].setdefault("posteriorHistory", []).append(history_entry)

    # Prediction snapshot
    try:
        from framework.scoring import snapshot_posteriors
        snapshot_posteriors(topic, trigger="bayesian_update")
    except ImportError:
        pass

    return topic


def hold_posteriors(topic: dict, reason: str = "No new indicators") -> dict:
    """Record that posteriors were reviewed but not changed."""
    add_evidence(topic, {
        "tag": "INTEL",
        "text": f"Posteriors HELD: {reason}",
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
    })
    return topic


def update_submodel(topic: dict, submodel_id: str,
                    new_probs: dict[str, float], reason: str,
                    evidence_refs: list[str] = None) -> dict:
    """
    Update sub-model scenario probabilities with governor gate.

    submodel_id: key in topic["subModels"] (e.g. "meuMission")
    new_probs: {"kharg": 0.60, "declareVictory": 0.12, ...}
    reason: why the update happened
    evidence_refs: list of evidence timestamps supporting this update

    Governor gate: runs hallucination checklist (same as posterior updates).
    CRITICAL failures block the update. Warnings are logged.
    Evidence trail is always recorded.
    """
    submodels = topic.get("subModels", {})
    if submodel_id not in submodels:
        raise ValueError(f"Unknown sub-model: {submodel_id}")

    sm = submodels[submodel_id]
    scenarios = sm.get("scenarios", {})

    # Validate all keys exist
    for k in new_probs:
        if k not in scenarios:
            raise ValueError(f"Unknown scenario in {submodel_id}: {k}")

    # Build merged probabilities
    merged = {}
    for k in scenarios:
        merged[k] = new_probs.get(k, scenarios[k]["prob"])

    # Enforce sum-to-1
    total = sum(merged.values())
    if abs(total - 1.0) > 0.01:
        raise ValueError(f"Sub-model probs sum to {total:.4f}, must be ~1.0")

    # Normalize
    for k in merged:
        merged[k] = round(merged[k] / total, 4)

    # === GOVERNOR HARD GATE ===
    # Use the same hallucination checklist as posteriors — sub-model shifts
    # are posterior shifts on a nested question, same epistemic discipline applies.
    # Pass current posteriors unchanged so governor checks run without
    # flagging a phantom shift on the main model.
    current_posteriors = {
        k: v["posterior"] for k, v in topic["model"]["hypotheses"].items()
    }
    proposal_check = check_update_proposal(
        topic, current_posteriors, evidence_refs=evidence_refs, reason=reason
    )

    if not proposal_check["passed"]:
        raise GovernanceError(
            f"Sub-model update ({submodel_id}) blocked by governance: "
            f"{', '.join(proposal_check['failures'])}",
            failures=proposal_check["failures"],
            warnings=proposal_check["warnings"],
        )

    # Non-critical warnings: log them
    if proposal_check["warnings"]:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"GOVERNANCE WARNING on sub-model update ({submodel_id}): "
                     f"{', '.join(proposal_check['warnings'])}. "
                     f"Reason: {reason}"),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "PROPOSED",
            "effectiveWeight": 0.5,
        })

    # Require evidence refs for large shifts
    max_shift = max(abs(merged[k] - scenarios[k]["prob"]) for k in merged)
    if max_shift > 0.10 and evidence_refs is None:
        raise ValueError(
            f"Major sub-model shift ({max_shift:.0%}) requires evidence_refs."
        )

    # Record old values for audit trail
    old_probs = {k: scenarios[k]["prob"] for k in scenarios}

    # Apply
    for k in merged:
        scenarios[k]["prob"] = merged[k]

    # Log the update as governor-enriched evidence
    shifts = []
    for k in merged:
        delta = merged[k] - old_probs[k]
        if abs(delta) >= 0.005:
            shifts.append(f"{k}: {old_probs[k]:.0%}->{merged[k]:.0%}")
    shift_str = ", ".join(shifts) if shifts else "no change"

    add_evidence(topic, {
        "tag": "INTEL",
        "text": (f"SUB-MODEL UPDATE ({submodel_id}): {shift_str}. "
                 f"Reason: {reason}"),
        "provenance": "DERIVED",
        "posteriorImpact": "MODERATE",
    })

    return topic


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

import re as _re


# Tier-based default magnitudes (pp) for qualitative posteriorEffect strings
_TIER_MAGNITUDE = {
    "tier1_critical":   {"strong": 22, "moderate": 15},
    "tier2_strong":     {"strong": 12, "moderate": 8},
    "tier3_suggestive": {"strong": 4,  "moderate": 3},
    "anti_indicators":  {"strong": 10, "moderate": 8},
}

# Words that map to strong/moderate magnitude
_STRONG_WORDS = {"surge", "collapse", "plunge", "spike", "soar", "crash"}
_MODERATE_WORDS = {"up", "down", "rise", "fall", "increase", "decrease", "flat"}
_NEGATIVE_WORDS = {"collapse", "plunge", "crash", "down", "fall", "decrease", "flat"}


def _parse_posterior_effect(effect_str: str, tier_key: str,
                            hypothesis_keys: list[str]) -> dict:
    """
    Parse a posteriorEffect string into structured per-hypothesis pp shifts.

    Returns {
        "shifts": {"H1": +10, "H3": -5, ...},  # pp shifts per hypothesis
        "submodel_refs": ["kharg", ...],         # submodel scenario names found
        "confidence": "HIGH" | "MEDIUM" | "LOW" | "UNPARSEABLE",
        "warnings": [...],
    }
    """
    shifts = {}
    submodel_refs = []
    warnings_list = []
    confidence = "UNPARSEABLE"
    h_pattern = "|".join(_re.escape(k) for k in hypothesis_keys)

    # Pattern 1: explicit Hx +/-Npp or Hx +N-Mpp (possibly grouped H1/H2)
    # Matches: "H1 +25pp", "H3/H4 +5-10pp", "H1 -10pp"
    p1 = _re.compile(
        rf'((?:{h_pattern})(?:/(?:{h_pattern}))*)\s*([+-])\s*(\d+)(?:-(\d+))?\s*pp',
        _re.IGNORECASE,
    )
    for m in p1.finditer(effect_str):
        h_group = _re.findall(rf'({h_pattern})', m.group(1), _re.IGNORECASE)
        sign = 1 if m.group(2) == "+" else -1
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) else lo
        magnitude = (lo + hi) / 2.0
        per_h = magnitude / len(h_group)
        for h in h_group:
            h_upper = h.upper()
            shifts[h_upper] = shifts.get(h_upper, 0) + sign * per_h
        confidence = "HIGH"

    # Pattern 2: Hx/Hy with qualitative direction word (no pp number)
    # Matches: "H3/H4 surge", "H1/H2 collapse", "H3/H4 up"
    p2 = _re.compile(
        rf'((?:{h_pattern})(?:/(?:{h_pattern}))*)\s+(surge|collapse|plunge|spike|soar|crash|up|down|rise|fall|increase|decrease|flat)',
        _re.IGNORECASE,
    )
    for m in p2.finditer(effect_str):
        h_group = _re.findall(rf'({h_pattern})', m.group(1), _re.IGNORECASE)
        word = m.group(2).lower()
        tier_mag = _TIER_MAGNITUDE.get(tier_key, _TIER_MAGNITUDE["tier2_strong"])
        mag = tier_mag["strong"] if word in _STRONG_WORDS else tier_mag["moderate"]
        sign = -1 if word in _NEGATIVE_WORDS else 1
        per_h = mag / len(h_group)
        for h in h_group:
            h_upper = h.upper()
            if h_upper not in shifts:  # don't overwrite explicit pp from pattern 1
                shifts[h_upper] = shifts.get(h_upper, 0) + sign * per_h
        if confidence == "UNPARSEABLE":
            confidence = "MEDIUM"

    # Pattern 3: submodel references with pp — "Kharg +10-15pp"
    # Match any word that isn't an H-key followed by +/- Npp
    p3 = _re.compile(
        r'(\b[A-Z][a-z]+\b)\s*([+-])\s*(\d+)(?:-(\d+))?\s*pp',
    )
    for m in p3.finditer(effect_str):
        name = m.group(1).lower()
        # Skip if it's a hypothesis key
        if name.upper() in [k.upper() for k in hypothesis_keys]:
            continue
        sign = 1 if m.group(2) == "+" else -1
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) else lo
        submodel_refs.append({
            "name": name,
            "shift_pp": sign * (lo + hi) / 2.0,
        })
        if confidence == "UNPARSEABLE":
            confidence = "LOW"

    # Pattern 4: submodel → percentage — "Kharg → 80%+"
    p4 = _re.compile(r'(\b[A-Z][a-z]+\b)\s*(?:→|->)\s*(\d+)%')
    for m in p4.finditer(effect_str):
        name = m.group(1).lower()
        if name.upper() in [k.upper() for k in hypothesis_keys]:
            continue
        submodel_refs.append({
            "name": name,
            "target_pct": int(m.group(2)),
        })
        if confidence == "UNPARSEABLE":
            confidence = "LOW"

    # Pattern 5: "Hx -Xpp" template placeholder (anti-indicators)
    if _re.search(rf'({h_pattern})\s*-\s*Xpp', effect_str, _re.IGNORECASE):
        h_match = _re.search(rf'({h_pattern})', effect_str, _re.IGNORECASE)
        if h_match:
            tier_mag = _TIER_MAGNITUDE.get(tier_key, _TIER_MAGNITUDE["anti_indicators"])
            h_upper = h_match.group(1).upper()
            shifts[h_upper] = shifts.get(h_upper, 0) - tier_mag["strong"]
            confidence = "MEDIUM"
            warnings_list.append(f"Template placeholder 'Xpp' replaced with tier default ({tier_mag['strong']}pp)")

    # Catch unparseable
    if confidence == "UNPARSEABLE" and not submodel_refs:
        warnings_list.append(f"Could not parse posteriorEffect: '{effect_str}'")

    return {
        "shifts": shifts,
        "submodel_refs": submodel_refs,
        "confidence": confidence,
        "warnings": warnings_list,
    }


def _resolve_submodel_shifts(topic: dict, submodel_refs: list[dict],
                             hypothesis_keys: list[str]) -> dict:
    """
    Convert submodel references into hypothesis-level pp shifts using
    the topic's subModel conditionals.

    If a submodel scenario has a conditional mapping (e.g. khargConditionalHormuz),
    use the difference between that conditional distribution and current priors
    as the shift direction, scaled by the submodel pp magnitude.
    """
    hypotheses = topic["model"]["hypotheses"]
    current = {k: h["posterior"] for k, h in hypotheses.items()}
    shifts = {}

    submodels = topic.get("subModels", {})

    for ref in submodel_refs:
        name = ref["name"]
        # Find the submodel and scenario
        for sm_id, sm in submodels.items():
            scenarios = sm.get("scenarios", {})
            if name not in scenarios:
                continue

            conditionals = sm.get("conditionals", {})
            # Look for a conditional like "{name}ConditionalHormuz" or similar
            cond_key = None
            for ck in conditionals:
                if name in ck.lower():
                    cond_key = ck
                    break

            if not cond_key:
                continue

            cond = conditionals[cond_key]

            if "target_pct" in ref:
                # "Kharg → 80%" — scale the conditional's direction by how much
                # this indicator moves the submodel
                current_prob = scenarios[name]["prob"]
                target_prob = ref["target_pct"] / 100.0
                scale = max(0, target_prob - current_prob)
            elif "shift_pp" in ref:
                # "Kharg +10pp" — use the pp shift as a fraction of movement
                scale = abs(ref["shift_pp"]) / 100.0
                if ref["shift_pp"] < 0:
                    scale = -scale
            else:
                continue

            # The conditional tells us: if this scenario is more likely,
            # which hypotheses benefit? Use the difference from current priors.
            for hk in hypothesis_keys:
                if hk in cond:
                    direction = cond[hk] - current.get(hk, 0)
                    shift_pp = direction * scale * 100
                    shifts[hk] = shifts.get(hk, 0) + shift_pp

    return shifts


def _pp_shifts_to_likelihoods(priors: dict[str, float],
                               shifts: dict[str, float]) -> dict[str, float]:
    """
    Convert per-hypothesis pp shifts into likelihood ratios via inverse Bayes.

    Given current priors P(Hi) and desired shifts delta_i (in pp, e.g. +10 = +0.10):
    1. Compute target posteriors: target[Hi] = prior[Hi] + delta_i/100
    2. Clamp to [0.005, 0.995]
    3. Renormalize targets to sum to 1.0
    4. Derive likelihoods: L(Hi) = target[Hi] / prior[Hi]
    5. Normalize likelihoods so max = 1.0 (keeps values in (0, 1])
    """
    # Build target posteriors
    target = {}
    for k, p in priors.items():
        delta = shifts.get(k, 0.0) / 100.0
        target[k] = max(0.005, min(0.995, p + delta))

    # Renormalize
    total = sum(target.values())
    target = {k: v / total for k, v in target.items()}

    # Derive raw likelihoods
    raw = {}
    for k in priors:
        if priors[k] > 0:
            raw[k] = target[k] / priors[k]
        else:
            raw[k] = 1.0  # can't update a zero prior

    # Normalize to max = 1.0
    max_l = max(raw.values()) if raw else 1.0
    if max_l == 0:
        max_l = 1.0
    likelihoods = {k: round(v / max_l, 6) for k, v in raw.items()}

    return likelihoods


def suggest_likelihoods(topic: dict, fired_indicator_ids: list[str],
                        *, override_effects: dict[str, dict] = None) -> dict:
    """
    Convert fired indicators into suggested likelihood ratios for bayesian_update().

    Parses each indicator's posteriorEffect string, combines shifts, resolves
    submodel references via conditionals, and computes inverse-Bayes likelihoods.

    Parameters
    ----------
    topic : dict
        Live topic state.
    fired_indicator_ids : list[str]
        Indicator IDs to derive likelihoods from.
    override_effects : dict, optional
        Manual overrides: {indicator_id: {"H1": +5, "H2": -3, ...}} in pp.
        Overrides parsed effects entirely for that indicator.

    Returns
    -------
    dict with keys:
        suggested_likelihoods: {H1: float, ...} ready for bayesian_update()
        target_posteriors: {H1: float, ...} what posteriors would result
        current_priors: {H1: float, ...}
        indicator_breakdown: list of per-indicator parse results
        combined_shifts_pp: {H1: float, ...} net pp shift per hypothesis
        unparseable: list of indicator IDs that couldn't be parsed
        warnings: list of warning strings
        ready: bool — True if all indicators parsed (or overridden)
    """
    override_effects = override_effects or {}
    hypotheses = topic["model"]["hypotheses"]
    h_keys = list(hypotheses.keys())
    priors = {k: h["posterior"] for k, h in hypotheses.items()}

    # Find indicators by ID
    all_indicators = {}
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            all_indicators[ind["id"]] = (ind, tier_key)

    indicator_breakdown = []
    combined_shifts = {k: 0.0 for k in h_keys}
    unparseable = []
    all_warnings = []

    for ind_id in fired_indicator_ids:
        if ind_id not in all_indicators:
            all_warnings.append(f"Indicator '{ind_id}' not found in topic")
            continue

        ind, tier_key = all_indicators[ind_id]

        # Manual override?
        if ind_id in override_effects:
            parsed = {
                "shifts": override_effects[ind_id],
                "submodel_refs": [],
                "confidence": "HIGH",
                "warnings": ["Operator override"],
            }
        else:
            parsed = _parse_posterior_effect(
                ind.get("posteriorEffect", ""), tier_key, h_keys,
            )

        # Resolve submodel references into H-level shifts
        if parsed["submodel_refs"]:
            sm_shifts = _resolve_submodel_shifts(
                topic, parsed["submodel_refs"], h_keys,
            )
            for k, v in sm_shifts.items():
                parsed["shifts"][k] = parsed["shifts"].get(k, 0) + v
            if sm_shifts:
                parsed["warnings"].append(
                    f"Submodel refs resolved via conditionals: {sm_shifts}"
                )
                if parsed["confidence"] == "LOW":
                    parsed["confidence"] = "MEDIUM"

        # Accumulate
        for k, v in parsed["shifts"].items():
            if k in combined_shifts:
                combined_shifts[k] += v

        if parsed["confidence"] == "UNPARSEABLE" and ind_id not in override_effects:
            unparseable.append(ind_id)

        indicator_breakdown.append({
            "id": ind_id,
            "tier": tier_key,
            "posteriorEffect": ind.get("posteriorEffect", ""),
            "parsed_shifts": parsed["shifts"],
            "submodel_refs": parsed["submodel_refs"],
            "confidence": parsed["confidence"],
            "warnings": parsed["warnings"],
        })
        all_warnings.extend(parsed["warnings"])

    # Cap combined shifts at 40pp per hypothesis
    for k in combined_shifts:
        if abs(combined_shifts[k]) > 40:
            all_warnings.append(
                f"Combined shift for {k} capped at {'+' if combined_shifts[k] > 0 else '-'}40pp "
                f"(was {combined_shifts[k]:+.1f}pp)"
            )
            combined_shifts[k] = 40.0 if combined_shifts[k] > 0 else -40.0

    # Convert to likelihoods
    likelihoods = _pp_shifts_to_likelihoods(priors, combined_shifts)

    # Compute what posteriors would result (forward Bayes)
    unnorm = {k: priors[k] * likelihoods[k] for k in h_keys}
    total = sum(unnorm.values())
    target_posteriors = {k: round(v / total, 4) for k, v in unnorm.items()} if total > 0 else priors

    return {
        "suggested_likelihoods": likelihoods,
        "target_posteriors": target_posteriors,
        "current_priors": priors,
        "indicator_breakdown": indicator_breakdown,
        "combined_shifts_pp": {k: round(v, 2) for k, v in combined_shifts.items()},
        "unparseable": unparseable,
        "warnings": all_warnings,
        "ready": len(unparseable) == 0,
    }


def fire_indicator(topic: dict, indicator_id: str,
                   note: str = None, tier: str = None) -> dict:
    """
    Mark an indicator as FIRED. Auto-detects tier if not specified.
    Returns the topic with updated indicator and suggested posterior effect.
    """
    found = False
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            if ind["id"] == indicator_id:
                ind["status"] = "FIRED"
                ind["firedDate"] = _now_iso()
                if note:
                    ind["note"] = note
                found = True
                tier = tier or tier_key
                break
        if found:
            break

    if not found:
        raise ValueError(f"Indicator not found: {indicator_id}")

    # Update classification
    topic["meta"]["classification"] = compute_classification(topic)

    return topic


def partial_indicator(topic: dict, indicator_id: str, note: str) -> dict:
    """Mark an indicator as PARTIAL (partially confirmed)."""
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            if ind["id"] == indicator_id:
                ind["status"] = "PARTIAL"
                if note:
                    ind["note"] = note
                topic["meta"]["classification"] = compute_classification(topic)
                return topic
    raise ValueError(f"Indicator not found: {indicator_id}")


def compute_classification(topic: dict) -> str:
    """Determine ROUTINE/ELEVATED/ALERT from indicator state."""
    tiers = topic["indicators"]["tiers"]

    # Tier 1 FIRED → ALERT
    for ind in tiers.get("tier1_critical", []):
        if ind["status"] == "FIRED":
            return "ALERT"

    # Tier 1 PARTIAL or Tier 2 FIRED → ELEVATED
    for ind in tiers.get("tier1_critical", []):
        if ind["status"] == "PARTIAL":
            return "ELEVATED"
    for ind in tiers.get("tier2_strong", []):
        if ind["status"] == "FIRED":
            return "ELEVATED"

    return "ROUTINE"


def get_indicator_summary(topic: dict) -> dict:
    """Return counts of fired/partial/not_fired per tier."""
    summary = {}
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        counts = {"FIRED": 0, "PARTIAL": 0, "NOT_FIRED": 0}
        for ind in indicators:
            counts[ind.get("status", "NOT_FIRED")] += 1
        summary[tier_key] = counts
    return summary


# ---------------------------------------------------------------------------
# Evidence Log
# ---------------------------------------------------------------------------

def add_evidence(topic: dict, entry: dict) -> dict:
    """
    Add an evidence entry to the log with governor enrichment.

    Required fields: tag, text
    Optional: provenance, source, posteriorImpact

    Governor enrichment (auto-computed, caller can override):
      - ledger: FACT or DECISION (from tag classification)
      - claimState: PROPOSED/SUPPORTED/CONTESTED/INVALIDATED
      - effectiveWeight: 0.0-1.0 (from claim state)
    """
    if "tag" not in entry or "text" not in entry:
        raise ValueError("Evidence entry requires 'tag' and 'text'")

    full_entry = {
        "time": entry.get("time") or _now_iso(),
        "tag": entry["tag"],
        "text": entry["text"],
        "provenance": entry.get("provenance", "OBSERVED"),
        "source": entry.get("source"),
        "posteriorImpact": entry.get("posteriorImpact", "NONE"),
    }

    # Information-chain tracking: entries sharing a chain ID trace to the
    # same primary source and should not count as independent corroboration.
    if entry.get("informationChain"):
        full_entry["informationChain"] = entry["informationChain"]

    # Deduplication: don't add if identical text exists in last 10 entries
    recent = topic.get("evidenceLog", [])[-10:]
    for existing in recent:
        if existing.get("text", "").strip() == full_entry["text"].strip():
            return topic  # Skip duplicate

    # Governor enrichment: classify and weight the evidence
    evidence_log = topic.get("evidenceLog", [])
    full_entry["ledger"] = entry.get("ledger") or classify_evidence(full_entry)
    full_entry["claimState"] = entry.get("claimState") or assess_claim_state(
        full_entry, evidence_log
    )
    full_entry["effectiveWeight"] = entry.get("effectiveWeight") or get_effective_weight(
        full_entry, evidence_log, topic=topic
    )

    # --- Epistemic improvement: contradiction detection ---
    try:
        from framework.contradictions import detect_contradictions
        contradictions = detect_contradictions(topic, full_entry)
        if contradictions:
            # Override claim state to CONTESTED if contradictions found
            full_entry["claimState"] = "CONTESTED"
            full_entry["effectiveWeight"] = min(full_entry["effectiveWeight"], 0.5)
    except ImportError:
        pass  # Framework module not available, skip

    topic.setdefault("evidenceLog", []).append(full_entry)
    return topic


def _add_evidence_raw(topic: dict, entry: dict) -> dict:
    """
    Append a pre-built evidence entry directly (no enrichment, no dedup).
    Used internally by governance warnings to avoid recursion.
    """
    topic.setdefault("evidenceLog", []).append(entry)
    return topic


# ---------------------------------------------------------------------------
# Data Feeds
# ---------------------------------------------------------------------------

def update_feed(topic: dict, feed_id: str, value, as_of: str = None) -> dict:
    """Update a data feed value."""
    if feed_id not in topic.get("dataFeeds", {}):
        raise ValueError(f"Unknown feed: {feed_id}")
    topic["dataFeeds"][feed_id]["value"] = value
    topic["dataFeeds"][feed_id]["asOf"] = as_of or _now_iso()
    return topic


# ---------------------------------------------------------------------------
# Briefing Generation
# ---------------------------------------------------------------------------

def generate_brief(topic: dict, mode: str = "routine",
                   developments: list[str] = None) -> str:
    """Generate a structured briefing markdown string with governance health."""
    meta = topic["meta"]
    model = topic["model"]
    hypotheses = model["hypotheses"]

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H%M UTC")

    # Header
    lines = [
        f"# {meta['title']} BRIEF — {timestamp}",
        f"## Classification: {meta['classification']}",
        f"## Day {meta.get('dayCount', '?')} since tracking began",
        "",
    ]

    # Developments
    lines.append("### BREAKING / NEW DEVELOPMENTS")
    if developments:
        for d in developments:
            lines.append(f"- {d}")
    else:
        recent = topic.get("evidenceLog", [])[-10:]
        if recent:
            for e in reversed(recent):
                lines.append(f"- **[{e['tag']}]** {e['text']}")
        else:
            lines.append("- No new developments.")
    lines.append("")

    # Indicator status
    lines.append("### INDICATOR STATUS")
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        fired = [i for i in indicators if i["status"] in ("FIRED", "PARTIAL")]
        if fired:
            tier_label = tier_key.upper().replace("_", " ")
            for ind in fired:
                lines.append(f"- **{tier_label}** [{ind['status']}] {ind['desc']}")
                if ind.get("note"):
                    lines.append(f"  - {ind['note']}")
    if not any(i["status"] != "NOT_FIRED"
               for inds in topic["indicators"]["tiers"].values()
               for i in inds):
        lines.append("- All indicators quiet.")
    lines.append("")

    # Data feeds
    if topic.get("dataFeeds"):
        lines.append("### DATA FEEDS")
        for fid, feed in topic["dataFeeds"].items():
            baseline_note = ""
            if feed.get("baseline") and feed.get("value"):
                try:
                    pct = ((feed["value"] - feed["baseline"]) / feed["baseline"]) * 100
                    direction = "+" if pct > 0 else ""
                    baseline_note = f" ({direction}{pct:.1f}% vs baseline)"
                except (TypeError, ZeroDivisionError):
                    pass
            lines.append(
                f"- **{feed['label']}**: {feed['value']} {feed.get('unit', '')}"
                f"{baseline_note}"
            )
        lines.append("")

    # Posteriors
    lines.append("### POSTERIORS")
    parts = []
    for k, h in hypotheses.items():
        parts.append(f"{k}={h['posterior']:.0%}")
    ev = model.get("expectedValue", "?")
    eu = model.get("expectedUnit", "")
    lines.append(" | ".join(parts) + f" | E[{eu}]={ev}")

    # Check if posteriors changed in this session
    history = model.get("posteriorHistory", [])
    if len(history) >= 2:
        prev = history[-2]
        curr = history[-1]
        changed = any(abs(curr.get(k, 0) - prev.get(k, 0)) > 0.005
                       for k in hypotheses)
        if changed:
            lines.append(f"**UPDATED** — {curr.get('note', 'see evidence log')}")
        else:
            lines.append("**HELD** — no new indicators")
    else:
        lines.append("**HELD** — no new indicators")
    lines.append("")

    # Epistemic Health (from embedded governance snapshot)
    gov = topic.get("governance")
    if gov:
        lines.append("### EPISTEMIC HEALTH")
        rt = gov.get("rt", {})
        lines.append(
            f"- **Status**: {gov.get('health', '?')} | "
            f"R_t={rt.get('rt', '?'):.2f} ({rt.get('regime', '?')}) | "
            f"Entropy={gov.get('entropy', 0):.2f}/{gov.get('maxEntropy', 0):.2f} "
            f"({gov.get('uncertaintyRatio', 0):.0%} uncertainty)"
        )
        fresh = gov.get("evidenceFreshness", {})
        if fresh:
            lines.append(
                f"- **Evidence**: {fresh.get('fresh', '?')} fresh / "
                f"{fresh.get('stale', '?')} stale / "
                f"{fresh.get('total', '?')} total"
            )
        admissibility = gov.get("hypothesisAdmissibility", {})
        inadmissible_h = [k for k, v in admissibility.items() if v == "INADMISSIBLE"]
        marginal_h = [k for k, v in admissibility.items() if v == "MARGINAL"]
        if inadmissible_h:
            lines.append(f"- **WARNING**: Inadmissible hypotheses: {', '.join(inadmissible_h)}")
        if marginal_h:
            lines.append(f"- **NOTE**: Marginal hypotheses: {', '.join(marginal_h)}")
        gov_issues = gov.get("issues", [])
        if gov_issues:
            for issue in gov_issues:
                lines.append(f"- {issue}")
        lines.append("")

    # Sub-models
    if topic.get("subModels"):
        lines.append("### SUB-MODELS")
        for sm_key, sm in topic["subModels"].items():
            if "scenarios" in sm:
                lines.append(f"**{sm_key}**:")
                for sk, sv in sm["scenarios"].items():
                    lines.append(f"  - {sv.get('label', sk)}: {sv.get('prob', '?'):.0%}")
        lines.append("")

    # Watchpoints
    if topic.get("watchpoints"):
        lines.append("### KEY WATCHPOINTS NEXT 12-24H")
        for wp in topic["watchpoints"]:
            # Skip malformed watchpoints (missing event key)
            if 'event' not in wp:
                continue
            lines.append(f"- **{wp['time']}** — {wp['event']}")
            if wp.get("watch"):
                lines.append(f"  - {wp['watch']}")
        lines.append("")

    return "\n".join(lines)


def save_brief(topic: dict, brief_text: str) -> str:
    """Save a briefing to disk. Returns the file path."""
    slug = topic["meta"]["slug"]
    brief_dir = BRIEFS_DIR / slug
    brief_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y-%m-%d-%H%M") + ".md"
    path = brief_dir / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(brief_text)
    return str(path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_topic(topic: dict) -> dict:
    """
    Validate a topic state file.
    Raises ValueError on structural issues.
    Returns admissibility report for informational use.
    """
    if "meta" not in topic:
        raise ValueError("Topic missing 'meta' section")
    if "model" not in topic:
        raise ValueError("Topic missing 'model' section")
    if "hypotheses" not in topic["model"]:
        raise ValueError("Topic missing 'model.hypotheses'")
    if "indicators" not in topic:
        raise ValueError("Topic missing 'indicators' section")

    meta = topic["meta"]
    for field in ("slug", "title", "question", "resolution"):
        if field not in meta:
            raise ValueError(f"Topic meta missing '{field}'")

    # Validate posteriors sum to ~1.0
    hypotheses = topic["model"]["hypotheses"]
    total = sum(h["posterior"] for h in hypotheses.values())
    if abs(total - 1.0) > 0.02:
        raise ValueError(f"Posteriors sum to {total:.4f}, expected ~1.0")

    # Governor: admissibility check (non-blocking on load)
    admissibility = validate_hypotheses(topic)
    inadmissible = [k for k, v in admissibility.items()
                    if v["grade"] == "INADMISSIBLE"]
    if inadmissible:
        warnings.warn(
            f"Topic '{meta.get('slug', '?')}' has INADMISSIBLE hypotheses: "
            f"{', '.join(inadmissible)}. Consider revising.",
            stacklevel=2,
        )

    return admissibility


# ---------------------------------------------------------------------------
# Day Count
# ---------------------------------------------------------------------------

def update_day_count(topic: dict) -> dict:
    """Update the dayCount based on startDate."""
    start = topic["meta"].get("startDate")
    if start:
        start_date = datetime.fromisoformat(start).date()
        today = datetime.now(timezone.utc).date()
        topic["meta"]["dayCount"] = (today - start_date).days
    return topic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base, modifying base in place."""
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _empty_topic() -> dict:
    return {
        "meta": {
            "slug": "",
            "title": "",
            "question": "",
            "resolution": "",
            "created": "",
            "lastUpdated": "",
            "status": "ACTIVE",
            "dayCount": 0,
            "startDate": "",
            "classification": "ROUTINE",
        },
        "model": {
            "hypotheses": {},
            "expectedValue": 0,
            "expectedUnit": "",
            "posteriorHistory": [],
        },
        "subModels": {},
        "indicators": {
            "tiers": {
                "tier1_critical": [],
                "tier2_strong": [],
                "tier3_suggestive": [],
                "anti_indicators": [],
            }
        },
        "actorModel": {
            "description": "",
            "actors": {},
            "methodology": [
                "ACTIONS OVER RHETORIC",
                "TAG EVERYTHING",
                "DON'T FRONT-RUN",
                "SOCIALIZATION DETECTION",
            ],
        },
        "evidenceLog": [],
        "dataFeeds": {},
        "watchpoints": [],
        "governance": None,
    }


# ---------------------------------------------------------------------------
# Dashboard Snapshot Generation
# ---------------------------------------------------------------------------

def generate_dashboard(topic: dict, event_label: str = None) -> str:
    """
    Generate a standalone HTML dashboard snapshot with topic state baked in.
    Returns the saved file path.
    """
    slug = topic["meta"]["slug"]
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d-%H%M")
    label = event_label or "snapshot"
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)

    # Read the dashboard template
    template_path = Path(__file__).parent / "dashboard.html"
    template = template_path.read_text(encoding="utf-8")

    # Inject state directly into the HTML so it works standalone
    state_json = json.dumps(topic, ensure_ascii=False, indent=2)

    # Build governance report if available
    gov_json = "null"
    try:
        from governor import governance_report
        gov_json = json.dumps(governance_report(topic), ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Replace the dynamic loading with baked-in state
    injection = f"""<script>
// --- BAKED-IN STATE (snapshot: {timestamp} / {label}) ---
const SNAPSHOT_MODE = true;
const SNAPSHOT_STATE = {state_json};
const SNAPSHOT_GOV = {gov_json};
const SNAPSHOT_META = {{
  generated: "{now.isoformat(timespec='seconds')}",
  event: "{label}",
  slug: "{slug}"
}};
</script>"""

    # Replace the init script block to use baked-in data
    snapshot_init = """<script>
// --- Snapshot overrides ---
if (typeof SNAPSHOT_MODE !== 'undefined' && SNAPSHOT_MODE) {
  // Override fetch-based loading with baked-in state
  async function loadTopics() {
    const sel = document.getElementById('topicSelect');
    sel.innerHTML = '<option value="">Select topic...</option>';
    const opt = document.createElement('option');
    opt.value = SNAPSHOT_META.slug;
    opt.textContent = SNAPSHOT_STATE.meta.title;
    opt.selected = true;
    sel.appendChild(opt);
    switchTopic(SNAPSHOT_META.slug);
  }
  async function switchTopic(slug) {
    if (!slug) return;
    currentSlug = slug;
    state = SNAPSHOT_STATE;
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('mainGrid').style.display = '';
    document.getElementById('topicHeader').style.display = '';
    render();
    if (SNAPSHOT_GOV) { govData = SNAPSHOT_GOV; renderGovernance(); renderVoI(); }
  }
  // Add snapshot banner
  document.addEventListener('DOMContentLoaded', function() {
    const banner = document.createElement('div');
    banner.style.cssText = 'background:#1e293b;border-bottom:2px solid #f59e0b;padding:8px 24px;font-size:12px;color:#f59e0b;text-align:center;letter-spacing:1px;';
    banner.textContent = 'SNAPSHOT: ' + SNAPSHOT_META.event + ' — ' + SNAPSHOT_META.generated;
    document.body.insertBefore(banner, document.body.firstChild);
    loadTopics();
  });
}
</script>"""

    # Insert injection before </head> and snapshot_init before </body>
    html = template.replace("</head>", injection + "\n</head>")
    html = html.replace("</body>", snapshot_init + "\n</body>")

    # Save to dashboards/{slug}/{timestamp}-{label}.html
    out_dir = DASHBOARDS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{timestamp}-{safe_label}.html"
    out_path = out_dir / filename
    out_path.write_text(html, encoding="utf-8")

    return str(out_path)


def list_dashboards(slug: str = None) -> list[dict]:
    """List generated dashboard snapshots, optionally filtered by topic slug."""
    results = []
    if not DASHBOARDS_DIR.exists():
        return results
    search_dirs = [DASHBOARDS_DIR / slug] if slug else DASHBOARDS_DIR.iterdir()
    for d in search_dirs:
        if not d.is_dir():
            continue
        topic_slug = d.name
        for p in sorted(d.glob("*.html"), reverse=True):
            results.append({
                "slug": topic_slug,
                "filename": p.name,
                "path": str(p),
                "url": f"/dashboards/{topic_slug}/{p.name}",
            })
    return results


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python engine.py <command> [args]")
        print("Commands: list, show <slug>, brief <slug>, validate <slug>, govern <slug>")
        print("          scaffold <slug>, dashboard <slug> [event-label], dashboards [slug]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        for t in list_topics():
            status_icon = {"ACTIVE": "+", "RESOLVED": "=", "SUSPENDED": "-"}.get(
                t["status"], "?"
            )
            cls_color = {"ALERT": "!", "ELEVATED": "*", "ROUTINE": " "}.get(
                t["classification"], " "
            )
            gov_health = t.get("governanceHealth") or "?"
            print(f"  [{status_icon}]{cls_color} {t['slug']:20s} {t['title']:30s} [{gov_health}]")

    elif cmd == "govern" and len(sys.argv) > 2:
        from governor import governance_report
        topic = load_topic(sys.argv[2])
        update_day_count(topic)
        r = governance_report(topic)
        print(f"\n  {topic['meta']['title']} — Epistemic Health: {r['health']}")
        print(f"  R_t={r['rt']['rt']:.3f} ({r['rt']['regime']}) | "
              f"Entropy={r['entropy']:.3f}/{r['max_entropy']:.3f} ({r['uncertainty_ratio']:.0%})")
        if r["issues"]:
            for issue in r["issues"]:
                print(f"  ! {issue}")
        print()

    elif cmd == "show" and len(sys.argv) > 2:
        topic = load_topic(sys.argv[2])
        meta = topic["meta"]
        model = topic["model"]
        gov = topic.get("governance", {})
        print(f"\n  {meta['title']}")
        print(f"  {meta['question']}")
        print(f"  Status: {meta['status']} | Classification: {meta['classification']}"
              f" | Health: {gov.get('health', 'not computed')}")
        print(f"  Day {meta['dayCount']} | Last updated: {meta['lastUpdated']}")
        print()
        for k, h in model["hypotheses"].items():
            bar = "#" * int(h["posterior"] * 40)
            print(f"  {k} {h['label']:15s} {h['posterior']:5.1%} {bar}")
        ev = model.get("expectedValue", "?")
        eu = model.get("expectedUnit", "")
        print(f"\n  E[{eu}] = {ev}")
        print()
        summary = get_indicator_summary(topic)
        for tier, counts in summary.items():
            fired = counts["FIRED"] + counts["PARTIAL"]
            total = sum(counts.values())
            print(f"  {tier}: {fired}/{total} fired")

    elif cmd == "brief" and len(sys.argv) > 2:
        topic = load_topic(sys.argv[2])
        update_day_count(topic)
        print(generate_brief(topic))

    elif cmd == "validate" and len(sys.argv) > 2:
        try:
            load_topic(sys.argv[2])
            print(f"  {sys.argv[2]}: valid")
        except (ValueError, FileNotFoundError) as e:
            print(f"  {sys.argv[2]}: INVALID — {e}")

    elif cmd == "scaffold" and len(sys.argv) > 2:
        slug = sys.argv[2]
        existing = TOPICS_DIR / f"{slug}.json"
        if existing.exists():
            print(f"  Topic already exists: {slug}")
            sys.exit(1)
        path = scaffold_topic(slug)
        print(f"  Scaffolded: {path}")
        print(f"  Next steps:")
        print(f"    1. Edit topics/{slug}.json — fill in question, hypotheses, indicators")
        print(f"    2. python engine.py validate {slug}")
        print(f"    3. python engine.py govern {slug}")
        print(f"    4. python engine.py dashboard {slug} initial")
        print(f"  See PROTOCOL.md for full instructions.")

    elif cmd == "dashboard" and len(sys.argv) > 2:
        topic = load_topic(sys.argv[2])
        update_day_count(topic)
        label = sys.argv[3] if len(sys.argv) > 3 else None
        path = generate_dashboard(topic, event_label=label)
        print(f"  Dashboard saved: {path}")

    elif cmd == "dashboards":
        slug = sys.argv[2] if len(sys.argv) > 2 else None
        dashes = list_dashboards(slug)
        if not dashes:
            print("  No dashboards generated yet.")
        for d in dashes:
            print(f"  {d['slug']:20s} {d['filename']}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
