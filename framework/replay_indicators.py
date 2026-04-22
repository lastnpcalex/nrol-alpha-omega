"""
replay_indicators.py — Reconstruct posterior trajectory by replaying all
previously-fired indicators through the current LR system.

Why: reset_and_migrate returned topics to design priors so LR migration
could derive clean likelihoods, but it discarded the accumulated posterior
movement. Evidence log was preserved as record, but no evidence was
replayed through the new mechanics. This script walks each topic's
fired-indicator history chronologically and re-applies each one.

Approach:
  1. Reset posteriors to design priors (first posteriorHistory entry).
  2. Reset indicator state: status=NOT_FIRED, n_firings=0, firedDate=None.
  3. Truncate posteriorHistory to the single design-prior entry.
  4. Sort previously-fired indicators by firedDate (oldest first).
  5. For each, add a synthetic REPLAY evidence entry and call
     apply_indicator_effect. Restore original firedDate on success.
  6. Skip GovernanceError/ValueError, report for operator review.

Trade-offs:
  - All replay happens at today's date with today's deadline state. H1
    (<6 weeks) stays deadline-eliminated throughout, even though it was
    valid when early indicators fired. Approximate but defensible.
  - Only FIRED indicators replay. Ad-hoc likelihoods= updates without an
    indicator firing are lost — there's no audit trail for those.
  - evidenceLog is preserved as-is; REPLAY entries are added alongside.

Usage:
    python framework/replay_indicators.py --topic hormuz-closure
    python framework/replay_indicators.py --all
    python framework/replay_indicators.py --all --dry-run
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine import (
    load_topic, save_topic, apply_indicator_effect, extract_posteriors,
    GovernanceError, _now_iso, _add_evidence_raw,
)

TOPICS_DIR = Path(__file__).parent.parent / "topics"


def _collect_fired_indicators(topic):
    """Return [(firedDate, ind_id, tier_key, note, original_firedDate), ...]."""
    fired = []
    for tier_key, inds in topic["indicators"].get("tiers", {}).items():
        for ind in inds:
            if ind.get("status") == "FIRED" and ind.get("firedDate"):
                fired.append((ind["firedDate"], ind["id"], tier_key,
                              ind.get("note", ""), ind["firedDate"]))
    for ind in topic["indicators"].get("anti_indicators", []):
        if ind.get("status") == "FIRED" and ind.get("firedDate"):
            fired.append((ind["firedDate"], ind["id"], "anti_indicators",
                          ind.get("note", ""), ind["firedDate"]))
    fired.sort()
    return fired


def _reset_indicator_state(topic):
    """Reset all indicators to NOT_FIRED, n_firings=0, clear firedDate."""
    for tier_key, inds in topic["indicators"].get("tiers", {}).items():
        for ind in inds:
            ind["status"] = "NOT_FIRED"
            ind["n_firings"] = 0
            ind["firedDate"] = None
    for ind in topic["indicators"].get("anti_indicators", []):
        ind["status"] = "NOT_FIRED"
        ind["n_firings"] = 0
        ind["firedDate"] = None


def _restore_fired_date(topic, ind_id, original_firedDate):
    """After replay firing, restore the original firedDate for auditability."""
    for tier_key, inds in topic["indicators"].get("tiers", {}).items():
        for ind in inds:
            if ind["id"] == ind_id:
                ind["firedDate"] = original_firedDate
                return
    for ind in topic["indicators"].get("anti_indicators", []):
        if ind["id"] == ind_id:
            ind["firedDate"] = original_firedDate
            return


def replay_topic(slug, dry_run=False):
    """Reconstruct posterior trajectory for one topic. Returns a report dict."""
    t = load_topic(slug)

    # Guard: never replay RESOLVED or ARCHIVED topics — their posteriors are
    # locked to the winning hypothesis and must not be re-opened.
    status = t.get("meta", {}).get("status", "ACTIVE")
    if status in ("RESOLVED", "ARCHIVED"):
        return {"slug": slug, "status": f"skipped_{status.lower()}",
                "applied_count": 0, "skipped_count": 0,
                "final_posteriors": {k: round(h["posterior"], 4)
                                     for k, h in t["model"]["hypotheses"].items()}}

    h_keys = list(t["model"]["hypotheses"].keys())

    hist = t["model"].get("posteriorHistory", [])
    if not hist:
        return {"slug": slug, "status": "no_history", "applied": [], "skipped": []}

    design = extract_posteriors(hist[0], h_keys)
    if not design or abs(sum(design.values()) - 1.0) > 0.05:
        return {"slug": slug, "status": "malformed_design_priors",
                "applied": [], "skipped": []}

    fired = _collect_fired_indicators(t)

    if dry_run:
        return {"slug": slug, "status": "dry_run",
                "design_priors": design,
                "fired_count": len(fired),
                "fired_preview": [(f[0], f[1]) for f in fired[:10]]}

    # Reset state
    for k in h_keys:
        t["model"]["hypotheses"][k]["posterior"] = design.get(k, 1.0 / len(h_keys))
    _reset_indicator_state(t)
    t["model"]["posteriorHistory"] = [hist[0]]

    _add_evidence_raw(t, {
        "time": _now_iso(),
        "tag": "INTEL",
        "text": (f"REPLAY BEGIN: {len(fired)} previously-fired indicators will be "
                 "re-applied chronologically under current LR system. "
                 "Posteriors reset to design priors; indicator state cleared. "
                 "Evidence log preserved for audit."),
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
        "ledger": "DECISION",
        "claimState": "SUPPORTED",
        "effectiveWeight": 1.0,
    })
    save_topic(t)

    applied = []
    skipped = []

    for firedDate, ind_id, tier_key, note, original_fd in fired:
        t = load_topic(slug)  # reload — triggers deadline eliminations each round
        ev_id = f"ev_replay_{ind_id}"
        # Ensure unique — collisions if an indicator shows up twice
        existing_ids = {e.get("id") for e in t["evidenceLog"]}
        if ev_id in existing_ids:
            ev_id = f"{ev_id}_{firedDate[:10]}"

        t["evidenceLog"].append({
            "id": ev_id,
            "time": firedDate,
            "tag": "EVENT",
            "text": (f"REPLAY: indicator {ind_id} fires at historical firedDate "
                     f"{firedDate}. Original note: {note[:100]}"),
            "source": "replay",
            "claimState": "SUPPORTED",
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "FACT",
            "effectiveWeight": 1.0,
        })

        try:
            apply_indicator_effect(
                t, ind_id, evidence_refs=[firedDate],
                note=f"REPLAY: {note[:80]}" if note else "REPLAY",
            )
            _restore_fired_date(t, ind_id, original_fd)
            save_topic(t)
            applied.append((firedDate, ind_id))
        except (GovernanceError, ValueError) as e:
            skipped.append((firedDate, ind_id, str(e)[:100]))
            # Don't save — the failed firing shouldn't pollute the log
            continue

    # Final summary entry
    t = load_topic(slug)
    final_posteriors = {k: round(h["posterior"], 4)
                        for k, h in t["model"]["hypotheses"].items()}
    _add_evidence_raw(t, {
        "time": _now_iso(),
        "tag": "INTEL",
        "text": (f"REPLAY COMPLETE: {len(applied)} indicators applied, "
                 f"{len(skipped)} skipped. Final posteriors: {final_posteriors}. "
                 f"Indicator firedDates restored to originals for audit."),
        "provenance": "DERIVED",
        "posteriorImpact": "NONE",
        "ledger": "DECISION",
        "claimState": "SUPPORTED",
        "effectiveWeight": 1.0,
    })
    save_topic(t)

    return {
        "slug": slug, "status": "replayed",
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "final_posteriors": final_posteriors,
    }


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    slugs = ([args.topic] if args.topic else
             sorted(p.stem for p in TOPICS_DIR.glob("*.json")
                    if not p.stem.startswith("_")
                    and not p.stem.startswith("test-")
                    and p.stem != "CHANGE-ME"))

    rows = []
    for slug in slugs:
        try:
            rows.append(replay_topic(slug, dry_run=args.dry_run))
        except Exception as e:
            rows.append({"slug": slug, "status": f"ERROR: {e}"})

    print(f"{'Topic':<45} {'Status':<15} {'Applied':>8} {'Skipped':>8}")
    print("-" * 80)
    for r in rows:
        a = r.get("applied_count", r.get("fired_count", "-"))
        s = r.get("skipped_count", "-")
        print(f"{r['slug']:<45} {r['status']:<15} {str(a):>8} {str(s):>8}")

    total_applied = sum(r.get("applied_count", 0) for r in rows)
    total_skipped = sum(r.get("skipped_count", 0) for r in rows)
    print(f"\nTotal: {total_applied} indicators applied, {total_skipped} skipped")

    if any(r.get("skipped") for r in rows):
        print("\nSkipped indicators (first 20):")
        count = 0
        for r in rows:
            for firedDate, ind_id, err in r.get("skipped", []):
                if count >= 20:
                    break
                print(f"  [{r['slug']}] {firedDate[:10]} {ind_id}: {err}")
                count += 1


if __name__ == "__main__":
    main()
