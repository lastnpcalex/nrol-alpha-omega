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

from framework.lint_indicators import propose_indicators_lint
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

# Canvas directories for auto-sync (dashboard reads from these)
_REPO_ROOT = Path(__file__).parent.parent
CANVAS_TOPICS_DIR = _REPO_ROOT / "canvas" / "topics"
LOOM_TOPICS_DIR = Path(__file__).parent / "loom" / "topics"


# ---------------------------------------------------------------------------
# Governance Exception
# ---------------------------------------------------------------------------

class GovernanceError(Exception):
    """Raised when a governor check blocks an operation."""
    def __init__(self, message: str, failures: list = None, warnings: list = None):
        super().__init__(message)
        self.failures = failures or []
        self.warnings = warnings or []


class IndicatorAddNotAllowed(Exception):
    """
    Raised when add_indicator is called without an active cleanup session.
    Topic creation builds indicators directly via create_topic; mid-life
    schema additions must go through start_indicator_cleanup_session +
    commit_indicator_cleanup_session, which is the cyborgist cleanup
    workflow's enforcement point.
    """
    pass


class IndicatorShapeReviewRequired(Exception):
    """
    Raised by bayesian_update if an indicator is missing a shape review,
    or if its shape review is stale (schema hash mismatch). Enforces that
    all indicators pass the semantic resolution-disguise check via subagents.
    """
    pass


def _collect_indicator_ids(topic: dict) -> set:
    """Collect all indicator IDs from a topic across tiers and anti_indicators."""
    ids = set()
    inds = topic.get("indicators", {}) or {}
    for tier_key in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in inds.get("tiers", {}).get(tier_key, []) or []:
            if isinstance(ind, dict) and ind.get("id"):
                ids.add(ind["id"])
    for ind in inds.get("anti_indicators", []) or []:
        if isinstance(ind, dict) and ind.get("id"):
            ids.add(ind["id"])
    return ids


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
    _eliminate_expired_hypotheses(topic)
    return topic


def save_topic(topic: dict) -> None:
    """Write topic state back to disk with embedded governance snapshot.

    Indicator-add gate: if new indicator IDs appear in this save vs the
    on-disk version AND there's no active running_indicator_loop, the save
    is refused. This catches direct-dict-manipulation bypasses of
    add_indicator's gate. Topic creation (first save, no on-disk version)
    skips this check.
    """
    slug = topic["meta"]["slug"]
    topic["meta"]["lastUpdated"] = _now_iso()

    # --- Indicator-add gate ---

    # --- Indicator Shape Lint Gate ---
    try:
        from framework.lint_indicators import propose_indicators_lint
        topic_path = TOPICS_DIR / f"{slug}.json"
        all_inds = []
        for t in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
            all_inds.extend(topic.get("indicators", {}).get("tiers", {}).get(t, []))
        all_inds.extend(topic.get("indicators", {}).get("anti_indicators", []))
        
        inds_to_check = []
        if topic_path.exists():
            with open(topic_path, "r", encoding="utf-8") as _f:
                _disk = json.load(_f)
            _disk_ids = _collect_indicator_ids(_disk)
            inds_to_check = [i for i in all_inds if i.get("id") not in _disk_ids]
        else:
            inds_to_check = all_inds
            
        if inds_to_check:
            lint_res = propose_indicators_lint(topic, inds_to_check)
            if not lint_res["passed"]:
                blocker_msgs = [b["message"] for b in lint_res["blockers"]]
                raise ValueError(
                    f"save_topic({slug!r}) blocked by indicator shape lint. "
                    f"Blockers: {blocker_msgs}"
                )
    except ValueError:
        raise
    except Exception:
        pass

    # Compare in-memory indicator IDs to on-disk. If new ids appeared and
    # no cleanup session is active, refuse. Allows first-save (topic creation).
    try:
        topic_path = TOPICS_DIR / f"{slug}.json"
        if topic_path.exists():
            with open(topic_path, "r", encoding="utf-8") as _f:
                _disk = json.load(_f)
            _disk_ids = _collect_indicator_ids(_disk)
            _new_ids = _collect_indicator_ids(topic)
            _added = _new_ids - _disk_ids
            if _added:
                _is_active, _session_msg = _is_indicator_cleanup_session_active(topic)
                if not _is_active:
                    raise IndicatorAddNotAllowed(
                        f"save_topic({slug!r}) blocked: indicator IDs added without "
                        f"active cleanup session. Added: {sorted(_added)}. "
                        f"Session status: {_session_msg}. "
                        f"Open a session via start_indicator_cleanup_session before "
                        f"adding indicators, or use add_indicator() which enforces "
                        f"this gate at the function level."
                    )
    except IndicatorAddNotAllowed:
        raise
    except Exception:
        # If the comparison itself fails (corrupt JSON, etc.) don't block save —
        # the load failure is itself a separate problem.
        pass

    # Preserve operator-curated governance fields across the recompute below.
    # save_topic re-derives governance.{health, issues, rt, freshness, ...} from
    # the current topic state, but these fields are operator records that must
    # survive: reviewed_alerts (suppressions), and dependencyHistory tracked at
    # the governance level.
    _preserved_governance = {}
    _existing_gov = topic.get("governance") or {}
    for _k in (
        "reviewed_alerts",
        # Cleanup-workflow fields (Phase 1+) — must survive the governance
        # recompute below or sessions silently disappear and parked queues
        # vanish.
        "running_indicator_loop",
        "flagged_for_indicator_review",
        "indicator_cleanup_history",
        "indicator_bypasses",
    ):
        if _k in _existing_gov:
            _preserved_governance[_k] = _existing_gov[_k]

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

        # --- Epistemic improvement: cross-topic dependency staleness ---
        try:
            from framework.dependencies import check_stale_dependencies, propagate_alert
            stale_deps = check_stale_dependencies(topic)
            stale_list = [s for s in stale_deps if s.get("stale")]
            topic["governance"]["staleDependencies"] = len(stale_list)
            if stale_list:
                for sd in stale_list:
                    issues.append(
                        f"Stale dependency: {sd['upstream_slug']}.{sd['hypothesis']} "
                        f"assumed={sd['assumed']}, actual={sd['actual']}, "
                        f"drift={sd['drift']:.2%}"
                    )
                topic["governance"]["staleDependencyDetails"] = stale_list

            # Check if THIS topic's shift affects downstream topics
            downstream_alerts = propagate_alert(topic)
            if downstream_alerts:
                topic["governance"]["downstreamAlerts"] = downstream_alerts
                for alert in downstream_alerts:
                    issues.append(
                        f"Downstream alert: {alert['downstream_slug']} has "
                        f"{len(alert['stale_assumptions'])} stale assumption(s) "
                        f"from this topic"
                    )
        except ImportError:
            pass
        except Exception:
            pass  # Dependency checks must never prevent saving

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

    # Restore operator-curated governance fields preserved at the top of this
    # function. governance may not exist if the recompute block raised before
    # creating it, so use setdefault.
    if _preserved_governance:
        gov = topic.setdefault("governance", {})
        for _k, _v in _preserved_governance.items():
            gov[_k] = _v

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

    # Auto-sync to canvas and loom dashboards
    _sync_to_canvas(topic, slug)


def _sync_to_canvas(topic: dict, slug: str) -> None:
    """Copy topic JSON to canvas/topics/ and loom/topics/, regenerate manifest."""
    import shutil

    topic_json = json.dumps(topic, indent=2, ensure_ascii=False)

    for dest_dir in (CANVAS_TOPICS_DIR, LOOM_TOPICS_DIR):
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{slug}.json"
            dest.write_text(topic_json, encoding="utf-8")
        except Exception:
            pass  # Canvas sync must never prevent saving

    # Regenerate manifest from all topics in TOPICS_DIR
    _regenerate_manifest()


def _regenerate_manifest() -> None:
    """Rebuild canvas/topics/manifest.json from all active topics."""
    topics = list_topics()

    manifest = {
        "_generated": _now_iso(),
        "_docs": "Auto-generated by engine.save_topic(). Do not edit manually.",
        "topics": topics,
    }

    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    for dest_dir in (CANVAS_TOPICS_DIR, LOOM_TOPICS_DIR):
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
        except Exception:
            pass  # Manifest sync must never prevent saving


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

    # Map top-level convenience keys into their canonical nested locations.
    # Skill callers use a flat config dict; template uses nested structure.
    for _k in ("slug", "title", "question", "resolution", "classification",
               "status", "startDate", "created"):
        if _k in config:
            # Always override: template placeholders ("CHANGE-ME", "") must not
            # block a caller-supplied value from landing in meta.
            topic.setdefault("meta", {})[_k] = config[_k]
    # hypotheses and indicators live in model.hypotheses / topic.indicators
    if "hypotheses" in config and not isinstance(config["hypotheses"], dict):
        pass  # malformed — skip
    elif "hypotheses" in config:
        # Build hypotheses with posterior from prior (skill callers use "prior")
        merged = {}
        for hk, hv in config["hypotheses"].items():
            h = dict(hv)
            if "posterior" not in h and "prior" in h:
                h["posterior"] = h.pop("prior")
            merged[hk] = h
        topic.setdefault("model", {})["hypotheses"] = merged
    if "indicators" in config:
        cfg_inds = config["indicators"]
        # Skill callers pass flat {tier1_critical: [...], anti_indicators: [...]}.
        # Engine expects {tiers: {tier1_critical: [...]}, anti_indicators: [...]}.
        # If "tiers" key already present, assume nested structure and pass through.
        if isinstance(cfg_inds, dict) and "tiers" in cfg_inds:
            topic["indicators"] = cfg_inds
        elif isinstance(cfg_inds, dict):
            tier_keys = ("tier1_critical", "tier2_strong", "tier3_suggestive")
            tiers = {tk: cfg_inds.get(tk, []) for tk in tier_keys}
            topic["indicators"] = {
                "tiers": tiers,
                "anti_indicators": cfg_inds.get("anti_indicators", []),
            }

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

    # Auto-stamp resolution_deadline on any time-bounded hypotheses that don't have one.
    # stamp_topic loads/saves the on-disk JSON, so reload to bring deadlines back
    # into the returned in-memory topic.
    try:
        from framework.stamp_deadlines import stamp_topic
        slug = topic["meta"].get("slug", "")
        if slug:
            stamp_result = stamp_topic(slug, dry_run=False)
            if stamp_result.get("stamped"):
                topic = load_topic(slug)
    except ImportError:
        pass  # framework not available — operator must stamp manually

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


VALID_INDICATOR_TIERS = (
    "tier1_critical",
    "tier2_strong",
    "tier3_suggestive",
    "anti_indicators",
)


_INDICATOR_CLEANUP_SESSION_TTL_SECONDS = 60 * 60  # 1 hour


def _is_indicator_cleanup_session_active(topic: dict) -> tuple[bool, str]:
    """
    Returns (is_active, reason). Session is active if
    governance.running_indicator_loop is set AND not past TTL.
    Past-TTL sessions auto-expire (caller can clear them via
    commit/abort_indicator_cleanup_session).
    """
    from datetime import datetime, timezone, timedelta
    loop = topic.get("governance", {}).get("running_indicator_loop")
    if not loop:
        return False, "no active session"
    started = loop.get("started_at")
    if not started:
        return False, "session has no started_at"
    try:
        started_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False, "session has invalid started_at"
    age = datetime.now(timezone.utc) - started_dt
    ttl = timedelta(seconds=loop.get("ttl_seconds", _INDICATOR_CLEANUP_SESSION_TTL_SECONDS))
    if age > ttl:
        return False, f"session expired ({age.total_seconds():.0f}s old, ttl {ttl.total_seconds():.0f}s)"
    return True, f"active session {loop.get('session_id', '?')}"


