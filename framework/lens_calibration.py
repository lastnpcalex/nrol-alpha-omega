"""
lens_calibration.py — Per-(lens, classification) Brier scoring.

For every RESOLVED topic, walks `posteriorHistory` chronologically and computes
per-update Brier against the resolved hypothesis. Each entry is attributed to
the lens that was active *at update time* (read from `lrSource.lens`), so
lens-switching mid-life is accounted for honestly.

Aggregates into cells keyed `"<lens>|<classification>"`. Each cell carries:
    { brier: <mean>, n: <count>, contributors: [<topic-slug>, ...] }

Cells with n < 5 are flagged as uncalibrated by the canvas picker.

Output: canvas/lens-brier.json — read by the topic detail page lens picker.

Usage:
    python framework/lens_calibration.py            # rebuild from all resolved
    python framework/lens_calibration.py --verbose
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Allow running from repo root or framework/
sys.path.insert(0, str(Path(__file__).parent.parent))

from framework.scoring import compute_brier_score

REPO_ROOT = Path(__file__).parent.parent
TOPICS_DIR = REPO_ROOT / "topics"
CANVAS_LENS_BRIER = REPO_ROOT.parent / "canvas" / "lens-brier.json"


def _resolved_hypothesis(topic: dict) -> str | None:
    """
    Return the hypothesis the topic resolved to, or None if not resolved.
    Reads predictionScoring.outcomes (latest entry) — the canonical resolution
    record per scoring.py.
    """
    ps = topic.get("predictionScoring") or {}
    outcomes = ps.get("outcomes") or []
    for entry in reversed(outcomes):
        # Skip PARTIAL_EXPIRY rows — those are per-hypothesis expirations,
        # not topic resolutions
        if entry.get("type") == "PARTIAL_EXPIRY":
            continue
        resolved = entry.get("resolved")
        if resolved:
            return resolved
    # Fallback: meta.status == RESOLVED + meta.resolvedHypothesis if pipeline
    # hasn't filled outcomes
    if topic.get("meta", {}).get("status") == "RESOLVED":
        return topic.get("meta", {}).get("resolvedHypothesis")
    return None


def _walk_history(topic: dict, resolved: str, slug: str, classification: str):
    """
    Yield (lens, brier) per posteriorHistory entry that has lrSource.

    Skips:
      - The resolution-time entry itself (posteriors collapsed to truth by
        construction; not a forecast).
      - Entries without lrSource (legacy pre-lens entries).
      - Entries whose posteriors don't include the resolved hypothesis.
    """
    history = topic.get("model", {}).get("posteriorHistory", []) or []
    if len(history) < 2:
        return

    # Last entry is typically the resolution; skip if its posteriors are
    # collapsed (resolved hypothesis at >= 0.99)
    for i, entry in enumerate(history):
        lr = entry.get("lrSource") or {}
        lens = lr.get("lens")
        if not lens:
            continue
        # Skip legacy retroactively-stamped entries — they were anonymous
        # bayesian_update calls before the provenance gate; their lens stamp
        # was assigned by migration, not by the operator at update time.
        # Including them would pollute OPERATOR_JUDGMENT's calibration with
        # un-calibratable historical data.
        if lr.get("legacy") is True or lr.get("source") == "legacy_migration":
            continue
        posteriors = entry.get("posteriors") or {}
        if not posteriors or resolved not in posteriors:
            continue
        # Skip near-resolution snapshots — they're not real forecasts
        if posteriors.get(resolved, 0) >= 0.995:
            continue
        try:
            result = compute_brier_score(posteriors, resolved)
        except Exception:
            continue
        yield lens, result["brier"]


def compute_calibration(topics_dir: Path = TOPICS_DIR, verbose: bool = False) -> dict:
    """
    Walk every topic JSON; for resolved topics, accumulate per-(lens, classification)
    Brier scores. Returns the cell map.
    """
    cells = {}  # key: "LENS|CLASSIFICATION" -> {sum, n, contributors:set, briers:[]}

    n_topics_total = 0
    n_topics_resolved = 0
    n_entries_scored = 0

    for path in sorted(topics_dir.glob("*.json")):
        if path.name.startswith("_") or path.name == "manifest.json":
            continue
        try:
            topic = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            if verbose:
                print(f"[skip] {path.name}: {e}", file=sys.stderr)
            continue
        n_topics_total += 1

        resolved = _resolved_hypothesis(topic)
        if not resolved:
            continue
        n_topics_resolved += 1

        slug = topic.get("meta", {}).get("slug") or path.stem
        classification = topic.get("meta", {}).get("classification") or "UNCLASSIFIED"

        for lens, brier in _walk_history(topic, resolved, slug, classification):
            key = f"{lens}|{classification}"
            cell = cells.setdefault(key, {
                "lens": lens,
                "classification": classification,
                "sum": 0.0,
                "n": 0,
                "contributors": set(),
                "briers": [],
            })
            cell["sum"] += brier
            cell["n"] += 1
            cell["contributors"].add(slug)
            cell["briers"].append(brier)
            n_entries_scored += 1

    # Finalize: compute means, convert sets to sorted lists
    final_cells = {}
    for key, cell in cells.items():
        n = cell["n"]
        if n == 0:
            continue
        briers = cell["briers"]
        mean = cell["sum"] / n
        # Sample variance for stability indicator
        var = sum((b - mean) ** 2 for b in briers) / n if n > 1 else 0.0
        final_cells[key] = {
            "lens": cell["lens"],
            "classification": cell["classification"],
            "brier": round(mean, 4),
            "n": n,
            "stddev": round(var ** 0.5, 4),
            "contributors": sorted(cell["contributors"]),
        }

    out = {
        "_schema": "Per-(lens, classification) Brier scores from resolved predictions. Populated by framework/lens_calibration.py. Cells with n < 5 are uncalibrated.",
        "_updated": datetime.now(timezone.utc).isoformat(),
        "_topics_scanned": n_topics_total,
        "_topics_resolved": n_topics_resolved,
        "_entries_scored": n_entries_scored,
        "cells": final_cells,
    }

    if verbose:
        print(f"Scanned {n_topics_total} topics, "
              f"{n_topics_resolved} resolved, "
              f"{n_entries_scored} entries scored, "
              f"{len(final_cells)} (lens, classification) cells.",
              file=sys.stderr)

    return out


def write_lens_brier(report: dict, path: Path = CANVAS_LENS_BRIER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def rebuild() -> dict:
    """
    Rebuild lens-brier.json from current state. Safe to call from save_topic
    or pipeline hooks — never raises (logs and returns empty on failure).
    """
    try:
        report = compute_calibration()
        write_lens_brier(report)
        return report
    except Exception as e:
        print(f"[lens_calibration.rebuild] failed: {e}", file=sys.stderr)
        return {"cells": {}, "_error": str(e)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and print summary; do not write file")
    args = parser.parse_args()

    report = compute_calibration(verbose=args.verbose)

    if args.dry_run:
        print(json.dumps(report, indent=2))
        return

    write_lens_brier(report)
    cells = report["cells"]
    print(f"Wrote {CANVAS_LENS_BRIER.relative_to(REPO_ROOT.parent)} — "
          f"{len(cells)} cell(s) from {report['_topics_resolved']} resolved "
          f"topic(s), {report['_entries_scored']} scored update(s)")
    if args.verbose and cells:
        print("\nCells:")
        for key in sorted(cells.keys()):
            c = cells[key]
            print(f"  {key:40s} brier={c['brier']:.4f} n={c['n']} "
                  f"σ={c['stddev']:.4f}")


if __name__ == "__main__":
    main()
