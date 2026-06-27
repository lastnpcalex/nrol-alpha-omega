"""Export a sanitized NROL-AO snapshot for the black-hole public surface.

Produces a JSON file (default: surfaces/nrol-ao/data.json in the black-hole
repo) containing, per topic: slug, title, status, classification,
lastUpdated, committed posteriors, shadow posteriors + deltas, governance
health. NO evidence text, NO source names, NO article URLs — the slice is
safe to publish.

Usage:
    python export_blackhole_snapshot.py --black-hole <path-to-black-hole-repo>
    python export_blackhole_snapshot.py --black-hole <path> --out data.json

If --black-hole is omitted, writes data.json next to this script for inspection.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shadow_for(slug: str) -> dict | None:
    """Dynamics-derived shadow posteriors for a topic, or None if no spec."""
    try:
        from framework.dynamics_shadow import run
        return run(REPO_ROOT, slug)
    except Exception:
        return None


def _aligned_shadow(shadow_post: dict, h_keys: list[str]) -> dict[str, float]:
    """Fold the shadow's residual hypothesis out + renormalize to committed keys."""
    total = sum(shadow_post.get(k, 0.0) for k in h_keys)
    if total <= 0:
        return {k: 0.0 for k in h_keys}
    return {k: round(shadow_post.get(k, 0.0) / total, 4) for k in h_keys}


def build_snapshot() -> dict:
    """Build the sanitized snapshot dict. No PII, no evidence text."""
    from engine import get_overview
    overview = get_overview()
    topics_out: list[dict] = []
    for t in overview.get("topics", []):
        slug = t.get("slug", "")
        h_keys = list((t.get("posteriors") or {}).keys())
        committed = {k: round(float(v), 4) for k, v in (t.get("posteriors") or {}).items()}
        shadow_raw = _shadow_for(slug)
        shadow = None
        delta = None
        if shadow_raw and "shadow_posteriors" in shadow_raw:
            shadow = _aligned_shadow(shadow_raw["shadow_posteriors"], h_keys)
            delta = {k: round(shadow.get(k, 0.0) - committed.get(k, 0.0), 4) for k in h_keys}
        topics_out.append({
            "slug": slug,
            "title": t.get("title", slug),
            "status": t.get("status", "UNKNOWN"),
            "classification": t.get("classification", "ROUTINE"),
            "lastUpdated": t.get("lastUpdated", ""),
            "posteriors": committed,
            "shadow_posteriors": shadow,
            "shadow_delta": delta,
            "shadow_elapsed_entrenched_days": (shadow_raw or {}).get("elapsed_in_entrenched_days"),
            "health": t.get("health", "UNKNOWN"),
            "expectedValue": t.get("expectedValue"),
            "expectedUnit": t.get("expectedUnit"),
        })
    return {
        "generated_at": _now_iso(),
        "topic_count": len(topics_out),
        "topics": topics_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a sanitized NROL-AO snapshot for the black-hole surface.")
    ap.add_argument("--black-hole", default="", help="path to the black-hole repo (writes surfaces/nrol-ao/data.json)")
    ap.add_argument("--out", default="data.json", help="output filename (default data.json)")
    args = ap.parse_args()

    snapshot = build_snapshot()
    if args.black_hole:
        out_path = Path(args.black_hole) / "surfaces" / "nrol-ao" / args.out
    else:
        out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path} ({snapshot['topic_count']} topics, generated {snapshot['generated_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
