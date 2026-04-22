"""
migrate_to_lr.py — Convert topic indicators from pp-shift posteriorEffect
strings to native likelihood ratio (LR) format (schemaVersion 2).

Derives LRs from current posteriors (not design priors), per spec A6.
Flags indicators where design-prior-derived LR would differ >30% from
current-posterior-derived LR (posterior drift warning).

Usage:
    python framework/migrate_to_lr.py --topic calibration-fed-rate-2026
    python framework/migrate_to_lr.py --all
    python framework/migrate_to_lr.py --all --dry-run
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Allow running from repo root or framework/
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import load_topic, save_topic, suggest_likelihoods, _now_iso

TOPICS_DIR = Path(__file__).parent.parent / "topics"

TIER_DECAY_DEFAULTS = {"tier1": 0.70, "tier2": 0.65, "tier3": 0.50}


def _pp_string_to_lrs(topic: dict, indicator: dict, tier_key: str) -> dict:
    """
    Convert a single indicator's posteriorEffect string to a likelihoods dict
    using current posteriors as the reference point.

    Returns:
        {
            "likelihoods": {H1: float, ...},   # normalized to max=1.0
            "lr_decay": float,
            "drift_warning": bool,              # True if design vs current LR diff >30%
            "drift_detail": str,
        }
    """
    hypotheses = topic["model"]["hypotheses"]
    h_keys = list(hypotheses.keys())
    current_priors = {k: h["posterior"] for k, h in hypotheses.items()}

    # Use suggest_likelihoods to parse posteriorEffect via existing engine
    result = suggest_likelihoods(topic, [indicator["id"]])

    if not result["ready"] or not result["suggested_likelihoods"]:
        return {
            "likelihoods": None,
            "lr_decay": TIER_DECAY_DEFAULTS.get(tier_key, 0.65),
            "drift_warning": False,
            "drift_detail": f"Could not parse: {result.get('unparseable')}",
            "error": True,
        }

    # suggest_likelihoods already uses current priors — these are current-derived LRs
    current_lrs = result["suggested_likelihoods"]

    # Compute what LRs would have been at design priors (if design_prior available)
    # We don't store design priors, so we check the combined_shifts_pp
    # and compute LRs from a flat prior (0.25 each for 4 hypotheses) as a proxy.
    # If the flat-prior LR differs >30% from current-derived LR, flag it.
    flat_prior = {k: 1.0 / len(h_keys) for k in h_keys}
    shifts = result["combined_shifts_pp"]

    flat_targets = {k: max(0.005, min(0.995, flat_prior[k] + shifts.get(k, 0) / 100))
                    for k in h_keys}
    flat_total = sum(flat_targets.values())
    flat_targets = {k: v / flat_total for k, v in flat_targets.items()}
    flat_raw = {k: (flat_targets[k] / flat_prior[k]) if flat_prior[k] > 0 else 1.0
                for k in h_keys}
    flat_max = max(flat_raw.values()) or 1.0
    flat_lrs = {k: round(v / flat_max, 6) for k, v in flat_raw.items()}

    # Check drift: max absolute difference between flat-prior and current LRs
    drift = max(abs(current_lrs.get(k, 0) - flat_lrs.get(k, 0)) for k in h_keys)
    drift_warning = drift > 0.30
    drift_detail = (f"LR drift {drift:.2f} vs flat-prior baseline "
                    f"(current posterior has diverged from design assumptions)"
                    if drift_warning else "")

    return {
        "likelihoods": current_lrs,
        "lr_decay": TIER_DECAY_DEFAULTS.get(tier_key, 0.65),
        "drift_warning": drift_warning,
        "drift_detail": drift_detail,
        "error": False,
    }


def migrate_topic(slug: str, dry_run: bool = False) -> dict:
    """
    Migrate a single topic to schemaVersion 2 LR format.

    Returns a migration report dict.
    """
    topic = load_topic(slug)

    if topic.get("schemaVersion", 1) >= 2:
        return {"slug": slug, "status": "already_migrated", "indicators": []}

    report = {"slug": slug, "status": "ok", "indicators": [], "warnings": []}
    migrated = 0
    errors = 0
    drift_flags = 0

    for tier_key, indicators in topic["indicators"]["tiers"].items():
        for ind in indicators:
            # Skip if already has likelihoods (partial migration)
            if "likelihoods" in ind:
                report["indicators"].append({
                    "id": ind["id"],
                    "status": "skipped_already_lr",
                })
                continue

            effect = ind.get("posteriorEffect", "")
            if not effect or effect.strip() in ("", "NONE"):
                report["indicators"].append({
                    "id": ind["id"],
                    "status": "skipped_no_effect",
                })
                continue

            conv = _pp_string_to_lrs(topic, ind, tier_key)

            if conv.get("error"):
                report["indicators"].append({
                    "id": ind["id"],
                    "status": "error",
                    "detail": conv["drift_detail"],
                })
                errors += 1
                continue

            entry = {
                "id": ind["id"],
                "status": "migrated",
                "likelihoods": conv["likelihoods"],
                "lr_decay": conv["lr_decay"],
                "drift_warning": conv["drift_warning"],
            }
            if conv["drift_warning"]:
                entry["drift_detail"] = conv["drift_detail"]
                entry["lr_migration_warning"] = "design prior drift >30% — review before next firing"
                drift_flags += 1
                report["warnings"].append(
                    f"{ind['id']}: {conv['drift_detail']}"
                )

            if not dry_run:
                ind["likelihoods"] = conv["likelihoods"]
                ind["lr_decay"] = conv["lr_decay"]
                ind["n_firings"] = ind.get("n_firings", 0)
                ind["resolution_class"] = ind.get("resolution_class", False)
                if conv["drift_warning"]:
                    ind["lr_migration_warning"] = entry["lr_migration_warning"]

            report["indicators"].append(entry)
            migrated += 1

    if not dry_run:
        topic["schemaVersion"] = 2
        topic["migratedAt"] = _now_iso()
        save_topic(topic)

    report["summary"] = {
        "migrated": migrated,
        "errors": errors,
        "drift_flags": drift_flags,
        "dry_run": dry_run,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Migrate topic indicators to LR format")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", help="Single topic slug to migrate")
    group.add_argument("--all", action="store_true", help="Migrate all topics")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    args = parser.parse_args()

    if args.topic:
        slugs = [args.topic]
    else:
        slugs = [p.stem for p in TOPICS_DIR.glob("*.json")
                 if not p.stem.startswith("_")]

    all_reports = []
    for slug in sorted(slugs):
        try:
            report = migrate_topic(slug, dry_run=args.dry_run)
            all_reports.append(report)
            s = report["summary"]
            flag = "[DRY RUN] " if args.dry_run else ""
            print(f"{flag}{slug}: {s['migrated']} migrated, "
                  f"{s['errors']} errors, {s['drift_flags']} drift flags")
            for w in report.get("warnings", []):
                print(f"  DRIFT WARNING: {w}")
        except Exception as e:
            print(f"ERROR {slug}: {e}")
            all_reports.append({"slug": slug, "status": "exception", "error": str(e)})

    total_errors = sum(r.get("summary", {}).get("errors", 0) for r in all_reports
                       if "summary" in r)
    total_drift = sum(r.get("summary", {}).get("drift_flags", 0) for r in all_reports
                      if "summary" in r)
    print(f"\nDone. Total errors: {total_errors}, drift flags: {total_drift}")
    if total_drift > 0:
        print("Review drift-flagged indicators manually before their next firing.")


if __name__ == "__main__":
    main()
