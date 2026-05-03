"""
meta_health — compute structured health metrics across topics + framework.

Pure-Python: walks topic JSONs, the activity log, and the lens-calibration
file, returns a structured metrics dict. Does NOT dispatch subagents
(that's the skill's job) or write any files. Used by:

  - skills/meta-health.md (calls compute_full_health_report and feeds the
    output to red/blue subagents for adversarial review)
  - any test/CI tooling that wants to inspect framework state

Public entry points:
  compute_topic_metrics(topic) -> dict
  compute_system_metrics(topics_dir, activity_log_path) -> dict
  compute_full_health_report(...) -> dict
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict


REPO_ROOT = Path(__file__).parent.parent  # NROL-AO/temp-repo/
CANVAS_DIR = REPO_ROOT.parent / "canvas"
DEFAULT_TOPICS_DIR = REPO_ROOT / "topics"
DEFAULT_ACTIVITY_LOG = CANVAS_DIR / "activity-log.json"
DEFAULT_LENS_BRIER = CANVAS_DIR / "lens-brier.json"

# Slugs to skip — template/placeholder files, not real topics.
# Mirrors the convention in post_edit_check, replay_indicators,
# stamp_resolution_dates.
SKIP_SLUGS = {"CHANGE-ME", "_template"}


# ---------- date helpers ----------

def _parse_iso(ts):
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _days_since(ts):
    dt = _parse_iso(ts)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).days


# ---------- per-topic metrics ----------

def _collect_indicators(topic):
    out = []
    inds = topic.get("indicators", {}) or {}
    for tier in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in inds.get("tiers", {}).get(tier, []) or []:
            if isinstance(ind, dict):
                out.append((tier, ind))
    for ind in inds.get("anti_indicators", []) or []:
        if isinstance(ind, dict):
            out.append(("anti_indicators", ind))
    return out


def _max_lr_hypothesis(likelihoods):
    if not likelihoods:
        return None
    try:
        return max(likelihoods, key=lambda k: likelihoods[k])
    except Exception:
        return None


def compute_topic_metrics(topic: dict) -> dict:
    """
    Compute the structured per-topic metrics block.

    Returns a flat dict suitable for direct inclusion in the system report
    (one row per topic).
    """
    meta = topic.get("meta", {}) or {}
    slug = meta.get("slug", "?")
    status = meta.get("status", "ACTIVE")
    title = meta.get("title", slug)

    # Posteriors
    hyps = topic.get("model", {}).get("hypotheses", {}) or {}
    posts = {k: (h.get("posterior", 0.0) if isinstance(h, dict) else 0.0)
             for k, h in hyps.items()}
    max_h = max(posts, key=posts.get) if posts else None
    max_p = posts.get(max_h, 0.0) if max_h else 0.0
    saturated = max_p >= 0.85

    # Governance health (rebuild lightly — full recompute is in governance_report,
    # but we want an at-a-glance read without forcing the full machinery)
    gov = topic.get("governance", {}) or {}
    health = gov.get("health", "?")

    # Indicator schema completeness
    inds = _collect_indicators(topic)
    n_inds = len(inds)
    n_with_lrs = sum(1 for _, i in inds if i.get("likelihoods"))
    n_with_event_id = sum(1 for _, i in inds if i.get("causal_event_id"))
    n_fired = sum(1 for _, i in inds if i.get("status") == "FIRED")

    # Parking queue
    parked = gov.get("flagged_for_indicator_review") or []
    parked_count = len(parked) if isinstance(parked, list) else 0

    # Lens stamping rate on last 10 posteriorHistory entries
    history = topic.get("model", {}).get("posteriorHistory", []) or []
    recent = history[-10:] if history else []
    n_lens_stamped = 0
    n_lens_legacy = 0
    for entry in recent:
        if not isinstance(entry, dict):
            continue
        lrs = entry.get("lrSource") or {}
        lens = lrs.get("lens")
        if not lens:
            continue
        if lrs.get("legacy") is True or lrs.get("source") == "legacy_migration":
            n_lens_legacy += 1
        elif lens != "OPERATOR_JUDGMENT":
            n_lens_stamped += 1
    lens_stamping_rate = (n_lens_stamped / len(recent)) if recent else 0.0

    # Days since last indicator firing
    last_fire_date = None
    for _, i in inds:
        d = _parse_iso(i.get("firedDate"))
        if d and (last_fire_date is None or d > last_fire_date):
            last_fire_date = d
    days_since_last_firing = (
        (datetime.now(timezone.utc) - last_fire_date).days
        if last_fire_date else None
    )

    # Direction drift: last 3+ firings all favor same hypothesis?
    fired_indicators = [i for _, i in inds
                        if i.get("status") == "FIRED" and i.get("likelihoods")]
    fired_indicators.sort(key=lambda i: i.get("firedDate") or "")
    last_3_fires = fired_indicators[-3:]
    direction_drift = False
    if len(last_3_fires) >= 3:
        max_h_per_fire = [_max_lr_hypothesis(i.get("likelihoods")) for i in last_3_fires]
        if len(set(max_h_per_fire)) == 1 and max_h_per_fire[0]:
            direction_drift = True

    # Historical freeform debt
    legacy_freeform_debt = sum(
        1 for e in history
        if isinstance(e, dict)
        and e.get("updateMethod") in ("bayesian_update_legacy", "bayesian_update")
        and not e.get("indicatorId")
    )

    # Past-deadline hypotheses
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past_deadline = []
    for k, h in hyps.items():
        if isinstance(h, dict):
            d = h.get("resolution_deadline")
            if d and str(d)[:10] < now_str:
                past_deadline.append(k)

    # Cleanup session activity
    running_session = bool(gov.get("running_indicator_loop"))
    cleanup_history = gov.get("indicator_cleanup_history", []) or []
    n_cleanup_sessions = len(cleanup_history)

    return {
        "slug": slug,
        "title": title,
        "status": status,
        "posteriors": posts,
        "max_h": max_h,
        "max_p": round(max_p, 4),
        "saturated": saturated,
        "governance_health": health,
        "indicators": {
            "total": n_inds,
            "with_lrs": n_with_lrs,
            "with_event_id": n_with_event_id,
            "fired": n_fired,
            "lr_completeness_pct": round(n_with_lrs / n_inds * 100, 1) if n_inds else None,
        },
        "parked_count": parked_count,
        "lens_stamping_rate_recent": round(lens_stamping_rate, 3),
        "lens_legacy_recent": n_lens_legacy,
        "days_since_last_firing": days_since_last_firing,
        "direction_drift": direction_drift,
        "legacy_freeform_debt": legacy_freeform_debt,
        "past_deadline_hypotheses": past_deadline,
        "cleanup_session_active": running_session,
        "n_cleanup_sessions_total": n_cleanup_sessions,
    }


# ---------- system-level metrics ----------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def compute_system_metrics(
    topics_dir: Path = DEFAULT_TOPICS_DIR,
    activity_log_path: Path = DEFAULT_ACTIVITY_LOG,
    lens_brier_path: Path = DEFAULT_LENS_BRIER,
    *,
    activity_window_days: int = 30,
) -> dict:
    """Aggregate framework-level metrics."""
    # Load topics
    topic_files = sorted(p for p in Path(topics_dir).glob("*.json")
                         if not p.name.startswith("_")
                         and p.name != "manifest.json"
                         and p.stem not in SKIP_SLUGS)
    topic_metrics = []
    for path in topic_files:
        data = _read_json(path)
        if not data:
            continue
        topic_metrics.append(compute_topic_metrics(data))

    # Status distribution
    by_status = defaultdict(int)
    by_health = defaultdict(int)
    n_saturated = 0
    n_with_parked = 0
    n_with_legacy_debt = 0
    n_with_drift = 0
    n_with_active_session = 0
    for tm in topic_metrics:
        by_status[tm.get("status", "?")] += 1
        by_health[tm.get("governance_health", "?")] += 1
        if tm.get("saturated"):
            n_saturated += 1
        if tm.get("parked_count", 0) > 0:
            n_with_parked += 1
        if tm.get("legacy_freeform_debt", 0) > 0:
            n_with_legacy_debt += 1
        if tm.get("direction_drift"):
            n_with_drift += 1
        if tm.get("cleanup_session_active"):
            n_with_active_session += 1

    # Activity log scan (last activity_window_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=activity_window_days)
    activity = _read_json(activity_log_path) or {}
    entries = activity.get("entries", []) if isinstance(activity, dict) else (
        activity if isinstance(activity, list) else []
    )

    cleanup_started = 0
    cleanup_committed = 0
    cleanup_aborted = 0
    framework_edits_by_severity = defaultdict(int)
    bypass_attempts = 0
    governance_violations = 0

    for e in entries:
        if not isinstance(e, dict):
            continue
        ts = _parse_iso(e.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        action = e.get("action", "")

        if action.startswith("INDICATOR_CLEANUP") or "indicator_cleanup" in action.lower():
            if "START" in action.upper() or "BEGIN" in action.upper():
                cleanup_started += 1
            elif "COMMIT" in action.upper():
                cleanup_committed += 1
            elif "ABORT" in action.upper():
                cleanup_aborted += 1
        elif action == "BETA_CLEANUP_RECOMMIT":
            cleanup_committed += 1
        elif action == "BETA_CLEANUP_REVERT":
            cleanup_aborted += 1
        elif action == "FRAMEWORK_CODE_EDIT":
            sev = e.get("severity", "MEDIUM")
            framework_edits_by_severity[sev] += 1
        # Look in summary/notes for governance bypass signals
        summary = (e.get("summary") or "") + " " + (e.get("notes") or "")
        if "IndicatorAddNotAllowed" in summary or "blocked by governance" in summary.lower():
            governance_violations += 1
        if "bypass" in summary.lower():
            bypass_attempts += 1

    # Lens calibration coverage
    lens_brier = _read_json(lens_brier_path) or {}
    lens_cells = lens_brier.get("cells", {}) if isinstance(lens_brier, dict) else {}
    lens_coverage = []
    for key, cell in lens_cells.items():
        if isinstance(cell, dict):
            n = cell.get("n", 0)
            lens_coverage.append({
                "key": key,
                "lens": cell.get("lens"),
                "n": n,
                "brier": cell.get("brier"),
                "calibrated": n >= 5,
            })

    return {
        "topic_count": len(topic_metrics),
        "topics_by_status": dict(by_status),
        "topics_by_health": dict(by_health),
        "saturated_count": n_saturated,
        "with_parked_evidence_count": n_with_parked,
        "with_legacy_freeform_debt_count": n_with_legacy_debt,
        "with_direction_drift_count": n_with_drift,
        "active_cleanup_sessions": n_with_active_session,
        "cleanup_activity_window_days": activity_window_days,
        "cleanup_sessions_started": cleanup_started,
        "cleanup_sessions_committed": cleanup_committed,
        "cleanup_sessions_aborted": cleanup_aborted,
        "framework_code_edits_by_severity": dict(framework_edits_by_severity),
        "bypass_attempts": bypass_attempts,
        "governance_violations_logged": governance_violations,
        "lens_calibration_coverage": lens_coverage,
    }


def compute_full_health_report(
    topics_dir: Path = DEFAULT_TOPICS_DIR,
    activity_log_path: Path = DEFAULT_ACTIVITY_LOG,
    lens_brier_path: Path = DEFAULT_LENS_BRIER,
    *,
    activity_window_days: int = 30,
) -> dict:
    """
    Build the metrics portion of the full health report. The skill adds
    red/blue subagent output and the recommended_actions list before
    writing the report file.
    """
    topic_files = sorted(p for p in Path(topics_dir).glob("*.json")
                         if not p.name.startswith("_")
                         and p.name != "manifest.json"
                         and p.stem not in SKIP_SLUGS)
    topic_metrics = []
    for path in topic_files:
        data = _read_json(path)
        if not data:
            continue
        topic_metrics.append(compute_topic_metrics(data))

    system = compute_system_metrics(
        topics_dir, activity_log_path, lens_brier_path,
        activity_window_days=activity_window_days,
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "system": system,
            "topics": topic_metrics,
        },
        # Filled in by the skill after subagent dispatch:
        "red_team": None,
        "blue_team": None,
        "recommended_actions": None,
        "operator_notes": "",
    }
