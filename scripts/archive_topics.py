"""Archive (abandon) topics: stamp + move to topics/_archive_<date>-<label>/.

Admin tool. Archiving is NOT resolution: no outcome is recorded and no
Brier scoring happens — the topic simply leaves the live set. Each topic
gets meta.status=ARCHIVED, meta.archivedAt, meta.archiveReason, and a
DECISION-ledger evidence entry, then the file moves to the archive dir
(gitignored; restorable via scripts/restore_from_archive.py pattern).

Usage:
    python scripts/archive_topics.py --label abandoned --reason "..." slug1 slug2
    python scripts/archive_topics.py --all-active-except calibration-hormuz-reopen-2027 \
        --label abandoned --reason "Operator decision: focus on hormuz only"
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).parent.parent
TOPICS = REPO / "topics"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def archive_topic(path: Path, dest_dir: Path, reason: str) -> str:
    topic = json.loads(path.read_text(encoding="utf-8"))
    meta = topic.setdefault("meta", {})
    prior_status = meta.get("status", "ACTIVE")
    meta["status"] = "ARCHIVED"
    meta["archivedAt"] = _now()
    meta["archiveReason"] = reason
    topic.setdefault("evidenceLog", []).append({
        "id": f"ev_{len(topic.get('evidenceLog', [])) + 1:03d}",
        "time": _now(),
        "tag": "INTEL",
        "text": (f"TOPIC ARCHIVED (was {prior_status}): {reason}. "
                 "No outcome recorded; not Brier-scored."),
        "source": "operator",
        "provenance": "USER_PROVIDED",
        "posteriorImpact": "NONE",
        "ledger": "DECISION",
        "claimState": "PROPOSED",
        "effectiveWeight": 0.5,
    })
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    dest.write_text(json.dumps(topic, indent=2, ensure_ascii=True), encoding="utf-8")
    path.unlink()
    return meta.get("slug", path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slugs", nargs="*", help="topic slugs to archive")
    parser.add_argument("--all-active-except", default="",
                        help="archive every ACTIVE topic except this slug (comma-separated keeps)")
    parser.add_argument("--label", default="abandoned")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dest_dir = TOPICS / f"_archive_{datetime.now(timezone.utc).date().isoformat()}-{args.label}"
    keeps = {s.strip() for s in args.all_active_except.split(",") if s.strip()}

    targets = []
    if args.slugs:
        for slug in args.slugs:
            p = TOPICS / f"{slug}.json"
            if not p.exists():
                print(f"[SKIP] {slug}: not found")
                continue
            targets.append(p)
    if keeps:
        for p in sorted(TOPICS.glob("*.json")):
            if p.stem.startswith("_") or p.name == "manifest.json":
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = d.get("meta") or {}
            if meta.get("status") == "ACTIVE" and meta.get("slug") not in keeps and p.stem not in keeps:
                targets.append(p)

    if not targets:
        print("Nothing to archive.")
        return 0
    for p in targets:
        if args.dry_run:
            print(f"[DRY] would archive {p.name} -> {dest_dir.name}/")
        else:
            slug = archive_topic(p, dest_dir, args.reason)
            print(f"[ARCHIVED] {slug} -> {dest_dir.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