def start_indicator_cleanup_session(slug: str, reason: str = "",
                                    ttl_seconds: int = _INDICATOR_CLEANUP_SESSION_TTL_SECONDS) -> dict:
    """
    Open an indicator-cleanup session on a topic. Sets
    governance.running_indicator_loop with session_id + started_at + ttl.
    While the session is active, add_indicator() accepts new indicators on
    this topic; outside a session, add_indicator() raises.

    Returns the topic with the session set. Caller must save_topic.

    Refuses if a session is already active and not expired.
    """
    import uuid
    topic = load_topic(slug)
    is_active, msg = _is_indicator_cleanup_session_active(topic)
    if is_active:
        raise ValueError(
            f"Cannot start cleanup session on {slug!r}: another is already active "
            f"({msg}). Run commit_indicator_cleanup_session or abort_indicator_cleanup_session first."
        )
    gov = topic.setdefault("governance", {})
    gov["running_indicator_loop"] = {
        "session_id": uuid.uuid4().hex[:12],
        "started_at": _now_iso(),
        "ttl_seconds": int(ttl_seconds),
        "reason": reason or "",
    }
    save_topic(topic)
    return topic


def commit_indicator_cleanup_session(slug: str, summary: str = "") -> dict:
    """
    Close the indicator-cleanup session by clearing
    governance.running_indicator_loop. Caller has already added indicators
    and applied any cleanup commits — this just clears the gate.
    """
    topic = load_topic(slug)
    gov = topic.setdefault("governance", {})
    closed = gov.pop("running_indicator_loop", None)
    if closed:
        history = gov.setdefault("indicator_cleanup_history", [])
        history.append({
            "session_id": closed.get("session_id"),
            "started_at": closed.get("started_at"),
            "closed_at": _now_iso(),
            "summary": summary or "",
            "outcome": "committed",
        })
    save_topic(topic)
    return topic


def commit_indicator_cleanup(
    slug: str,
    proposal_envelope: dict,
    canvas_receipt_path: str,
    *,
    receipt_max_age_seconds: int = 600,  # 10 min — receipt should be fresh
) -> dict:
    """
    Apply a cleanup proposal: add new indicators, fire indicators against
    matched parked evidence, clear those entries from the flagged queue,
    close the cleanup session.

    Hard gates (refuses unless ALL pass):
      1. Active cleanup session exists on the topic
      2. proposal_envelope.lint_result.passed is True (no blockers)
      3. canvas_receipt_path exists and was written within receipt_max_age_seconds
      4. proposal_envelope.session_id matches the current session

    proposal_envelope structure:
        {
          "session_id": "<active session id>",
          "topic_slug": "<slug>",
          "created_at": "<iso8601>",
          "proposed_indicators": [
              {"id", "tier", "desc", "posteriorEffect", "likelihoods", ...}
          ],
          "indicator_firings": [
              {"evidence_id": "ev_185", "indicator_id": "iran_reopen_proposal",
               "rationale": "match found via indicator_match"}
          ],
          "lint_result": {"passed": bool, "blockers": [], "warnings": []},
          "debate_envelope": {<red/blue team output>},
          "operator_notes": "..."
        }

    Returns:
        {
          "slug", "session_id",
          "indicators_added": [...],
          "firings": [...],
          "flagged_cleared": [...],
          "topic": <updated topic>,
        }

    Raises:
        IndicatorAddNotAllowed: no active session
        ValueError: lint failed, receipt invalid, envelope malformed
        FileNotFoundError: receipt file missing
    """
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    topic = load_topic(slug)

    # Gate 1: active session
    is_active, session_msg = _is_indicator_cleanup_session_active(topic)
    if not is_active:
        raise IndicatorAddNotAllowed(
            f"commit_indicator_cleanup({slug!r}) blocked: {session_msg}. "
            f"Open a session via start_indicator_cleanup_session."
        )
    current_session_id = topic["governance"]["running_indicator_loop"].get("session_id")

    # Gate 2: lint result
    lint = (proposal_envelope or {}).get("lint_result") or {}
    if not lint.get("passed"):
        blockers = lint.get("blockers", [])
        raise ValueError(
            f"commit_indicator_cleanup({slug!r}) blocked: lint did not pass. "
            f"{len(blockers)} blocker(s): "
            + "; ".join(b.get("message", "?")[:120] for b in blockers[:3])
        )

    # Gate 3: canvas receipt
    receipt_path = _Path(canvas_receipt_path)
    if not receipt_path.exists():
        raise FileNotFoundError(
            f"Canvas approval receipt not found at {canvas_receipt_path}. "
            f"The operator must click 'Approve & commit' on the canvas to "
            f"generate this receipt before commit can proceed."
        )
    try:
        with open(receipt_path, "r", encoding="utf-8") as f:
            receipt = _json.load(f)
    except Exception as e:
        raise ValueError(f"Cannot parse canvas receipt: {e}")
    receipt_ts = receipt.get("timestamp")
    if not receipt_ts:
        raise ValueError("Canvas receipt missing timestamp field.")
    try:
        receipt_dt = _dt.fromisoformat(str(receipt_ts).replace("Z", "+00:00"))
        if receipt_dt.tzinfo is None:
            receipt_dt = receipt_dt.replace(tzinfo=_tz.utc)
    except Exception as e:
        raise ValueError(f"Canvas receipt has invalid timestamp: {e}")
    age = _dt.now(_tz.utc) - receipt_dt
    if age > _td(seconds=receipt_max_age_seconds):
        raise ValueError(
            f"Canvas receipt is stale ({age.total_seconds():.0f}s old, "
            f"max {receipt_max_age_seconds}s). Operator must re-approve."
        )

    # Gate 4: session_id match
    envelope_session = (proposal_envelope or {}).get("session_id")
    if envelope_session and envelope_session != current_session_id:
        raise ValueError(
            f"Envelope session_id ({envelope_session}) does not match active "
            f"session ({current_session_id}). Stale envelope?"
        )

    # All gates pass. Apply changes.
    indicators_added = []
    firings = []
    flagged_cleared = []

    # Add new indicators
    for ind in proposal_envelope.get("proposed_indicators", []) or []:
        tier = ind.get("tier") or ind.get("_tier")
        if not tier:
            continue
        # add_indicator will load_topic and save_topic itself; relies on
        # the active session flag still being set (which it is — we won't
        # close until the end)
        indicator_payload = {
            k: v for k, v in ind.items()
            if k not in ("tier", "_tier")
        }
        result = add_indicator(
            slug=slug,
            tier=tier,
            indicator=indicator_payload,
            rationale=f"cleanup commit (session {current_session_id})",
        )
        indicators_added.append({
            "id": indicator_payload.get("id"),
            "tier": tier,
            "added": result.get("added"),
        })

    # Reload topic after add_indicator's saves
    topic = load_topic(slug)

    # Fire indicators on matched evidence. Each firing goes through fire_indicator
    # + bayesian_update via apply_indicator_effect (which already enforces the
    # provenance gate).
    for firing in proposal_envelope.get("indicator_firings", []) or []:
        ev_id = firing.get("evidence_id")
        ind_id = firing.get("indicator_id")
        rationale = firing.get("rationale", "cleanup-matched")
        if not ev_id or not ind_id:
            continue
        try:
            topic = apply_indicator_effect(
                topic,
                indicator_id=ind_id,
                evidence_refs=[ev_id],
                note=f"Cleanup match: {rationale}",
            )
            firings.append({"evidence_id": ev_id, "indicator_id": ind_id, "applied": True})
            # Clear from flagged queue
            flagged = topic.setdefault("governance", {}).get("flagged_for_indicator_review", [])
            if ev_id in flagged:
                flagged.remove(ev_id)
                flagged_cleared.append(ev_id)
        except Exception as e:
            firings.append({
                "evidence_id": ev_id, "indicator_id": ind_id,
                "applied": False, "error": str(e)[:200],
            })

    save_topic(topic)

    # Close the session
    summary = (
        f"Cleanup commit: added {len(indicators_added)} indicator(s), "
        f"fired {sum(1 for f in firings if f.get('applied'))} firing(s), "
        f"cleared {len(flagged_cleared)} flagged evidence id(s). "
        f"Lint passed with {len(lint.get('warnings', []))} warning(s)."
    )
    topic = commit_indicator_cleanup_session(slug, summary=summary)

    return {
        "slug": slug,
        "session_id": current_session_id,
        "indicators_added": indicators_added,
        "firings": firings,
        "flagged_cleared": flagged_cleared,
        "summary": summary,
        "topic": topic,
    }


def abort_indicator_cleanup_session(slug: str, reason: str = "") -> dict:
    """
    Cancel an active session without committing changes. Same as commit
    but tags the outcome as aborted. Used when operator decides not to
    apply the proposed indicators.
    """
    topic = load_topic(slug)
    gov = topic.setdefault("governance", {})
    closed = gov.pop("running_indicator_loop", None)
    if closed:
        history = gov.setdefault("indicator_cleanup_history", [])
        history.append({
            "session_id": closed.get("session_id"),
            "started_at": closed.get("started_at"),
            "closed_at": _now_iso(),
            "summary": reason or "operator aborted",
            "outcome": "aborted",
        })
    save_topic(topic)
    return topic


def add_indicator(
    slug: str,
    tier: str,
    indicator: dict,
    rationale: str = "",
) -> dict:
    """
    Add a new indicator to an existing topic.

    Gated: requires an active indicator-cleanup session
    (governance.running_indicator_loop set and not expired). Topic creation
    builds indicators directly via create_topic() and does not call this
    function — this function is only for mid-life schema additions, which
    must go through the cleanup workflow.

    Args:
        slug: topic slug
        tier: one of tier1_critical, tier2_strong, tier3_suggestive,
              anti_indicators
        indicator: dict with required keys {id, desc, posteriorEffect}.
                   Optional: status (default NOT_FIRED), firedDate, note,
                   likelihoods, lr_decay, causal_event_id.
        rationale: short note explaining why this indicator is being added.

    Returns:
        {slug, tier, indicator_id, added, tier_size_before, tier_size_after}

    Raises:
        IndicatorAddNotAllowed: no active cleanup session
        ValueError: invalid tier, missing required fields, duplicate id
        FileNotFoundError: topic doesn't exist
    """
    if tier not in VALID_INDICATOR_TIERS:
        raise ValueError(
            f"Invalid tier '{tier}'. Must be one of {VALID_INDICATOR_TIERS}."
        )

    required = {"id", "desc", "posteriorEffect"}
    missing = required - set(indicator.keys())
    if missing:
        raise ValueError(
            f"Indicator missing required fields: {sorted(missing)}. "
            f"Required: {sorted(required)}."
        )

    new_id = indicator["id"]
    if not isinstance(new_id, str) or not new_id.strip():
        raise ValueError("Indicator 'id' must be a non-empty string.")

    topic = load_topic(slug)

    # --- Cleanup session gate ---
    # add_indicator may only run inside an open indicator-cleanup session.
    # The session is opened by start_indicator_cleanup_session() (typically
    # via the cleanup-indicator-sweep skill) and closed by commit/abort.
    # New topics should use create_topic() which builds indicators inline
    # without calling this function.
    is_active, session_msg = _is_indicator_cleanup_session_active(topic)
    if not is_active:
        raise IndicatorAddNotAllowed(
            f"add_indicator on {slug!r} blocked: {session_msg}. "
            f"Open a cleanup session first via "
            f"engine.start_indicator_cleanup_session({slug!r}, reason=...). "
            f"This gate prevents schema changes from being applied outside "
            f"the audited cleanup workflow."
        )

    inds = topic.setdefault("indicators", {})
    tiers = inds.setdefault("tiers", {})
    for t in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        tiers.setdefault(t, [])
    inds.setdefault("anti_indicators", [])

    existing_ids = set()
    for t in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        existing_ids.update(i.get("id") for i in tiers.get(t, []))
    existing_ids.update(i.get("id") for i in inds.get("anti_indicators", []))
    if new_id in existing_ids:
        raise ValueError(
            f"Indicator id '{new_id}' already exists on topic '{slug}'. "
            "Use a unique id."
        )

    record = {
        "id": new_id,
        "desc": indicator["desc"],
        "posteriorEffect": indicator["posteriorEffect"],
        "status": indicator.get("status", "NOT_FIRED"),
        "firedDate": indicator.get("firedDate"),
        "note": indicator.get("note"),
        "shape": indicator.get("shape"),
        "causal_event_id": indicator.get("causal_event_id"),
        "ladder_group": indicator.get("ladder_group"),
        "ladder_step": indicator.get("ladder_step"),
        "likelihoods": indicator.get("likelihoods"),
        "lr_decay": indicator.get("lr_decay"),
    }
    # Remove None values so schema is clean
    record = {k: v for k, v in record.items() if v is not None}

    if tier == "anti_indicators":
        target = inds["anti_indicators"]
    else:
        target = tiers[tier]
        
    # --- Shape Lint Gate ---
    from framework.lint_indicators import propose_indicators_lint
    lint_res = propose_indicators_lint(topic, [record])
    if not lint_res["passed"]:
        blocker_msgs = [b["message"] for b in lint_res["blockers"]]
        raise ValueError(
            f"add_indicator({new_id!r}) blocked by indicator shape lint. "
            f"Blockers: {blocker_msgs}"
        )
        
    size_before = len(target)
    target.append(record)
    size_after = len(target)

    topic.setdefault("meta", {})["lastUpdated"] = _now_iso()
    topic.setdefault("evidenceLog", []).append({
        "time": _now_iso(),
        "tag": "INTEL",
        "text": (
            f"INDICATOR ADDED: {new_id} ({tier}). "
            f"posteriorEffect={record['posteriorEffect']}. "
            f"Rationale: {rationale or 'not supplied'}."
        ),
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
        "ledger": "DECISION",
        "claimState": "PROPOSED",
        "effectiveWeight": 0.5,
    })

    save_topic(topic)

    return {
        "slug": slug,
        "tier": tier,
        "indicator_id": new_id,
        "added": True,
        "tier_size_before": size_before,
        "tier_size_after": size_after,
    }


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Hypothesis deadline elimination
# ---------------------------------------------------------------------------

