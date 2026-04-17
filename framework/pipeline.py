"""
NROL-AO Evidence Pipeline — the glue function.

Connects all engine components into a single call:
  information in → posteriors out (through Bayes)

Every canvas interaction that carries signal calls process_evidence().
UI-only interactions (sorting, filtering, navigating) do not.

Pipeline:
  1. add_evidence()          — enrichment, contradiction check, dedup
  2. bayesian_update()       — mechanical Bayes with attenuated likelihoods
  3. snapshot_posteriors()    — record for Brier scoring
  4. auto_calibrate()        — resolve claims, update source trust
  5. ingest_from_topic()     — feed into cross-topic source DB
  6. check_expired_hypotheses() — partial Brier if needed
  7. governance_report()     — full epistemic health
  8. save_topic()            — embed governance snapshot
  9. propagate_alert()       — downstream dependency check
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Ensure repo root is on path
_REPO = str(Path(__file__).parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from engine import (
    load_topic, add_evidence, save_topic,
    fire_indicator, suggest_likelihoods, bayesian_update,
    triage_headline,
)
from governor import governance_report, check_update_proposal
from framework.scoring import snapshot_posteriors, check_expired_hypotheses
from framework.source_ledger import auto_calibrate, scan_for_resolutions
from framework.dependencies import propagate_alert

# Try to load source DB — not fatal if missing
_SOURCE_DB_PATH = Path(_REPO) / "sources" / "source_db.json"

def _load_source_db():
    try:
        from framework.source_db import load_db
        return load_db()
    except Exception:
        return None

def _save_source_db(db):
    try:
        from framework.source_db import save_db
        save_db(db)
    except Exception:
        pass

def _ingest_source_db(topic):
    """Feed evidence into cross-topic source database."""
    try:
        from framework.source_db import ingest_from_topic
        db = _load_source_db()
        if db:
            db = ingest_from_topic(db, topic)
            _save_source_db(db)
    except Exception:
        pass


def process_evidence(
    slug: str,
    entry: dict,
    likelihoods: dict[str, float] = None,
    fired_indicator_id: str = None,
    reason: str = None,
) -> dict:
    """
    Full evidence pipeline: information in, posteriors out.

    Args:
        slug: topic slug (e.g. 'hormuz-closure')
        entry: evidence entry dict. Minimum: {tag, text, source}
               Optional: time, claimState, tags, note, informationChain
        likelihoods: P(E|H_i) for each hypothesis. If None and an indicator
                     fired, suggest_likelihoods() derives them. If None and
                     no indicator, the caller MUST supply them — this function
                     will raise ValueError rather than silently skip the
                     Bayesian update.
        fired_indicator_id: if an indicator's observable threshold was met,
                           pass its ID here. The indicator will be fired and
                           likelihoods derived from its pre-committed effect.
        reason: human-readable reason for the update. Auto-generated if None.

    Returns:
        dict with:
          topic: the updated topic dict
          evidence_id: the ID of the added evidence entry
          posteriors_before: dict of posteriors before update
          posteriors_after: dict of posteriors after update
          governance: governance report
          calibration: source trust calibration results
          downstream_alerts: list of stale dependency alerts
    """
    topic = load_topic(slug)
    result = {
        "slug": slug,
        "posteriors_before": {k: v["posterior"] for k, v in topic["model"]["hypotheses"].items()},
    }

    # 1. Add evidence with governor enrichment
    topic = add_evidence(topic, entry)
    evidence_id = topic["evidenceLog"][-1]["id"]
    result["evidence_id"] = evidence_id
    result["evidence_text"] = entry.get("text", "")
    result["url"] = entry.get("url")

    # 1b. Auto-resolve contradictions created by add_evidence as SUPERSEDED.
    # In active/conflict topics, temporal evolution (ceasefire → ceasefire collapsed)
    # triggers the noun-overlap contradiction detector. These are supersession, not
    # genuine contradictions. Resolve them so they don't block bayesian_update().
    try:
        from framework.contradictions import get_unresolved_contradictions, resolve_contradiction
        unresolved = get_unresolved_contradictions(topic)
        if unresolved:
            resolved_count = 0
            while unresolved:
                resolve_contradiction(topic, len(unresolved) - 1, "SUPERSEDED")
                unresolved = get_unresolved_contradictions(topic)
                resolved_count += 1
            result["contradictions_auto_resolved"] = resolved_count
    except ImportError:
        pass

    # 2. Fire indicator if specified
    if fired_indicator_id:
        topic = fire_indicator(
            topic,
            indicator_id=fired_indicator_id,
            note=entry.get("note") or entry["text"][:100],
        )

    # 3. Determine likelihoods
    if likelihoods is None and fired_indicator_id:
        # Derive from pre-committed indicator effects
        suggested = suggest_likelihoods(topic, [fired_indicator_id])
        likelihoods = suggested.get("likelihoods")

    if likelihoods is None:
        raise ValueError(
            f"No likelihoods supplied and no indicator fired. "
            f"The Bayesian update requires explicit likelihoods: "
            f"P(E|H_i) for each hypothesis. Evidence was logged as {evidence_id} "
            f"but posteriors were NOT updated. Call process_evidence() again "
            f"with likelihoods={{H1: p1, H2: p2, ...}} to complete the update."
        )

    # 4. Bayesian update — mechanical, governor-gated
    update_reason = reason or f"Evidence {evidence_id}: {entry['text'][:80]}"
    if fired_indicator_id:
        update_reason = f"Indicator {fired_indicator_id} FIRED. {update_reason}"

    topic = bayesian_update(
        topic,
        likelihoods=likelihoods,
        reason=update_reason,
        evidence_refs=[evidence_id],
    )

    result["posteriors_after"] = {k: v["posterior"] for k, v in topic["model"]["hypotheses"].items()}

    # 5. Snapshot posteriors for Brier scoring
    trigger = f"pipeline:{evidence_id}"
    if fired_indicator_id:
        trigger = f"indicator:{fired_indicator_id}"
    snapshot_posteriors(topic, trigger=trigger)

    # 6. Source trust calibration — resolve any claims, update trust
    try:
        calibration_results = auto_calibrate(topic)
        result["calibration"] = calibration_results
    except Exception as e:
        result["calibration"] = {"error": str(e)}

    # 7. Ingest into cross-topic source DB
    _ingest_source_db(topic)

    # 8. Check for expired hypotheses (partial Brier)
    try:
        expired = check_expired_hypotheses(topic)
        if expired:
            result["expired_hypotheses"] = expired
    except Exception:
        pass

    # 9. Governance report
    gov = governance_report(topic)
    result["governance"] = {
        "health": gov["health"],
        "issues": gov["issues"],
        "rt_regime": gov["rt"]["regime"],
        "rt_value": gov["rt"]["rt"],
        "entropy": gov["entropy"],
        "uncertainty_ratio": gov["uncertainty_ratio"],
    }

    # 10. Save topic with governance snapshot
    save_topic(topic)

    # 11. Propagate dependency alerts
    try:
        alerts = propagate_alert(topic)
        result["downstream_alerts"] = alerts
    except Exception as e:
        result["downstream_alerts"] = [{"error": str(e)}]

    result["topic"] = topic
    return result


def process_headline(
    headline: str,
    source: str,
    likelihoods_by_slug: dict[str, dict[str, float]] = None,
) -> list[dict]:
    """
    Full triage-to-update pipeline for a headline.

    Triages the headline against all active topics, then runs
    process_evidence() for each matched topic.

    Args:
        headline: the news headline or description
        source: source name (e.g. 'Reuters', 'Al Jazeera')
        likelihoods_by_slug: optional dict of {slug: {H1: p, H2: p, ...}}
                            If not supplied, the caller must handle the
                            ValueError from process_evidence for non-indicator
                            matches.

    Returns:
        list of process_evidence() results, one per matched topic
    """
    triage = triage_headline(headline, source)
    results = []

    for match in triage.get("matches", []):
        if match["action"] in ("IGNORE",):
            continue

        slug = match["slug"]

        # Build evidence entry from triage
        entry = {
            "text": headline,
            "source": source,
            "tag": "EVENT",  # default; caller should override for RHETORIC etc.
            "tags": ["EVENT"],
            "note": match.get("explanation", ""),
        }

        # Determine if an indicator fired
        fired_id = None
        if match["action"] == "UPDATE_CYCLE" and match.get("matched_indicators"):
            fired_id = match["matched_indicators"][0].get("id")

        # Get likelihoods for this slug
        slug_likelihoods = None
        if likelihoods_by_slug and slug in likelihoods_by_slug:
            slug_likelihoods = likelihoods_by_slug[slug]

        try:
            result = process_evidence(
                slug=slug,
                entry=entry,
                likelihoods=slug_likelihoods,
                fired_indicator_id=fired_id,
            )
            results.append(result)
        except ValueError as e:
            # Likelihoods missing — evidence logged but posteriors not updated
            results.append({
                "slug": slug,
                "evidence_id": None,
                "error": str(e),
                "action": match["action"],
            })

    return results


# --- Activity log helper ---
def log_activity(result: dict, platform: str = "pipeline"):
    """Append a pipeline result to the canvas activity log."""
    log_path = Path(_REPO).parent / "canvas" / "activity-log.json"
    if not log_path.exists():
        return

    try:
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "POSTERIOR_UPDATE" if result.get("posteriors_after") else "EVIDENCE_LOGGED",
        "topic": result.get("slug", ""),
        "summary": result.get("evidence_text", "") or f"Evidence {result.get('evidence_id', '?')} logged.",
        "evidenceId": result.get("evidence_id", ""),
        "source": platform,
        "platform": platform,
        "url": result.get("url"),
        "route": "BAYESIAN_UPDATE",
        "posteriorChange": {
            "before": result.get("posteriors_before"),
            "after": result.get("posteriors_after"),
            "trigger": result.get("evidence_id", ""),
        } if result.get("posteriors_after") else None,
        "notes": f"Governance: {result.get('governance', {}).get('health', '?')}. "
                 f"R_t: {result.get('governance', {}).get('rt_regime', '?')}.",
    }
    log.setdefault("entries", []).append(entry)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# --- CLI entry point ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NROL-AO Evidence Pipeline")
    sub = parser.add_subparsers(dest="cmd")

    ev_parser = sub.add_parser("evidence", help="Process a single evidence entry")
    ev_parser.add_argument("slug", help="Topic slug")
    ev_parser.add_argument("--text", required=True, help="Evidence text")
    ev_parser.add_argument("--source", required=True, help="Source name")
    ev_parser.add_argument("--tag", default="EVENT", help="Evidence tag")
    ev_parser.add_argument("--indicator", help="Fired indicator ID")
    ev_parser.add_argument("--likelihoods", help="JSON dict of likelihoods")

    hl_parser = sub.add_parser("headline", help="Triage and process a headline")
    hl_parser.add_argument("headline", help="Headline text")
    hl_parser.add_argument("--source", default="unknown", help="Source name")

    args = parser.parse_args()

    if args.cmd == "evidence":
        entry = {"text": args.text, "source": args.source, "tag": args.tag, "tags": [args.tag]}
        lk = json.loads(args.likelihoods) if args.likelihoods else None
        result = process_evidence(args.slug, entry, likelihoods=lk, fired_indicator_id=args.indicator)
        log_activity(result)
        del result["topic"]  # Don't dump the whole topic
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "headline":
        results = process_headline(args.headline, args.source)
        for r in results:
            if "topic" in r:
                del r["topic"]
            log_activity(r)
        print(json.dumps(results, indent=2, default=str))

    else:
        parser.print_help()
