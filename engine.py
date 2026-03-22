"""
NRL-Alpha Omega — Generalized Epistemic Bayesian Estimator Engine

Core engine for loading, updating, and managing topic state files.
No external dependencies — Python stdlib only.
"""

import json
import os
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TOPICS_DIR = Path(__file__).parent / "topics"
BRIEFS_DIR = Path(__file__).parent / "briefs"


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
    """Write topic state back to disk."""
    slug = topic["meta"]["slug"]
    topic["meta"]["lastUpdated"] = _now_iso()
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
            results.append({
                "slug": meta.get("slug", p.stem),
                "title": meta.get("title", p.stem),
                "status": meta.get("status", "UNKNOWN"),
                "classification": meta.get("classification", "ROUTINE"),
                "question": meta.get("question", ""),
                "lastUpdated": meta.get("lastUpdated", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def create_topic(config: dict) -> dict:
    """Create a new topic from a config dict. Returns the initialized state."""
    template_path = TOPICS_DIR / "_template.json"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            topic = json.load(f)
    else:
        topic = _empty_topic()

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

    validate_topic(topic)
    save_topic(topic)
    return topic


# ---------------------------------------------------------------------------
# Bayesian Updates
# ---------------------------------------------------------------------------

def update_posteriors(topic: dict, new_posteriors: dict[str, float],
                      reason: str, evidence_refs: list[str] = None) -> dict:
    """
    Apply a posterior update. Enforces sum-to-1 and logs the change.

    new_posteriors: {"H1": 0.30, "H2": 0.40, ...}
    reason: why the update happened
    evidence_refs: list of evidence log timestamps/IDs supporting this update
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

    # Confidence gate: check for large shifts without high-tier indicator support
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
    topic["model"].setdefault("posteriorHistory", []).append(history_entry)

    return topic


def hold_posteriors(topic: dict, reason: str = "No new indicators") -> dict:
    """Record that posteriors were reviewed but not changed."""
    # Just log it — no history entry needed for holds
    add_evidence(topic, {
        "tag": "INTEL",
        "text": f"Posteriors HELD: {reason}",
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
    })
    return topic


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

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
    Add an evidence entry to the log.
    Required fields: tag, text
    Optional: provenance, source, posteriorImpact
    """
    if "tag" not in entry or "text" not in entry:
        raise ValueError("Evidence entry requires 'tag' and 'text'")

    full_entry = {
        "time": _now_iso(),
        "tag": entry["tag"],
        "text": entry["text"],
        "provenance": entry.get("provenance", "OBSERVED"),
        "source": entry.get("source"),
        "posteriorImpact": entry.get("posteriorImpact", "NONE"),
    }

    # Deduplication: don't add if identical text exists in last 10 entries
    recent = topic.get("evidenceLog", [])[-10:]
    for existing in recent:
        if existing.get("text") == full_entry["text"]:
            return topic  # Skip duplicate

    topic.setdefault("evidenceLog", []).append(full_entry)
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
    """Generate a structured briefing markdown string."""
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

def validate_topic(topic: dict) -> None:
    """Validate a topic state file. Raises ValueError on issues."""
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
    }


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python engine.py <command> [args]")
        print("Commands: list, show <slug>, brief <slug>, validate <slug>, govern <slug>")
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
            print(f"  [{status_icon}]{cls_color} {t['slug']:20s} {t['title']}")

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
        print(f"\n  {meta['title']}")
        print(f"  {meta['question']}")
        print(f"  Status: {meta['status']} | Classification: {meta['classification']}")
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

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