def _eliminate_expired_hypotheses(topic: dict) -> list[str]:
    """
    Check each hypothesis for a `resolution_deadline` field. If now is past
    the deadline, floor the hypothesis to 0.005 and redistribute its mass
    proportionally to non-expired survivors — in a single batched pass.

    Returns list of eliminated hypothesis keys (empty if none expired).

    Rules:
    - Hypotheses with no `resolution_deadline` field are never eliminated.
    - All expired hypotheses are batched before any redistribution.
    - If ALL hypotheses are expired, raises GovernanceError (topic design
      failure — operator must intervene).
    - Writes one DECISION-ledger evidence entry per batch (not per hypothesis).
    - Does nothing if topic status is RESOLVED.
    """
    topic_status = topic.get("meta", {}).get("status", "ACTIVE")
    if topic_status == "RESOLVED":
        return []

    hypotheses = topic["model"]["hypotheses"]
    now_str = _now_iso()[:10]  # YYYY-MM-DD

    # Batch: find all hypotheses past their deadline and not already floored
    expired = []
    for k, h in hypotheses.items():
        deadline = h.get("resolution_deadline")
        if deadline is None:
            continue
        if now_str > deadline[:10] and h["posterior"] > 0.005:
            expired.append(k)

    if not expired:
        return []

    # All-expired guard
    survivors = [k for k in hypotheses if k not in expired
                 and hypotheses[k].get("resolution_deadline") is None
                 or k not in expired
                 and hypotheses[k].get("resolution_deadline", "9999") >= now_str]
    # Recompute cleanly
    survivors = [
        k for k in hypotheses
        if k not in expired and (
            hypotheses[k].get("resolution_deadline") is None
            or hypotheses[k]["resolution_deadline"][:10] >= now_str
        )
    ]
    if not survivors:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"DEADLINE ELIMINATION HALTED: all hypotheses "
                     f"({', '.join(hypotheses.keys())}) are past their "
                     f"resolution_deadline. Topic requires operator review."),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "PROPOSED",
            "effectiveWeight": 1.0,
        })
        raise GovernanceError(
            "All hypotheses are past their resolution_deadline — topic design "
            "failure. Operator must manually resolve or extend deadlines.",
            failures=["all_hypotheses_expired"],
            warnings=[],
        )

    # Collect mass to redistribute
    freed_mass = sum(h["posterior"] - 0.005 for k, h in hypotheses.items()
                     if k in expired)
    survivor_total = sum(hypotheses[k]["posterior"] for k in survivors)

    # Floor expired hypotheses
    for k in expired:
        hypotheses[k]["posterior"] = 0.005

    # Redistribute proportionally to survivors
    if survivor_total > 0:
        for k in survivors:
            share = hypotheses[k]["posterior"] / survivor_total
            hypotheses[k]["posterior"] = round(
                hypotheses[k]["posterior"] + freed_mass * share, 4
            )

    # Renormalize to exactly 1.0
    total = sum(h["posterior"] for h in hypotheses.values())
    for k in hypotheses:
        hypotheses[k]["posterior"] = round(hypotheses[k]["posterior"] / total, 4)

    # Audit entry
    _add_evidence_raw(topic, {
        "time": _now_iso(),
        "tag": "INTEL",
        "text": (f"DEADLINE ELIMINATION: hypothesis "
                 f"{', '.join(expired)} past resolution_deadline "
                 f"({', '.join(hypotheses[k].get('resolution_deadline','?') for k in expired)}). "
                 f"Mass redistributed to {', '.join(survivors)}."),
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
        "ledger": "DECISION",
        "claimState": "SUPPORTED",
        "effectiveWeight": 1.0,
    })

    # Append to posteriorHistory with dedicated updateMethod
    hist = topic["model"].setdefault("posteriorHistory", [])
    priors = extract_posteriors(hist[-1], list(hypotheses.keys())) if hist else {}
    hist.append({
        "date": _now_iso()[:10],
        "posteriors": {k: h["posterior"] for k, h in hypotheses.items()},
        "priors": priors,
        "updateMethod": "deadline_elimination",
        "evidenceRefs": [],
        "eliminatedHypotheses": expired,
        "note": (f"Auto-eliminated {', '.join(expired)} — past resolution_deadline."),
    })

    return expired


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

    # Eliminate any hypotheses past their resolution_deadline before applying update
    _eliminate_expired_hypotheses(topic)

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

    # Epistemic floor/ceiling for ACTIVE topics — redistribution keeps bounds strict
    topic_status = topic.get("meta", {}).get("status", "ACTIVE")
    if topic_status != "RESOLVED":
        needs_clamp = any(v < 0.005 or v > 0.98 for v in merged.values())
        if needs_clamp:
            merged = clamp_posteriors_with_redistribution(merged)

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

    # Append to enriched history
    old_posteriors = {k: h["posterior"] for k, h in hypotheses.items()}
    # (old_posteriors captured BEFORE we apply merged — but merged is already applied above)
    # Reconstruct priors from the previous history entry
    hist = topic["model"].get("posteriorHistory", [])
    priors = extract_posteriors(hist[-1], list(hypotheses.keys())) if hist else {}

    history_entry = {
        "date": _now_iso()[:10],
        "posteriors": dict(merged),
        "priors": priors,
        "updateMethod": "operator_override",
        "evidenceRefs": evidence_refs or [],
        "note": reason,
    }

    # Turning point detection
    tp = detect_turning_point(topic, priors, merged)
    if tp:
        history_entry["turningPoint"] = tp

    # Red team challenge
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
        pass

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


def _bayes_pass(hypotheses: dict, adjusted_lrs: dict,
                eliminated_keys: set, active_keys: list) -> dict:
    """Single Bayes pass. Returns normalized posteriors dict."""
    if not eliminated_keys:
        unnorm = {k: h["posterior"] * adjusted_lrs[k] for k, h in hypotheses.items()}
    else:
        active_budget = 1.0 - 0.005 * len(eliminated_keys)
        active_prior_sum = sum(hypotheses[k]["posterior"] for k in active_keys)
        unnorm = {k: 0.005 for k in eliminated_keys}
        if active_prior_sum > 0:
            active_unnorm = {
                k: (hypotheses[k]["posterior"] / active_prior_sum) * adjusted_lrs[k]
                for k in active_keys
            }
            at = sum(active_unnorm.values())
            for k in active_keys:
                unnorm[k] = (active_unnorm[k] / at * active_budget) if at > 0 else active_budget / len(active_keys)
        else:
            for k in active_keys:
                unnorm[k] = active_budget / len(active_keys)
    total = sum(unnorm.values())
    if total == 0:
        raise ValueError("All unnormalized posteriors are zero")
    return {k: round(v / total, 4) for k, v in unnorm.items()}


def _normalize_lrs(lr_dict: dict) -> dict:
    """Normalize a likelihoods dict so max=1.0."""
    mx = max(lr_dict.values())
    return {k: round(v / mx, 6) for k, v in lr_dict.items()} if mx > 0 else lr_dict


def _attenuate_lrs(lrs: dict, weight: float) -> dict:
    """Apply mixture-model attenuation: w*LR + (1-w)*noise."""
    if weight >= 1.0:
        return dict(lrs)
    noise = sum(lrs.values()) / len(lrs)
    return {k: round(weight * v + (1.0 - weight) * noise, 6) for k, v in lrs.items()}


def _geo_mean_posteriors(p_lo: dict, p_hi: dict) -> dict:
    """Post-Bayes geometric mean, renormalized to sum=1."""
    import math as _math
    raw = {k: _math.sqrt(p_lo[k] * p_hi[k]) for k in p_lo}
    total = sum(raw.values())
    return {k: round(v / total, 4) for k, v in raw.items()} if total > 0 else p_lo


