#!/usr/bin/env python3
"""
beta_cleanup.py — Pre-resolution recommit for beta-test topics.

Walks a topic's posteriorHistory and computes the audited-only posterior
by replaying every entry EXCEPT those flagged lrSource.legacy=True (the
209 retroactively-stamped unstamped/anonymous entries from the migration
sweep). Reports the audited value alongside the topic's current "full"
posterior. With --commit, writes the audited posteriors to the topic and
appends a beta_cleanup_recommit history entry with reason + red-team.

Beta-only tool. Each beta topic gets at most one recommit per structural
reason. After all beta topics are cleaned, this script should be archived.

The engine itself does NOT expose this capability — it lives here so it
doesn't become a permanent posterior-tinkering escape hatch on future
topics created under the new gate.

Usage:
    python scripts/beta_cleanup.py <slug>                        # dry run
    python scripts/beta_cleanup.py <slug> -v                     # show trajectory
    python scripts/beta_cleanup.py <slug> --commit \\
        --reason "..." --redteam "..."                           # apply
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import load_topic, save_topic, clamp_posteriors_with_redistribution
from framework.pipeline import log_activity


def _would_be_rejected_by_new_gate(entry):
    """
    Identify entries that the post-gate engine would REJECT.

    The new bayesian_update gate accepts updates only with:
      - indicator_id (path A: indicator-bound)
      - freeform=True + redteam_summary (path B: operator judgment with red-team)

    Lens stamp alone is NOT sufficient — the new gate requires either an
    indicator binding OR a red-team. So lens-only-without-redteam entries
    (e.g. OCHRE entries that didn't attach a red-team) get rejected.

    Mechanical events (deadline_elimination, beta_cleanup_recommit) are
    structural updates, not LR-driven; replay them.

    This is the STRICT interpretation: "what would the system produce if
    the new gate had been in place from the start?"
    """
    method = entry.get("updateMethod", "")

    # Mechanical events: replay them
    if method.startswith("deadline_elimination"):
        return False
    if method == "beta_cleanup_recommit":
        return False

    # Path A: indicator-bound (post-gate or recovered via note)
    if entry.get("indicatorId"):
        return False
    if method == "bayesian_update_indicator":
        return False
    note = (entry.get("note") or "").lower()
    if "indicator " in note and (" fired" in note or " fires" in note):
        return False

    # Path B: freeform with red-team attached
    if method == "bayesian_update_freeform" and entry.get("redTeamSummary"):
        return False
    if entry.get("redTeamSummary"):
        return False

    # Anything else: would be rejected (no indicator, no red-team)
    if method.startswith("bayesian_update"):
        return True

    return False


def _replay_audited(topic):
    """
    Walk posteriorHistory and replay non-anonymous entries from initial priors.
    Returns (audited_posteriors, walked_trace, n_skipped, skipped_entries).
    """
    history = topic["model"]["posteriorHistory"]
    h_keys = list(topic["model"]["hypotheses"].keys())

    if not history:
        return None, [], 0, []

    first = history[0]
    # Schema split: some entries store posteriors as a nested dict, others
    # store H1/H2/... as top-level fields on the entry itself. Read both shapes.
    current = dict(first.get("posteriors") or {})
    if not current or not all(k in current for k in h_keys):
        # Try top-level H1/H2/... shape
        current = {k: first.get(k) for k in h_keys if first.get(k) is not None}
    if not current or not all(k in current for k in h_keys):
        return None, [], 0, []

    note_text = (first.get("note") or "").lower()
    is_clean_prior = "prior" in note_text or "initial" in note_text
    start_label = ("initial prior" if is_clean_prior else
                   "rebuild start state (history[0] not labeled 'Prior')")
    walked = [{"i": 0,
               "method": "prior" if is_clean_prior else "rebuild_start",
               "posteriors": dict(current),
               "lens": "-", "note": start_label}]
    skipped = []

    for i, entry in enumerate(history[1:], start=1):
        if _would_be_rejected_by_new_gate(entry):
            skipped.append({"i": i, "note": (entry.get("note") or "")[:90]})
            continue

        method = entry.get("updateMethod", "")
        likelihoods = entry.get("likelihoods")
        lrs = entry.get("lrSource") or {}

        if method.startswith("deadline_elimination"):
            eliminated = entry.get("eliminatedHypotheses", [])
            if not eliminated:
                continue
            current = _apply_deadline_elimination(current, eliminated, h_keys)
            walked.append({"i": i, "method": "deadline_elimination",
                           "posteriors": dict(current),
                           "lens": "-", "note": entry.get("note", "")[:80],
                           "eliminated": eliminated})

        elif likelihoods:
            current = _apply_bayes(current, likelihoods, h_keys)
            walked.append({"i": i, "method": method,
                           "posteriors": dict(current),
                           "lens": lrs.get("lens", "-"),
                           "note": entry.get("note", "")[:80]})
        # Skip pure-log entries (CLAMP, etc.) without a posterior payload

    return current, walked, len(skipped), skipped


def _apply_deadline_elimination(current, eliminated, h_keys, floor=0.005):
    """Floor the eliminated hypotheses; redistribute proportionally to survivors."""
    freed = sum(max(0, current[k] - floor) for k in eliminated if k in current)
    survivors = [k for k in h_keys if k not in eliminated]
    survivor_total = sum(current[k] for k in survivors)
    new = {}
    for k in h_keys:
        if k in eliminated:
            new[k] = floor
        else:
            if survivor_total > 0:
                share = current[k] / survivor_total
                new[k] = current[k] + freed * share
            else:
                new[k] = current[k]
    total = sum(new.values())
    return {k: round(v / total, 6) for k, v in new.items()}


def _apply_bayes(current, likelihoods, h_keys, floor=0.005, ceiling=0.98):
    """Apply LRs to current posteriors, normalize, clamp via engine helper."""
    unnorm = {k: current[k] * likelihoods.get(k, 1.0) for k in h_keys}
    total = sum(unnorm.values())
    if total <= 0:
        return dict(current)
    after = {k: v / total for k, v in unnorm.items()}
    if any(v < floor or v > ceiling for v in after.values()):
        after = clamp_posteriors_with_redistribution(after, floor=floor, ceiling=ceiling)
    return {k: round(v, 6) for k, v in after.items()}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("slug", help="topic slug")
    parser.add_argument("--commit", action="store_true",
                        help="Write audited posteriors and append history entry")
    parser.add_argument("--reason", default="",
                        help="Operator reason citing the structural cause "
                             "(required with --commit)")
    parser.add_argument("--redteam", default="",
                        help="Red-team summary (required with --commit)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show last 15 audited replay steps")
    args = parser.parse_args()

    topic = load_topic(args.slug)

    # Critical safety check: never touch resolved or archived topics.
    # Their posteriors represent the resolution outcome, not a forecast that
    # can be rehabilitated. Beta cleanup is for ACTIVE topics only.
    status = topic.get("meta", {}).get("status", "")
    if status in ("RESOLVED", "ARCHIVED"):
        print(f"REFUSED: {args.slug} has status={status!r}.")
        print(f"  Beta cleanup is for ACTIVE topics whose posteriors were")
        print(f"  pegged via the pre-gate freeform-LR loophole. Resolved/archived")
        print(f"  topics carry the resolution outcome, not a rehabilitatable forecast.")
        print(f"  Posteriors at meta.status={status} will not be touched.")
        return 1

    audited, walked, n_skipped, skipped = _replay_audited(topic)

    if audited is None:
        print(f"Cannot replay {args.slug}: no clean prior entry at history[0].")
        return 1

    full = {k: v["posterior"] for k, v in topic["model"]["hypotheses"].items()}
    h_keys = list(topic["model"]["hypotheses"].keys())

    print(f"\n=== {args.slug} - audited replay ===")
    print(f"  posteriorHistory entries:   {len(topic['model']['posteriorHistory'])}")
    print(f"  replayed (non-anon):        {len(walked) - 1} (+1 prior)")
    print(f"  skipped (anonymous):        {n_skipped}")

    print(f"\n  hypothesis    full        audited     delta")
    print(f"  ----------    -------     -------     -------")
    for k in h_keys:
        f, a = full[k], audited[k]
        print(f"  {k:<12}  {f:.4f}      {a:.4f}      {a - f:+.4f}")

    if args.verbose:
        print("\n  audited replay (last 15 steps):")
        for w in walked[-15:]:
            posts = w["posteriors"]
            posts_s = " ".join(f"{k}={posts[k]:.3f}" for k in h_keys)
            print(f"    [{w['i']:>3}] {w['method'][:24]:<24} lens={w['lens']:<8} {posts_s}")
            if w["note"]:
                print(f"          {w['note'][:90]}")

        if skipped:
            print(f"\n  skipped (anonymous freeform — the loophole):")
            for s in skipped:
                print(f"    [{s['i']:>3}] {s['note']}")

    if not args.commit:
        print(f"\n[DRY RUN] No changes written. To apply, re-run with:")
        print(f"  --commit --reason '<structural cause>' --redteam '<red-team summary>'")
        return 0

    if not args.reason.strip() or not args.redteam.strip():
        print("ERROR: --commit requires both --reason and --redteam (non-empty).")
        print("       --reason: cite the structural cause that justifies the recommit")
        print("       --redteam: red-team analysis on the recommitted value")
        return 1

    # Apply audited posteriors
    for k in h_keys:
        topic["model"]["hypotheses"][k]["posterior"] = round(audited[k], 4)

    # Append history entry tagged beta_cleanup_recommit
    now_iso = datetime.now(timezone.utc).isoformat()
    history = topic["model"]["posteriorHistory"]
    history.append({
        "date": now_iso[:10],
        "timestamp": now_iso,
        "updateMethod": "beta_cleanup_recommit",
        "posteriors": {k: round(audited[k], 4) for k in h_keys},
        "priors": dict(full),
        "note": (f"BETA CLEANUP RECOMMIT: pre-resolution recommit to audited-only "
                 f"posterior. Replayed {len(walked) - 1} non-legacy entries; "
                 f"skipped {n_skipped} legacy unstamped entries. "
                 f"Reason: {args.reason}"),
        "redTeamSummary": args.redteam,
        "lrSource": {
            "lens": "OPERATOR_JUDGMENT",
            "lensSetAt": None,
            "source": "beta_cleanup",
        },
    })

    # Recompute expected value
    topic["model"]["expectedValue"] = round(
        sum(h.get("midpoint", 0) * h["posterior"]
            for h in topic["model"]["hypotheses"].values()),
        2,
    )
    topic.setdefault("meta", {})["lastUpdated"] = now_iso

    save_topic(topic)

    log_activity({
        "timestamp": now_iso,
        "action": "BETA_CLEANUP_RECOMMIT",
        "topic": args.slug,
        "summary": (
            f"Beta-test cleanup recommit on {args.slug}. Posteriors set to "
            f"audited-only replay (legacy unstamped entries excluded). "
            f"Deltas: " +
            ", ".join(f"{k}: {full[k]:.4f}->{audited[k]:.4f}" for k in h_keys) +
            f". Reason: {args.reason}"
        ),
        "source": "beta_cleanup_script",
        "platform": "framework",
        "route": "BETA_CLEANUP_RECOMMIT",
        "posteriorChange": {
            "before": full,
            "after": {k: round(audited[k], 4) for k in h_keys},
        },
        "redTeamAttached": True,
        "entriesReplayed": len(walked) - 1,
        "entriesSkipped": n_skipped,
        "notes": ("One-shot pre-resolution recommit. Topic returns to standard "
                  "gate-enforced flow going forward. Engine does not expose this "
                  "operation; it only lives in scripts/ as transient beta cleanup."),
    }, platform="framework")

    print(f"\n[OK] Committed audited posteriors to {args.slug}.")
    print(f"     posteriorHistory entry appended with updateMethod=beta_cleanup_recommit")
    print(f"     activity logged as BETA_CLEANUP_RECOMMIT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