def bayesian_update(topic: dict, likelihoods: dict[str, float] = None,
                    reason: str = "", evidence_refs: list[str] = None,
                    operator_posteriors: dict[str, float] = None,
                    lr_range: dict = None,
                    lr_confidence: str = "MEDIUM",
                    lens: str = None,
                    is_replay: bool = False,
                    *,
                    indicator_id: str = None,
                    legacy_unstamped: bool = False) -> dict:
    """
    Mechanistic Bayesian posterior update from explicit likelihood ratios.

    Accepts either:
      likelihoods: point LR dict {H1: float, ...} — single pass
      lr_range: interval LR dict {H1: [lo, hi], ...} — dual pass, post-Bayes
                geometric mean stored as point estimate

    When lr_range is supplied the history entry gains posteriorRangeLo,
    posteriorRangeHi, sensitivityFlag, and dominantHypothesisStable.

    Provenance gate: indicator_id is REQUIRED. The indicator must exist
    on the topic and be in FIRED state. Anonymous / freeform updates are
    no longer accepted — they were the failure mode that pegged 17 active
    topics at clamp ceilings via context-anchored LR commitments. Evidence
    that doesn't match an existing indicator must be parked via
    pipeline.process_evidence (without fired_indicator_id) and resolved
    later through the indicator-cleanup workflow.

    Lens (required, no silent fallback):
      Pass lens= explicitly OR set topic.meta.lens via set_topic_lens().
      legacy_unstamped=True is a transition flag for retroactive
      processing only — new code should not pass it.
    """
    import math as _math

    # --- Provenance gate ---
    # bayesian_update requires indicator_id, period. The freeform path was
    # removed because it was systematically used to commit context-anchored
    # LRs across topic sweeps, leading to compound saturation. New evidence
    # without a matching indicator must be parked, not freeform-applied.
    if not indicator_id:
        raise ValueError(
            "bayesian_update requires indicator_id. The freeform path was "
            "removed to prevent context-anchored LR accumulation that pegged "
            "17 topics at clamp ceilings.\n"
            "If no existing indicator matches the evidence:\n"
            "  - park it via pipeline.process_evidence (no fired_indicator_id)\n"
            "  - run the indicator-cleanup workflow to author/match indicators\n"
            "  - then fire the indicator and apply its pre-committed LRs"
        )
    if True:
        # Indicator must exist on topic and be in FIRED state.
        # Search tier1/tier2/tier3 + anti_indicators.
        _found = None
        _inds = topic.get("indicators", {})
        for _tk in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
            for _i in _inds.get("tiers", {}).get(_tk, []):
                if _i.get("id") == indicator_id:
                    _found = _i
                    break
            if _found:
                break
        if not _found:
            for _i in _inds.get("anti_indicators", []):
                if _i.get("id") == indicator_id:
                    _found = _i
                    break
        if not _found:
            raise ValueError(
                f"indicator_id={indicator_id!r} not found on topic. "
                "Use add_indicator() to register it first."
            )
        if _found.get("status") != "FIRED":
            raise ValueError(
                f"Indicator {indicator_id!r} is not FIRED (status: "
                f"{_found.get('status')!r}). fire_indicator() must succeed "
                "before bayesian_update can apply its LRs."
            )

        # --- Indicator Shape Review Gate ---
        try:
            from framework.lint_indicator_shape import verify_shape_review
            review_ok, review_msg = verify_shape_review(topic, indicator_id)
            if not review_ok:
                raise IndicatorShapeReviewRequired(
                    f"bayesian_update({indicator_id!r}) blocked: {review_msg}. "
                    f"All indicators must pass semantic shape review (resolution-disguise check) "
                    f"before firing. Use the cleanup workflow to review it."
                )
        except ImportError:
            pass  # skip if not yet implemented

        # --- Causal de-correlation ---
        # If this indicator declares a causal_event_id and other indicators
        # with the same event_id have fired in the recent window, attenuate
        # the LRs toward 1.0 by factor 1/(K+1). Prevents correlated evidence
        # (multiple indicators on one underlying event) from being counted as
        # independent observations. K is computed BEFORE this fire is logged.
        _causal_event_id = _found.get("causal_event_id")
        if _causal_event_id:
            _causal_K = _causal_event_cluster_size(topic, _causal_event_id)
            if _causal_K > 0:
                if likelihoods is not None:
                    likelihoods = _attenuate_lrs_for_cluster(likelihoods, _causal_K)
                if lr_range is not None:
                    lr_range = {
                        k: [
                            1.0 + (lr_range[k][0] - 1.0) / (_causal_K + 1),
                            1.0 + (lr_range[k][1] - 1.0) / (_causal_K + 1),
                        ]
                        for k in lr_range
                    }

        # --- LR cap for indicator-bound calls ---
        # Indicator likelihoods authored with H_max=1.0 are common (operator
        # convention: "this hypothesis is most compatible with the evidence").
        # The downstream sanity gate rejects LR >= 0.99. Proportionally scale
        # to max=0.95 — this is mathematically a no-op on the posterior (Bayes
        # is invariant to LR scaling) but keeps us under the gate threshold.
        if likelihoods is not None:
            _lr_max = max(likelihoods.values()) if likelihoods else 1.0
            if _lr_max > 0.95:
                _scale = 0.95 / _lr_max
                likelihoods = {k: v * _scale for k, v in likelihoods.items()}
        if lr_range is not None:
            _hi_max = max(v[1] for v in lr_range.values())
            if _hi_max > 0.95:
                _scale = 0.95 / _hi_max
                lr_range = {k: [v[0] * _scale, v[1] * _scale] for k, v in lr_range.items()}

    _provenance = "indicator"

    # --- Lens resolution (no silent OPERATOR_JUDGMENT fallback) ---
    # Caller may pass `lens` explicitly; otherwise read from topic.meta.lens.
    # If neither is set, raise unless legacy_unstamped=True (migration only).
    _topic_lens = topic.get("meta", {}).get("lens")
    _resolved_lens = lens or _topic_lens
    if not _resolved_lens:
        if legacy_unstamped:
            _resolved_lens = "OPERATOR_JUDGMENT"
        else:
            raise ValueError(
                "lens is required. Pass lens='GREEN'|'AMBER'|'BLUE'|'RED'|"
                "'VIOLET'|'OCHRE'|'OPERATOR_JUDGMENT' explicitly, or set "
                "topic.meta.lens via engine.set_topic_lens(). "
                "Silent OPERATOR_JUDGMENT fallback was removed because it "
                "left 39/41 hormuz entries un-Brier-scoreable."
            )
    if _resolved_lens not in VALID_LENSES:
        raise ValueError(
            f"Unknown lens {_resolved_lens!r} on bayesian_update. "
            f"Valid: {sorted(VALID_LENSES)}."
        )
    _lens_set_at = topic.get("meta", {}).get("lensSetAt")
    _lens_source = "explicit" if lens else (
        "topic_meta" if _topic_lens else
        ("legacy_unstamped" if legacy_unstamped else "fallback")
    )

    if likelihoods is None and lr_range is None:
        raise ValueError("Either likelihoods or lr_range must be supplied.")

    # --- LR sanity gate ---
    # P(E|H) = 1.0 means "this evidence is observed with certainty if H is true,
    # and never under any other hypothesis." That's a logical claim, almost
    # never honest for an observation drawn from news. P(E|H) = 0 is its mirror.
    # bayesian_update is the news/observation path; resolution uses a different
    # function (update_posteriors) and is exempt by virtue of not calling here.
    _LR_MIN = 0.01
    _LR_MAX = 0.99
    def _check_lr_value(k, v, label):
        if not isinstance(v, (int, float)):
            raise ValueError(f"Likelihood for {k} must be numeric, got {type(v).__name__}")
        if v >= _LR_MAX or v <= _LR_MIN:
            raise ValueError(
                f"Dishonest likelihood: P(E|{k}) = {v} ({label}). "
                f"bayesian_update rejects values >= {_LR_MAX} or <= {_LR_MIN}. "
                f"P(E|H)=1.0 means the evidence is logically impossible under any "
                f"other hypothesis — almost never true for a news observation. "
                f"If you genuinely mean near-certain, use 0.95 / 0.05. "
                f"If the topic is resolving, use update_posteriors() not bayesian_update()."
            )
    if likelihoods is not None:
        for k, v in likelihoods.items():
            _check_lr_value(k, v, "point LR")
    if lr_range is not None:
        for k, pair in lr_range.items():
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                raise ValueError(f"lr_range[{k}] must be a 2-element [lo, hi].")
            _check_lr_value(k, pair[0], "lr_range lo")
            _check_lr_value(k, pair[1], "lr_range hi")

    hypotheses = topic["model"]["hypotheses"]
    h_keys = list(hypotheses.keys())

    # Resolve evidence weight (shared across all passes)
    evidence_weight, weight_detail = _resolve_evidence_weight(topic, evidence_refs or [])
    if evidence_weight < 0.3:
        _add_evidence_raw(topic, {
            "time": _now_iso(), "tag": "INTEL",
            "text": (f"WEIGHT ATTENUATION: likelihoods attenuated to "
                     f"{evidence_weight:.0%} via mixture model ({weight_detail})"),
            "provenance": "DERIVED", "posteriorImpact": "NONE",
            "ledger": "DECISION", "claimState": "PROPOSED", "effectiveWeight": 0.5,
        })

    # Eliminated hypotheses (deadline enforcement — shared)
    now_str = _now_iso()[:10]
    eliminated_keys = {
        k for k, h in hypotheses.items()
        if h.get("resolution_deadline") is not None
        and h["resolution_deadline"][:10] < now_str
    }
    active_keys = [k for k in hypotheses if k not in eliminated_keys]

    # --- Single-pass path (point likelihoods) ---
    if lr_range is None:
        for k in h_keys:
            if k not in likelihoods:
                raise ValueError(f"Missing likelihood for hypothesis {k}.")
        adj = _attenuate_lrs(likelihoods, evidence_weight)
        computed = _bayes_pass(hypotheses, adj, eliminated_keys, active_keys)
        computed_lo = computed_hi = None
        dominant_stable = True
        sensitivity_flag = False

    # --- Dual-pass path (LR ranges) ---
    else:
        for k in h_keys:
            if k not in lr_range:
                raise ValueError(f"Missing lr_range entry for hypothesis {k}.")
        lo_raw = {k: lr_range[k][0] for k in h_keys}
        hi_raw = {k: lr_range[k][1] for k in h_keys}
        lo_norm = _normalize_lrs(lo_raw)
        hi_norm = _normalize_lrs(hi_raw)
        adj_lo = _attenuate_lrs(lo_norm, evidence_weight)
        adj_hi = _attenuate_lrs(hi_norm, evidence_weight)
        computed_lo = _bayes_pass(hypotheses, adj_lo, eliminated_keys, active_keys)
        computed_hi = _bayes_pass(hypotheses, adj_hi, eliminated_keys, active_keys)
        # Point estimate: post-Bayes geometric mean
        computed = _geo_mean_posteriors(computed_lo, computed_hi)
        # Apply clamp to range bounds too
        topic_status = topic.get("meta", {}).get("status", "ACTIVE")
        if topic_status != "RESOLVED":
            if any(v < 0.005 or v > 0.98 for v in computed_lo.values()):
                computed_lo = clamp_posteriors_with_redistribution(computed_lo)
            if any(v < 0.005 or v > 0.98 for v in computed_hi.values()):
                computed_hi = clamp_posteriors_with_redistribution(computed_hi)
        dom_lo = max(active_keys or h_keys, key=lambda k: computed_lo[k])
        dom_hi = max(active_keys or h_keys, key=lambda k: computed_hi[k])
        dominant_stable = (dom_lo == dom_hi)
        width = max(computed_hi[k] - computed_lo[k] for k in h_keys)
        sensitivity_flag = (not dominant_stable) or (width > 0.20)
        # likelihoods for drift detection = geometric mean of lo/hi norms
        likelihoods = _normalize_lrs(
            {k: _math.sqrt(lo_norm[k] * hi_norm[k]) for k in h_keys}
        )
        adj = _attenuate_lrs(likelihoods, evidence_weight)

    adjusted_likelihoods = adj

    # Epistemic floor/ceiling for point estimate
    topic_status = topic.get("meta", {}).get("status", "ACTIVE")
    if topic_status != "RESOLVED":
        if any(v < 0.005 or v > 0.98 for v in computed.values()):
            computed = clamp_posteriors_with_redistribution(computed)
            _add_evidence_raw(topic, {
                "time": _now_iso(), "tag": "INTEL",
                "text": "EPISTEMIC CLAMP: point-estimate posteriors clamped to [0.005, 0.98].",
                "provenance": "DERIVED", "posteriorImpact": "NONE",
                "ledger": "DECISION", "claimState": "PROPOSED", "effectiveWeight": 0.5,
            })

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
    _sens_meta = {}
    if computed_lo is not None:
        _sens_meta = {
            "dominantHypothesisStable": dominant_stable,
            "maxRangeWidth": max(computed_hi[k] - computed_lo[k] for k in computed_hi),
            "lr_confidence": lr_confidence,
        }
    proposal_check = check_update_proposal(
        topic, computed, evidence_refs=evidence_refs, reason=reason,
        sensitivity_meta=_sens_meta, is_replay=is_replay,
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

    # Append to enriched history
    hist = topic["model"].get("posteriorHistory", [])
    priors = extract_posteriors(hist[-1], list(hypotheses.keys())) if hist else {}

    history_entry = {
        "date": _now_iso()[:10],
        "timestamp": _now_iso(),
        "posteriors": dict(computed),
        "priors": priors,
        "updateMethod": "bayesian_update_indicator",
        "provenance": _provenance,
        "indicatorId": indicator_id,
        "evidenceRefs": evidence_refs or [],
        "likelihoods": likelihoods,
        "note": reason,
        "dominantHypothesisStable": dominant_stable,
        "sensitivityFlag": sensitivity_flag,
        "lrSource": {
            "lens": _resolved_lens,
            "lensSetAt": _lens_set_at,
            "source": _lens_source,
        },
    }
    if legacy_unstamped:
        history_entry["legacyUnstamped"] = True
    if computed_lo is not None:
        history_entry["posteriorRangeLo"] = computed_lo
        history_entry["posteriorRangeHi"] = computed_hi
    if evidence_weight < 1.0:
        history_entry["adjustedLikelihoods"] = adjusted_likelihoods
        history_entry["evidenceWeight"] = evidence_weight
        history_entry["weightDetail"] = weight_detail

    # Turning point detection
    tp = detect_turning_point(topic, priors, computed, fired_indicators=evidence_refs)
    if tp:
        history_entry["turningPoint"] = tp

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


VALID_LENSES = {"GREEN", "AMBER", "BLUE", "RED", "VIOLET", "OCHRE", "OPERATOR_JUDGMENT"}


def reset_to_design_priors(topic: dict) -> dict:
    """
    Return an in-memory copy of `topic` with posteriors reset to design priors
    and posteriorHistory truncated to a single design-prior entry. Does NOT
    mutate the input and does NOT save to disk — used for replay scaffolding.

    Indicator state is reset (n_firings=0, status="NOT_FIRED", firedDate=None,
    firedDates=[]) so the replay can refire indicators chronologically.
    """
    import copy as _copy
    fresh = _copy.deepcopy(topic)
    history = fresh.get("model", {}).get("posteriorHistory", [])
    if not history:
        raise ValueError(
            f"Topic {fresh.get('meta', {}).get('slug', '?')} has no "
            f"posteriorHistory; cannot derive design priors."
        )
    # First entry holds the design priors. Use extract_posteriors to handle
    # the legacy {posteriors: {...}} vs flat {H1, H2, ...} formats.
    h_keys = list(fresh["model"]["hypotheses"].keys())
    design = extract_posteriors(history[0], h_keys)
    if not design:
        raise ValueError("Could not extract design priors from history[0].")
    for k, v in design.items():
        if k in fresh["model"]["hypotheses"]:
            fresh["model"]["hypotheses"][k]["posterior"] = v
    # Truncate history to the design-prior entry, refresh its timestamp.
    seed_entry = dict(history[0])
    seed_entry["note"] = "REPLAY SEED — design priors restored"
    seed_entry["timestamp"] = _now_iso()
    seed_entry["date"] = _now_iso()[:10]
    fresh["model"]["posteriorHistory"] = [seed_entry]
    # Reset indicator state
    tiers = fresh.get("indicators", {}).get("tiers", {}) or {}
    for tier_inds in tiers.values():
        for ind in tier_inds:
            ind["status"] = "NOT_FIRED"
            ind["n_firings"] = 0
            ind["firedDate"] = None
            ind["firedDates"] = []
    for ind in fresh.get("indicators", {}).get("anti_indicators", []) or []:
        ind["status"] = "NOT_FIRED"
        ind["n_firings"] = 0
        ind["firedDate"] = None
        ind["firedDates"] = []
    return fresh


def set_topic_lens(topic: dict, lens: str, reason: str = "") -> dict:
    """
    Set the active lens (persona) used to generate likelihood ratios for this
    topic. Persists in topic.meta.lens with lensSetAt timestamp. Previous lens
    (if any) is archived to topic.meta.lensHistory with its tenure.

    Per-update Brier attribution reads `lrSource.lens` from each posteriorHistory
    entry — the lens active at update time, not the topic's current lens.

    `lens` must be one of VALID_LENSES, or empty string / None to clear (which
    resolves to OPERATOR_JUDGMENT fallback at LR-generation time).
    """
    if lens and lens not in VALID_LENSES:
        raise ValueError(f"Unknown lens {lens!r}. Valid: {sorted(VALID_LENSES)}")
    meta = topic.setdefault("meta", {})
    prior_lens = meta.get("lens")
    prior_set_at = meta.get("lensSetAt")
    prior_reason = meta.get("lensSetReason", "")
    now = _now_iso()
    if prior_lens and prior_lens != lens:
        history = meta.setdefault("lensHistory", [])
        history.append({
            "lens": prior_lens,
            "setAt": prior_set_at,
            "removedAt": now,
            "reason": prior_reason,
            "removalReason": reason or "",
        })
    if lens:
        meta["lens"] = lens
        meta["lensSetAt"] = now
        meta["lensSetReason"] = reason or ""
    else:
        meta.pop("lens", None)
        meta.pop("lensSetAt", None)
        meta.pop("lensSetReason", None)
    return topic


def compute_alert_fingerprint(alert: dict) -> str | None:
    """
    Compute a content fingerprint for an alert, used for versioned suppression.

    For alerts whose substance can change between reports (e.g.
    none_impact_saturation, where new high-relevance evidence_ids accumulate),
    the fingerprint captures the suppressible content. If two alerts produce
    the same signature but different fingerprints, the operator's prior
    review did not cover the current content and the alert should re-fire.

    Returns None for alerts with no meaningful versioning — those suppress
    unconditionally on signature match.
    """
    if not isinstance(alert, dict):
        return None
    sig = alert.get("signature", "")
    details = alert.get("details") or {}
    if sig.startswith("none_impact_saturation:"):
        ids = sorted(str(x) for x in (details.get("evidence_ids") or []))
        if not ids:
            return None
        import hashlib
        return hashlib.sha1(",".join(ids).encode()).hexdigest()[:16]
    return None


def mark_alert_reviewed(topic: dict, signature: str, reason: str = "",
                        fingerprint: str | None = None) -> dict:
    """
    Suppress a governance alert by adding it to topic.governance.reviewed_alerts.

    Idempotent: re-adding the same signature replaces the prior entry rather
    than duplicating. The buildActionableAlerts logic (governor.py) skips any
    alert whose signature is in this list AND whose current fingerprint matches
    the suppressed fingerprint. If a suppression has no fingerprint, it
    suppresses unconditionally on signature match.

    fingerprint: pass the result of compute_alert_fingerprint(alert) when
    suppressing a versioned alert (e.g. none_impact_saturation). If the
    underlying content changes (new evidence_ids arrive), the alert will
    re-fire under the same signature with a new fingerprint, signaling the
    review is stale. Pass None for unconditional suppression.

    The suppression survives save_topic — see the preserved-fields block at
    the top of save_topic.
    """
    if not signature:
        raise ValueError("signature required")
    gov = topic.setdefault("governance", {})
    reviewed = gov.setdefault("reviewed_alerts", [])
    # Drop any existing entry with the same signature (re-review overrides)
    reviewed[:] = [r for r in reviewed if r.get("signature") != signature]
    entry = {
        "signature": signature,
        "timestamp": _now_iso(),
        "reason": reason or "",
    }
    if fingerprint is not None:
        entry["fingerprint"] = fingerprint
    reviewed.append(entry)
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

    # Find indicators by ID — across tiers AND anti_indicators
    all_indicators = {}
    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            all_indicators[ind["id"]] = (ind, tier_key)
    for ind in topic.get("indicators", {}).get("anti_indicators", []) or []:
        if isinstance(ind, dict) and ind.get("id"):
            all_indicators[ind["id"]] = (ind, "anti_indicators")

    # Modern pre-committed-LR path: if every fired indicator has an explicit
    # likelihoods dict, use those directly without parsing posteriorEffect
    # strings. Combine across indicators by multiplication (Bayesian
    # independence assumption — same as multiple bayesian_update calls).
    # Falls through to legacy pp-shift path if any indicator lacks likelihoods.
    if (fired_indicator_ids
            and not override_effects
            and all(
                ind_id in all_indicators
                and all_indicators[ind_id][0].get("likelihoods")
                for ind_id in fired_indicator_ids
            )):
        combined_lrs = {k: 1.0 for k in h_keys}
        breakdown = []
        for ind_id in fired_indicator_ids:
            ind, tier_key = all_indicators[ind_id]
            ind_lrs = ind["likelihoods"]
            for k in h_keys:
                combined_lrs[k] *= ind_lrs.get(k, 1.0)
            breakdown.append({
                "id": ind_id,
                "tier": tier_key,
                "source": "pre_committed_likelihoods",
                "likelihoods": ind_lrs,
            })
        # Cap each LR at 0.95 max (proportional scale) — same as bayesian_update
        # gate; mathematical no-op on posterior.
        lr_max = max(combined_lrs.values()) if combined_lrs else 1.0
        if lr_max > 0.95:
            scale = 0.95 / lr_max
            combined_lrs = {k: round(v * scale, 6) for k, v in combined_lrs.items()}
        # Compute target posteriors (forward Bayes)
        unnorm = {k: priors[k] * combined_lrs[k] for k in h_keys}
        total = sum(unnorm.values())
        target = ({k: round(v / total, 4) for k, v in unnorm.items()}
                  if total > 0 else priors)
        return {
            "suggested_likelihoods": combined_lrs,
            "target_posteriors": target,
            "current_priors": priors,
            "indicator_breakdown": breakdown,
            "combined_shifts_pp": None,  # not applicable on this path
            "unparseable": [],
            "warnings": [],
            "ready": True,
            "_path": "pre_committed_likelihoods",
        }

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
        for ind in topic.get("indicators", {}).get("anti_indicators", []) or []:
            if isinstance(ind, dict) and ind.get("id") == indicator_id:
                ind["status"] = "FIRED"
                ind["firedDate"] = _now_iso()
                if note:
                    ind["note"] = note
                found = True
                tier = tier or "anti_indicators"
                break

    if not found:
        raise ValueError(f"Indicator not found: {indicator_id}")

    # Update classification
    topic["meta"]["classification"] = compute_classification(topic)

    return topic


_CAUSAL_EVENT_WINDOW_DAYS = 5


def _causal_event_cluster_size(topic: dict, event_id: str,
                               window_days: int = _CAUSAL_EVENT_WINDOW_DAYS) -> int:
    """
    Count distinct indicator fires (within window) that share this causal_event_id.
    Returns 0 if no prior fires of the same event in the window.
    Used to attenuate compound LR effect when multiple indicators trace to the
    same underlying event (correlation correction).
    """
    if not event_id:
        return 0
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    history = topic.get("model", {}).get("posteriorHistory", []) or []
    # Build lookup: indicator_id -> causal_event_id
    ind_event = {}
    for tk in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in topic.get("indicators", {}).get("tiers", {}).get(tk, []):
            if ind.get("causal_event_id"):
                ind_event[ind["id"]] = ind["causal_event_id"]
    for ind in topic.get("indicators", {}).get("anti_indicators", []):
        if ind.get("causal_event_id"):
            ind_event[ind["id"]] = ind["causal_event_id"]
    count = 0
    seen_ids = set()
    for entry in history:
        ts = entry.get("timestamp") or entry.get("date")
        if not ts:
            continue
        try:
            t_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t_dt.tzinfo is None:
                t_dt = t_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if t_dt < cutoff:
            continue
        ind_id = entry.get("indicatorId")
        if not ind_id:
            continue
        if ind_event.get(ind_id) != event_id:
            continue
        if ind_id in seen_ids:
            continue
        seen_ids.add(ind_id)
        count += 1
    return count


def _attenuate_lrs_for_cluster(likelihoods: dict, cluster_prior_count: int) -> dict:
    """
    Attenuate likelihoods toward 1.0 based on how many same-event indicators
    have already fired. The (k+1)th fire of a cluster contributes
    log-LR / (k+1) — so after k+1 total fires the cumulative log-effect is
    the harmonic-mean log-LR rather than the sum. This is the "correlated
    evidence" correction: one underlying event should not produce k
    independent updates.

    cluster_prior_count = K (number of prior same-event fires already in
    history). The current fire is the (K+1)th.
    """
    if cluster_prior_count <= 0:
        return dict(likelihoods)
    factor = 1.0 / (cluster_prior_count + 1)
    return {k: 1.0 + (v - 1.0) * factor for k, v in likelihoods.items()}


def apply_indicator_effect(topic: dict, indicator_id: str,
                           evidence_refs: list[str],
                           note: str = "") -> dict:
    """
    Fire an indicator and apply its posterior effect via Bayesian update.

    Handles three paths in priority order:
      1. resolution_class indicators: apply target_posteriors directly (once only)
      2. schemaVersion 2 indicators with 'likelihoods' dict: LR path with decay
      3. Legacy indicators with 'posteriorEffect' string: pp-shift fallback

    LR decay: LR_effective = LR_base * lr_decay^n_firings
    Phantom precision guard: max(raw_lrs)/min(raw_lrs) > 20 blocks the update.

    Causal de-correlation (NEW): if the indicator declares a causal_event_id
    and other indicators with the same event_id have fired in the last
    {_CAUSAL_EVENT_WINDOW_DAYS} days, the LRs are attenuated toward 1.0 by
    factor 1/(K+1) where K = prior same-event fires in window. This prevents
    correlated evidence (multiple indicators on one underlying event) from
    being counted as independent observations.
    """
    # --- Find indicator ---
    ind = None
    tier_key = None
    for tk, indicators in topic["indicators"]["tiers"].items():
        for i in indicators:
            if i["id"] == indicator_id:
                ind = i
                tier_key = tk
                break
        if ind is not None:
            break

    if ind is None:
        raise ValueError(f"Indicator '{indicator_id}' not found in topic")
    # Note: causal_event_id de-correlation lives in bayesian_update where
    # the pipeline actually applies updates. apply_indicator_effect just
    # passes indicator_id forward.

    hypotheses = topic["model"]["hypotheses"]
    h_keys = list(hypotheses.keys())
    priors = {k: h["posterior"] for k, h in hypotheses.items()}

    # --- Path 1: resolution_class ---
    if ind.get("resolution_class", False):
        if ind.get("n_firings", 0) > 0:
            raise GovernanceError(
                f"Resolution-class indicator '{indicator_id}' has already fired. "
                "Use reset_resolution_class() to re-enable with operator confirmation.",
                failures=["resolution_class_already_fired"],
                warnings=[],
            )
        target = ind.get("target_posteriors", {})
        if not target:
            raise ValueError(
                f"Resolution-class indicator '{indicator_id}' missing 'target_posteriors'"
            )
        missing = [k for k in h_keys if k not in target]
        if missing:
            raise ValueError(
                f"Resolution-class target_posteriors missing keys: {missing}"
            )
        ind["n_firings"] = ind.get("n_firings", 0) + 1
        ind["status"] = "FIRED"
        ind["firedDate"] = _now_iso()
        if note:
            ind["note"] = note
        topic["meta"]["classification"] = compute_classification(topic)
        return update_posteriors(
            topic, target,
            reason=f"Resolution-class indicator {indicator_id} fired: {note}",
            evidence_refs=evidence_refs,
        )

    # --- Path 2: schemaVersion 2 (point likelihoods OR lr_range) ---
    if "likelihoods" in ind or "lr_range" in ind:
        import math as _math
        n_firings = ind.get("n_firings", 0)
        tier_decay_defaults = {"tier1": 0.70, "tier2": 0.65, "tier3": 0.50}
        lr_decay = ind.get("lr_decay", tier_decay_defaults.get(tier_key, 0.65))
        decay_factor = lr_decay ** n_firings

        # MAX_LOG_WIDTH: log-space range width cap (= log(20), same as phantom_precision)
        MAX_LOG_WIDTH = _math.log(20)

        if "lr_range" in ind:
            base_range = ind["lr_range"]
            # Apply decay to both bounds, clamp lo to 0.0001
            raw_range = {
                k: [max(0.0001, base_range[k][0] * decay_factor),
                    max(0.0001, base_range[k][1] * decay_factor)]
                for k in h_keys
            }
            # Log-space explosion cap: shrink width toward geometric mean if too wide
            capped_range = {}
            for k in h_keys:
                lo, hi = raw_range[k]
                log_width = _math.log(hi / lo) if lo > 0 else MAX_LOG_WIDTH
                if log_width > MAX_LOG_WIDTH:
                    geo = _math.sqrt(lo * hi)
                    half = _math.exp(MAX_LOG_WIDTH / 2)
                    lo = geo / half
                    hi = geo * half
                capped_range[k] = [lo, hi]
            # Phantom precision on hi bounds
            hi_max = max(v[1] for v in capped_range.values())
            hi_min = min(v[1] for v in capped_range.values())
            if hi_min > 0 and hi_max / hi_min > 20:
                raise GovernanceError(
                    f"Indicator '{indicator_id}' hi-bound LR ratio {hi_max/hi_min:.1f} "
                    "exceeds 20 (phantom_precision).",
                    failures=["phantom_precision"], warnings=[],
                )
            ind["n_firings"] = n_firings + 1
            ind["status"] = "FIRED"
            ind["firedDate"] = _now_iso()
            if note:
                ind["note"] = note
            topic["meta"]["classification"] = compute_classification(topic)
            return bayesian_update(
                topic, lr_range=capped_range,
                lr_confidence=ind.get("lr_confidence", "MEDIUM"),
                reason=(f"Indicator {indicator_id} fired "
                        f"(firing #{n_firings + 1}, decay={decay_factor:.3f}): {note}"),
                evidence_refs=evidence_refs,
                indicator_id=indicator_id,
            )
        else:
            # Point likelihoods path
            base_lrs = ind["likelihoods"]
            raw_lrs = {k: max(0.0001, base_lrs.get(k, 1.0) * decay_factor) for k in h_keys}
            lr_min = min(raw_lrs.values())
            lr_max = max(raw_lrs.values())
            if lr_min > 0 and lr_max / lr_min > 20:
                raise GovernanceError(
                    f"Indicator '{indicator_id}' LR ratio {lr_max/lr_min:.1f} "
                    "exceeds 20 (phantom_precision).",
                    failures=["phantom_precision"], warnings=[],
                )
            # Normalize to max=0.95 (not 1.0) — proportional scaling preserves
            # the posterior, but max=1.0 trips bayesian_update's sanity gate at
            # >= 0.99. The 0.95 cap keeps us under the threshold while keeping
            # the most-favored hypothesis at full weight relative to others.
            scale = 0.95 / lr_max if lr_max > 0 else 1.0
            likelihoods = {k: round(v * scale, 6) for k, v in raw_lrs.items()}
            ind["n_firings"] = n_firings + 1
            ind["status"] = "FIRED"
            ind["firedDate"] = _now_iso()
            if note:
                ind["note"] = note
            topic["meta"]["classification"] = compute_classification(topic)
            return bayesian_update(
                topic, likelihoods=likelihoods,
                reason=(f"Indicator {indicator_id} fired "
                        f"(firing #{n_firings + 1}, decay={decay_factor:.3f}): {note}"),
                evidence_refs=evidence_refs,
                indicator_id=indicator_id,
            )

    # --- Path 3: legacy pp-shift fallback ---
    result = suggest_likelihoods(topic, [indicator_id])
    if not result["ready"]:
        raise ValueError(
            f"Could not parse posteriorEffect for indicator '{indicator_id}': "
            f"{result['unparseable']}"
        )
    ind["n_firings"] = ind.get("n_firings", 0) + 1
    ind["status"] = "FIRED"
    ind["firedDate"] = _now_iso()
    if note:
        ind["note"] = note
    topic["meta"]["classification"] = compute_classification(topic)

    return bayesian_update(
        topic, result["suggested_likelihoods"],
        reason=f"Indicator {indicator_id} fired (legacy pp path): {note}",
        evidence_refs=evidence_refs,
        indicator_id=indicator_id,
    )


def reset_resolution_class(topic: dict, indicator_id: str,
                           reason: str, confirmed: bool = False) -> dict:
    """
    Clear a misfired resolution-class indicator so it can fire again.

    Requires confirmed=True (operator must explicitly pass True — no default
    acceptance). Writes a DECISION-ledger evidence entry so the reset is
    permanently auditable.

    Use only when a resolution-class indicator fired on bad data (false positive).
    """
    if not confirmed:
        raise ValueError(
            "reset_resolution_class requires confirmed=True. "
            "Pass confirmed=True only after verifying the original firing was a false positive."
        )
    if not reason or len(reason.strip()) < 10:
        raise ValueError(
            "reset_resolution_class requires a substantive reason (>=10 chars)."
        )

    ind = None
    for tk, indicators in topic["indicators"]["tiers"].items():
        for i in indicators:
            if i["id"] == indicator_id:
                ind = i
                break
        if ind is not None:
            break

    if ind is None:
        raise ValueError(f"Indicator '{indicator_id}' not found in topic")

    if not ind.get("resolution_class", False):
        raise ValueError(
            f"Indicator '{indicator_id}' is not a resolution-class indicator. "
            "Only resolution_class indicators need this reset path."
        )

    prev_firings = ind.get("n_firings", 0)
    ind["n_firings"] = 0
    ind["status"] = "NOT_FIRED"
    ind["firedDate"] = None

    _add_evidence_raw(topic, {
        "time": _now_iso(),
        "tag": "INTEL",
        "text": (f"RESOLUTION-CLASS RESET: indicator '{indicator_id}' reset after "
                 f"{prev_firings} firing(s). Reason: {reason}"),
        "provenance": "OPERATOR",
        "posteriorImpact": "NONE",
        "ledger": "DECISION",
        "claimState": "SUPPORTED",
        "effectiveWeight": 1.0,
    })

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

    # Auto-assign sequential id if not provided
    evidence_log = topic.get("evidenceLog", [])
    if "id" in entry:
        ev_id = entry["id"]
    else:
        existing_ids = [e.get("id", "") for e in evidence_log]
        max_num = 0
        for eid in existing_ids:
            if eid.startswith("ev_"):
                try:
                    max_num = max(max_num, int(eid[3:]))
                except ValueError:
                    pass
        ev_id = f"ev_{max_num + 1:03d}"

    full_entry = {
        "id": ev_id,
        "time": entry.get("time") or _now_iso(),
        "tag": entry["tag"],
        "tags": entry.get("tags", [entry["tag"]]),
        "text": entry["text"],
        "provenance": entry.get("provenance", "OBSERVED"),
        "source": entry.get("source"),
        "posteriorImpact": entry.get("posteriorImpact", "NONE"),
    }

    # Carry over optional fields
    if entry.get("note"):
        full_entry["note"] = entry["note"]
    if entry.get("url"):
        full_entry["url"] = entry["url"]

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

    # Validate indicator lr_decay bounds (schemaVersion 2 fields)
    for tier_key, indicators in topic.get("indicators", {}).get("tiers", {}).items():
        for ind in indicators:
            lr_decay = ind.get("lr_decay")
            if lr_decay is not None:
                if not (0.0 < lr_decay <= 1.0):
                    raise ValueError(
                        f"Indicator '{ind.get('id', '?')}' has lr_decay={lr_decay}. "
                        "Must be in (0.0, 1.0] — values > 1.0 amplify on re-fire."
                    )

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

def clamp_posteriors_with_redistribution(
    posteriors: dict,
    floor: float = 0.005,
    ceiling: float = 0.98,
    max_iterations: int = 10,
) -> dict:
    """
    Clamp posteriors to [floor, ceiling] while preserving sum = 1.0.

    Naive clamp-then-renormalize can push ceiling-clamped values back above
    the ceiling because uniform renormalization scales every value up. This
    function redistributes the excess/deficit mass only across values that
    are NOT at a bound, preserving strict bound enforcement.

    Algorithm:
      1. Clamp all values to [floor, ceiling]
      2. Compute delta = 1.0 - sum(clamped)
      3. If |delta| < 1e-6, done
      4. Identify adjustable values:
         - delta > 0 (need to add mass): values NOT at ceiling
         - delta < 0 (need to remove mass): values NOT at floor
      5. Distribute delta proportionally to current mass of adjustable values
      6. Re-clamp and repeat (bounded iterations)

    Returns new clamped+normalized dict.
    """
    current = dict(posteriors)

    for iteration in range(max_iterations):
        # Clamp to bounds
        clamped = {k: max(floor, min(ceiling, v)) for k, v in current.items()}
        total = sum(clamped.values())
        delta = 1.0 - total

        if abs(delta) < 1e-6:
            return {k: round(v, 4) for k, v in clamped.items()}

        # Identify which values can be adjusted
        if delta > 0:
            # Need to add mass — only non-ceiling values can receive
            adjustable = {k: v for k, v in clamped.items() if v < ceiling - 1e-9}
        else:
            # Need to remove mass — only non-floor values can give
            adjustable = {k: v for k, v in clamped.items() if v > floor + 1e-9}

        if not adjustable:
            # No room to adjust — bounds are mutually incompatible with sum=1
            # (e.g. all at floor with n*floor > 1, or all at ceiling with n*ceiling < 1)
            return {k: round(v, 4) for k, v in clamped.items()}

        adj_total = sum(adjustable.values())
        if adj_total <= 0:
            # Edge case: adjustable values sum to zero, distribute equally
            per = delta / len(adjustable)
            current = dict(clamped)
            for k in adjustable:
                current[k] = clamped[k] + per
        else:
            # Distribute delta proportionally to current mass of adjustable values
            current = dict(clamped)
            for k, v in adjustable.items():
                share = v / adj_total
                current[k] = clamped[k] + delta * share

    # Final clamp in case of floating-point drift
    clamped = {k: max(floor, min(ceiling, v)) for k, v in current.items()}
    total = sum(clamped.values())
    if total > 0:
        clamped = {k: v / total for k, v in clamped.items()}
    return {k: round(v, 4) for k, v in clamped.items()}


def extract_posteriors(entry: dict, hypothesis_keys: list[str] = None) -> dict:
    """
    Extract posterior values from a posteriorHistory entry, handling both formats:
      Flat:   {"date": "...", "H1": 0.3, "H2": 0.4, "note": "..."}
      Nested: {"date": "...", "posteriors": {"H1": 0.3, "H2": 0.4}, "note": "..."}

    Returns {H1: float, H2: float, ...} or empty dict if nothing found.
    """
    # Try nested format first
    nested = entry.get("posteriors")
    if isinstance(nested, dict):
        if hypothesis_keys:
            return {k: nested.get(k, 0.0) for k in hypothesis_keys if k in nested}
        return {k: v for k, v in nested.items() if isinstance(v, (int, float))}

    # Flat format — extract keys that look like hypothesis IDs
    _META_KEYS = {"date", "note", "likelihoods", "adjustedLikelihoods", "evidenceWeight",
                  "weightDetail", "redTeam", "updateMethod", "evidenceRefs",
                  "firedIndicators", "turningPoint", "priors", "posteriors"}
    if hypothesis_keys:
        return {k: entry[k] for k in hypothesis_keys if k in entry and isinstance(entry[k], (int, float))}
    return {k: v for k, v in entry.items() if k not in _META_KEYS and isinstance(v, (int, float))}


def detect_turning_point(
    topic: dict,
    old_posteriors: dict,
    new_posteriors: dict,
    fired_indicators: list[str] = None,
) -> dict | None:
    """
    Detect if this update constitutes a structural turning point.

    Returns a turningPoint dict or None.
    Types: LEADING_CHANGED, THRESHOLD_CROSSED, BRANCH_DEATH, TIER1_FIRED, MAJOR_SHIFT
    """
    if not old_posteriors or not new_posteriors:
        return None

    old_leading = max(old_posteriors, key=old_posteriors.get) if old_posteriors else None
    new_leading = max(new_posteriors, key=new_posteriors.get) if new_posteriors else None

    # LEADING_CHANGED
    if old_leading and new_leading and old_leading != new_leading:
        return {
            "type": "LEADING_CHANGED",
            "from": old_leading,
            "to": new_leading,
            "fromProb": round(old_posteriors.get(old_leading, 0), 4),
            "toProb": round(new_posteriors.get(new_leading, 0), 4),
        }

    # TIER1_FIRED
    if fired_indicators:
        tiers = topic.get("indicators", {}).get("tiers", {})
        t1_ids = {ind["id"] for ind in tiers.get("tier1_critical", []) if isinstance(ind, dict)}
        t1_fired = [fid for fid in fired_indicators if fid in t1_ids]
        if t1_fired:
            return {"type": "TIER1_FIRED", "indicators": t1_fired}

    # BRANCH_DEATH — any hypothesis fell below epistemic floor
    _FLOOR = 0.005
    for k in new_posteriors:
        if old_posteriors.get(k, 1) > _FLOOR and new_posteriors[k] <= _FLOOR:
            return {"type": "BRANCH_DEATH", "hypothesis": k, "lastPosterior": round(new_posteriors[k], 4)}

    # THRESHOLD_CROSSED — any hypothesis crossed 0.50
    for k in new_posteriors:
        old_v = old_posteriors.get(k, 0)
        new_v = new_posteriors[k]
        if (old_v < 0.50 and new_v >= 0.50) or (old_v >= 0.50 and new_v < 0.50):
            return {"type": "THRESHOLD_CROSSED", "hypothesis": k, "from": round(old_v, 4), "to": round(new_v, 4)}

    # MAJOR_SHIFT — max shift > 15pp
    max_shift = max(abs(new_posteriors.get(k, 0) - old_posteriors.get(k, 0)) for k in new_posteriors)
    if max_shift > 0.15:
        return {"type": "MAJOR_SHIFT", "maxShift": round(max_shift, 4)}

    return None


def get_state_at(target_date: str, topic_loader=None) -> dict:
    """
    Reconstruct all topic posteriors at a given timestamp.

    Uses step function interpolation — posteriors hold until the next update.
    No linear interpolation, ever. Beliefs change discretely with evidence.

    Args:
        target_date: ISO8601 date or datetime string
        topic_loader: optional function(slug) -> dict

    Returns: {slug: {H1: float, H2: float, ...}} for all topics that
             existed at that date. Topics created after target_date are
             omitted.
    """
    if topic_loader is None:
        topic_loader = lambda s: load_topic(s)

    target = target_date[:10]  # YYYY-MM-DD
    result = {}

    if not TOPICS_DIR.exists():
        return result

    for path in TOPICS_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        slug = t.get("meta", {}).get("slug", path.stem)
        h_keys = list(t.get("model", {}).get("hypotheses", {}).keys())
        history = t.get("model", {}).get("posteriorHistory", [])

        if not history:
            continue

        # Check if topic existed at target date
        first_date = history[0].get("date", "")[:10]
        if first_date > target:
            continue  # topic didn't exist yet

        # Binary search for last entry on or before target date
        best = None
        for entry in history:
            entry_date = entry.get("date", "")[:10]
            if entry_date <= target:
                best = entry
            else:
                break  # history is chronological

        if best:
            posteriors = extract_posteriors(best, h_keys)
            if posteriors:
                result[slug] = posteriors

    return result


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


def triage_headline(headline: str, source: str = None) -> dict:
    """
    Triage a news headline against all active topics.

    Top-level engine function wrapping framework.triage. Returns
    indicator matches, pre-committed posterior effects, source trust,
    R_t status, dependency implications, and recommended action.
    """
    from framework.triage import triage
    return triage(headline, source=source)


def what_happens_next(topic: dict, topic_loader=None) -> dict:
    """
    Compute a structured "what happens next" outlook for a topic.

    Synthesizes:
    - Leading hypothesis and confidence level
    - Time horizon and urgency
    - Downstream implications via CPTs
    - Key unfired indicators (what would change the picture)
    - Uncertainty assessment

    Returns a dict suitable for canvas rendering. Pure computation —
    no LLM interpretation, no side effects.
    """
    import math as _math
    if topic_loader is None:
        topic_loader = lambda s: load_topic(s)

    meta = topic.get("meta", {})
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    h_keys = sorted(hypotheses.keys())
    posteriors = {k: hypotheses[k]["posterior"] for k in h_keys}
    status = meta.get("status", "ACTIVE")

    # --- Leading hypothesis ---
    leading_key = max(posteriors, key=posteriors.get)
    leading_prob = posteriors[leading_key]
    leading_label = hypotheses[leading_key].get("label", leading_key)

    # --- Confidence assessment ---
    entropy = -sum(p * _math.log2(p) if p > 0 else 0 for p in posteriors.values())
    max_entropy = _math.log2(len(posteriors)) if len(posteriors) > 1 else 1
    uncertainty_ratio = entropy / max_entropy if max_entropy > 0 else 0

    if uncertainty_ratio > 0.85:
        confidence = "TOO_UNCERTAIN"
        confidence_text = "The model hasn't discriminated between hypotheses. Multiple outcomes remain plausible."
    elif uncertainty_ratio > 0.65:
        confidence = "LEANING"
        confidence_text = f"Leaning toward {leading_key} ({leading_label}) at {leading_prob:.0%}, but alternatives remain live."
    elif uncertainty_ratio > 0.35:
        confidence = "PROBABLE"
        confidence_text = f"{leading_key} ({leading_label}) is the probable outcome at {leading_prob:.0%}."
    else:
        confidence = "STRONG"
        confidence_text = f"Strong convergence on {leading_key} ({leading_label}) at {leading_prob:.0%}."

    # --- Time horizon ---
    horizon = meta.get("horizon", "")
    day_count = meta.get("dayCount", 0)
    midpoint = hypotheses[leading_key].get("midpoint")
    midpoint_unit = hypotheses[leading_key].get("unit", "")
    time_note = None
    if midpoint and midpoint_unit:
        if "week" in midpoint_unit:
            elapsed_weeks = day_count / 7
            remaining = midpoint - elapsed_weeks
            if remaining > 0:
                time_note = f"Leading hypothesis midpoint: {midpoint} weeks. ~{remaining:.0f} weeks remain."
            else:
                time_note = f"Leading hypothesis midpoint ({midpoint} weeks) has passed — day {day_count} ({day_count/7:.0f} weeks elapsed)."
        elif "ft" in midpoint_unit or "percent" in midpoint_unit:
            time_note = f"Midpoint: {midpoint} {midpoint_unit}."

    # --- Unfired indicators (what would change things) ---
    change_triggers = []
    tiers = topic.get("indicators", {}).get("tiers", {})
    tier_priority = {"tier1_critical": 1, "tier2_strong": 2, "tier3_suggestive": 3, "anti_indicators": 4}
    for tier_name in sorted(tiers.keys(), key=lambda t: tier_priority.get(t, 5)):
        for ind in tiers.get(tier_name, []):
            if not isinstance(ind, dict):
                continue
            if ind.get("status") == "NOT_FIRED":
                change_triggers.append({
                    "id": ind["id"],
                    "tier": tier_name,
                    "desc": ind.get("desc", "")[:120],
                    "effect": ind.get("posteriorEffect", ""),
                })
            if len(change_triggers) >= 5:
                break
        if len(change_triggers) >= 5:
            break

    # --- Downstream implications via CPTs ---
    downstream_implications = []
    deps_downstream = []
    # Find topics that depend on this one
    if TOPICS_DIR.exists():
        slug = meta.get("slug", "")
        for path in TOPICS_DIR.glob("*.json"):
            if path.stem.startswith("_"):
                continue
            try:
                dt = json.loads(path.read_text(encoding="utf-8"))
                for dep in dt.get("dependencies", {}).get("upstream", []):
                    if dep.get("slug") == slug and dep.get("conditionals"):
                        # Compute what leading hypothesis implies
                        row = dep["conditionals"].get(leading_key, {})
                        dt_hyps = dt.get("model", {}).get("hypotheses", {})
                        implied = {}
                        for dk in dt_hyps:
                            v = row.get(dk)
                            if isinstance(v, (int, float)):
                                implied[dk] = v
                        if implied:
                            implied_leading = max(implied, key=implied.get)
                            downstream_implications.append({
                                "slug": dt["meta"].get("slug", path.stem),
                                "title": dt["meta"].get("title", path.stem),
                                "implied": implied,
                                "implied_leading": implied_leading,
                                "implied_leading_label": dt_hyps[implied_leading].get("label", implied_leading),
                                "implied_leading_prob": implied[implied_leading],
                                "narrative": row.get("narrative", ""),
                            })
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    # --- Hypothesis landscape (all hypotheses with status) ---
    landscape = []
    for k in h_keys:
        h = hypotheses[k]
        p = posteriors[k]
        status_label = "LEADING" if k == leading_key else "LIVE" if p > 0.05 else "TAIL" if p > 0.005 else "NEAR_ZERO"
        landscape.append({
            "key": k,
            "label": h.get("label", k),
            "posterior": p,
            "status": status_label,
            "midpoint": h.get("midpoint"),
            "unit": h.get("unit", ""),
        })

    return {
        "slug": meta.get("slug", ""),
        "title": meta.get("title", ""),
        "topic_status": status,
        "leading": {
            "key": leading_key,
            "label": leading_label,
            "posterior": leading_prob,
        },
        "confidence": confidence,
        "confidence_text": confidence_text,
        "uncertainty_ratio": round(uncertainty_ratio, 3),
        "time": {
            "day_count": day_count,
            "horizon": horizon,
            "note": time_note,
        },
        "landscape": landscape,
        "change_triggers": change_triggers,
        "downstream_implications": downstream_implications,
    }


def compute_model_flags(topic: dict) -> list[str]:
    """
    Compute epistemic honesty flags for a topic.

    Returns array of warning strings — not a score. Each flag represents
    a structural limitation of the model, not a quality judgment.
    """
    flags = []
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    h_keys = list(hypotheses.keys())

    # NO_CATCHALL — no "other/unprecedented" hypothesis
    labels = [h.get("label", "").lower() for h in hypotheses.values()]
    catchall_terms = ["other", "unprecedented", "novel", "none of the above", "catch-all", "unknown"]
    has_catchall = any(any(t in label for t in catchall_terms) for label in labels)
    if not has_catchall:
        flags.append("NO_CATCHALL")

    # NARROW_FRAMING — fewer than 3 hypotheses
    if len(h_keys) < 3:
        flags.append("NARROW_FRAMING")

    # HIGH_UNFIRED_INDICATORS — >3 tier-1/2 indicators unfired
    tiers = topic.get("indicators", {}).get("tiers", {})
    unfired_high = 0
    for tn in ("tier1_critical", "tier2_strong"):
        for ind in tiers.get(tn, []):
            if isinstance(ind, dict) and ind.get("status") == "NOT_FIRED":
                unfired_high += 1
    if unfired_high > 3:
        flags.append("HIGH_UNFIRED_INDICATORS")

    # SPARSE_GRAPH — no upstream or downstream edges
    deps = topic.get("dependencies", {})
    upstream = deps.get("upstream", [])
    # Check if anything depends on this topic
    slug = topic.get("meta", {}).get("slug", "")
    has_downstream = False
    if TOPICS_DIR.exists():
        for path in TOPICS_DIR.glob("*.json"):
            if path.stem.startswith("_") or path.stem == slug:
                continue
            try:
                t = json.loads(path.read_text(encoding="utf-8"))
                for d in t.get("dependencies", {}).get("upstream", []):
                    if d.get("slug") == slug:
                        has_downstream = True
                        break
            except (json.JSONDecodeError, OSError):
                continue
            if has_downstream:
                break
    if not upstream and not has_downstream:
        flags.append("SPARSE_GRAPH")

    return flags


def build_operator_model(topic_loader=None) -> dict:
    """
    Build the full operator model for the model.html visualization.

    Extends build_dependency_graph with what_happens_next per node,
    modelFlags, turning points, and a cross-topic timeline.
    """
    from framework.dependencies import build_dependency_graph

    if topic_loader is None:
        topic_loader = lambda s: load_topic(s)

    graph = build_dependency_graph(topic_loader)

    # Enrich nodes
    timeline_events = []
    for node in graph["nodes"]:
        slug = node["slug"]
        try:
            t = topic_loader(slug)
        except Exception:
            continue

        # what_happens_next
        try:
            whn = what_happens_next(t, topic_loader)
            node["whatHappensNext"] = whn
            node["confidence"] = whn["confidence"]
            node["leading"] = whn["leading"]
        except Exception:
            node["confidence"] = "UNKNOWN"
            node["leading"] = None

        # modelFlags
        node["modelFlags"] = compute_model_flags(t)

        # Turning points from posteriorHistory
        h_keys = list(t.get("model", {}).get("hypotheses", {}).keys())
        history = t.get("model", {}).get("posteriorHistory", [])
        turning_points = []
        for i, entry in enumerate(history):
            tp = entry.get("turningPoint")
            if tp:
                turning_points.append({
                    "date": entry.get("date", ""),
                    "slug": slug,
                    "title": node.get("title", slug),
                    **tp,
                })
        node["turningPoints"] = turning_points
        timeline_events.extend(turning_points)

        # Posteriors for pie chart
        node["posteriors"] = {k: t["model"]["hypotheses"][k]["posterior"]
                             for k in h_keys}

        # Last updated
        node["lastUpdated"] = t.get("meta", {}).get("lastUpdated", "")

    # Sort timeline
    timeline_events.sort(key=lambda e: e.get("date", ""))

    # Compute depth for each node (longest path from a root)
    depths = {}
    edge_map = {}  # to -> [from]
    for e in graph["edges"]:
        edge_map.setdefault(e["to"], []).append(e["from"])

    def get_depth(slug, visited=None):
        if visited is None:
            visited = set()
        if slug in depths:
            return depths[slug]
        if slug in visited:
            return 0  # cycle
        visited.add(slug)
        parents = edge_map.get(slug, [])
        if not parents:
            depths[slug] = 0
            return 0
        d = 1 + max(get_depth(p, visited) for p in parents)
        depths[slug] = d
        return d

    for node in graph["nodes"]:
        get_depth(node["slug"])
        node["depth"] = depths.get(node["slug"], 0)

    # System-level flags
    total_possible_edges = len(graph["nodes"]) * (len(graph["nodes"]) - 1) / 2
    edge_density = len(graph["edges"]) / total_possible_edges if total_possible_edges > 0 else 0
    system_flags = []
    if edge_density < 0.15:
        system_flags.append("SPARSE_OPERATOR_MODEL")

    graph["timeline"] = timeline_events
    graph["depths"] = depths
    graph["systemFlags"] = system_flags
    graph["edgeDensity"] = round(edge_density, 3)

    return graph


def get_overview() -> dict:
    """
    Cross-topic overview for the mirror dashboard.

    Returns all active topics with posteriors, governance health,
    R_t, stale dependencies, and dependency graph.
    """
    topics_data = []
    for t_info in list_topics():
        slug = t_info["slug"]
        try:
            topic = load_topic(slug)
        except (FileNotFoundError, ValueError):
            continue

        gov = topic.get("governance") or {}
        hypotheses = topic.get("model", {}).get("hypotheses", {})
        posteriors = {k: h["posterior"] for k, h in hypotheses.items()}

        topics_data.append({
            "slug": slug,
            "title": t_info["title"],
            "status": t_info["status"],
            "classification": t_info.get("classification", "ROUTINE"),
            "lastUpdated": t_info.get("lastUpdated", ""),
            "posteriors": posteriors,
            "expectedValue": topic.get("model", {}).get("expectedValue"),
            "expectedUnit": topic.get("model", {}).get("expectedUnit"),
            "health": gov.get("health"),
            "rt": gov.get("rt", {}),
            "staleDependencies": gov.get("staleDependencies", 0),
            "downstreamAlerts": gov.get("downstreamAlerts", []),
            "evidenceFreshness": gov.get("evidenceFreshness", {}),
        })

    # Build dependency graph
    dep_graph = None
    try:
        from framework.dependencies import build_dependency_graph
        dep_graph = build_dependency_graph()
    except (ImportError, Exception):
        pass

    return {
        "topics": topics_data,
        "dependency_graph": dep_graph,
        "timestamp": _now_iso(),
    }


def get_trajectories(slug: str = None) -> dict:
    """
    Get posterior trajectories for the mirror dashboard.

    Returns posteriorHistory for one or all topics, formatted for
    time-series charting.
    """
    trajectories = {}

    if slug:
        slugs = [slug]
    else:
        slugs = [t["slug"] for t in list_topics()
                 if t["status"] == "ACTIVE"]

    for s in slugs:
        try:
            topic = load_topic(s)
        except (FileNotFoundError, ValueError):
            continue

        history = topic.get("model", {}).get("posteriorHistory", [])
        hypotheses = topic.get("model", {}).get("hypotheses", {})
        h_labels = {k: h.get("label", k) for k, h in hypotheses.items()}

        trajectories[s] = {
            "title": topic.get("meta", {}).get("title", s),
            "hypothesis_labels": h_labels,
            "history": history,
        }

    return {
        "trajectories": trajectories,
        "timestamp": _now_iso(),
    }


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

    elif cmd == "triage":
        if len(sys.argv) < 3:
            print("Usage: python engine.py triage \"headline text\" [source]")
            sys.exit(1)
        headline = sys.argv[2]
        source = sys.argv[3] if len(sys.argv) > 3 else None
        result = triage_headline(headline, source)
        print(f"\n  TRIAGE: {result['summary']}")
        for m in result["matches"]:
            print(f"\n  [{m['action']}] {m['slug']} ({m['relevance']})")
            print(f"    {m['explanation']}")
            if m.get("pre_committed_effects"):
                for eff in m["pre_committed_effects"]:
                    print(f"    → {eff['indicator_id']}: {eff['posterior_effect']}")
            if m.get("dependency_implications"):
                for dep in m["dependency_implications"]:
                    print(f"    ↓ {dep['note']}")
        if not result["matches"]:
            print("  No active topics matched.")
        print()

    elif cmd == "overview":
        ov = get_overview()
        print(f"\n  OVERVIEW — {len(ov['topics'])} topics")
        print(f"  {'─' * 50}")
        for t in ov["topics"]:
            rt = t.get("rt", {})
            health = t.get("health", "?")
            stale = t.get("staleDependencies", 0)
            posteriors = " | ".join(f"{k}={v:.0%}" for k, v in t["posteriors"].items())
            stale_note = f" ⚠ {stale} stale deps" if stale else ""
            print(f"  [{health:8s}] {t['slug']:25s} {posteriors}{stale_note}")
        if ov.get("dependency_graph", {}).get("stale_edges"):
            print(f"\n  STALE DEPENDENCIES:")
            for edge in ov["dependency_graph"]["stale_edges"]:
                for h, d in edge["drift"].items():
                    print(f"    {edge['from']}.{h} → {edge['to']}: "
                          f"assumed={d['assumed']}, actual={d['actual']}, "
                          f"drift={d['drift']:.2%}")
        print()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
