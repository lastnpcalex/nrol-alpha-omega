"""
stamp_deadlines.py — Stamp resolution_deadline on time-bounded hypotheses.

Parses hypothesis labels (e.g. "<6 weeks", "6wk-4mo", "Q3 2026") against
the topic's startDate and adds resolution_deadline so _eliminate_expired_hypotheses
actually fires.

Rules:
  - Only hypotheses with an upper time bound get a deadline.
  - ">N" open-ended hypotheses get no deadline (they can't expire).
  - Existing resolution_deadline fields are never overwritten.

Usage:
    python framework/stamp_deadlines.py --all
    python framework/stamp_deadlines.py --topic hormuz-closure
    python framework/stamp_deadlines.py --all --dry-run
"""

import re
import sys
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine import load_topic, save_topic, _now_iso, _add_evidence_raw

TOPICS_DIR = Path(__file__).parent.parent / "topics"


def _parse_start(meta: dict) -> datetime | None:
    """Return topic start as UTC datetime, or None."""
    for key in ("startDate", "created"):
        s = meta.get(key, "")
        if s:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return None


def _deadline_from_label(label: str, start: datetime) -> datetime | None:
    """
    Derive a resolution_deadline from a hypothesis label string.

    Returns None if no upper bound is detectable (open-ended or non-temporal).
    Never sets a deadline on open-ended hypotheses (label starts with ">").
    """
    lbl = label.strip()

    # Open-ended: ">N" hypotheses can never expire
    if lbl.startswith(">"):
        return None

    # Explicit quarter: "Q2 2026", "by Q3 2026"
    m = re.search(r'Q([1-4])\s*(\d{4})', lbl, re.IGNORECASE)
    if m:
        q, yr = int(m.group(1)), int(m.group(2))
        month_end = q * 3
        # Last day of the quarter
        if month_end == 12:
            return datetime(yr, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        import calendar
        last_day = calendar.monthrange(yr, month_end)[1]
        return datetime(yr, month_end, last_day, 23, 59, 59, tzinfo=timezone.utc)

    # Explicit calendar year: "by 2027", ">=2027"
    m = re.search(r'\b(20\d{2})\b', lbl)
    if m:
        yr = int(m.group(1))
        return datetime(yr, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    # Extract the upper bound from range labels like "6wk-4mo", "4-12mo", "<6 weeks"
    # Strategy: find the rightmost time expression
    upper = _extract_upper_bound(lbl)
    if upper is None:
        return None

    value, unit = upper

    unit = unit.lower().rstrip("s")  # normalise plural
    if unit in ("day", "d"):
        return start + timedelta(days=value)
    elif unit in ("week", "wk", "w"):
        return start + timedelta(weeks=value)
    elif unit in ("month", "mo", "m"):
        # Approximate: 30.44 days/month
        return start + timedelta(days=round(value * 30.44))
    elif unit in ("year", "yr", "y"):
        return start + timedelta(days=round(value * 365.25))

    return None


def _extract_upper_bound(lbl: str):
    """
    Return (value, unit) for the upper bound in a label, or None.

    Handles:
      "<6 weeks"        → (6, "week")
      "6wk-4mo"         → (4, "mo")     ← rightmost
      "4-12mo"          → (12, "mo")    ← rightmost
      "<=18 months"     → (18, "month")
      "2 negative quarters, shallow (> -1.5%..." → None (no upper bound)
    """
    # Pattern: optional operator, number, optional dash/space, unit
    pattern = re.compile(
        r'(?:<=?|–|-|to)?\s*(\d+(?:\.\d+)?)\s*'
        r'(days?|d|weeks?|wks?|w|months?|mos?|m|years?|yrs?|y)\b',
        re.IGNORECASE
    )
    matches = list(pattern.finditer(lbl))
    if not matches:
        return None

    # Take the rightmost match as the upper bound
    m = matches[-1]

    # If the label starts with "<" or "<=", single value is the upper bound
    if lbl.startswith("<"):
        return (float(m.group(1)), m.group(2))

    # For ranges (e.g. "6wk-4mo"), rightmost is the upper bound
    return (float(m.group(1)), m.group(2))


def stamp_topic(slug: str, dry_run: bool = False) -> dict:
    """Stamp resolution_deadline on all time-bounded hypotheses in a topic."""
    topic = load_topic(slug)
    start = _parse_start(topic["meta"])
    if start is None:
        return {"slug": slug, "status": "no_start_date", "stamped": [], "skipped": []}

    stamped = []
    skipped = []
    already = []

    for k, h in topic["model"]["hypotheses"].items():
        if h.get("resolution_deadline"):
            already.append(k)
            continue

        label = h.get("label", "")
        deadline = _deadline_from_label(label, start)

        if deadline is None:
            skipped.append({"key": k, "label": label, "reason": "open-ended or non-temporal"})
            continue

        deadline_str = deadline.strftime("%Y-%m-%d")
        stamped.append({"key": k, "label": label, "deadline": deadline_str})

        if not dry_run:
            h["resolution_deadline"] = deadline_str

    if not dry_run and stamped:
        _add_evidence_raw(topic, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"DEADLINE MIGRATION: resolution_deadline stamped on "
                     f"{[s['key'] for s in stamped]} based on topic startDate {start.date()}."),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "SUPPORTED",
            "effectiveWeight": 1.0,
        })
        save_topic(topic)

    return {
        "slug": slug,
        "status": "ok",
        "start": str(start.date()),
        "stamped": stamped,
        "skipped": skipped,
        "already_set": already,
    }


def main():
    parser = argparse.ArgumentParser(description="Stamp resolution_deadline on hypotheses")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", help="Single topic slug")
    group.add_argument("--all", action="store_true", help="All topics")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    slugs = ([args.topic] if args.topic else
             sorted(p.stem for p in TOPICS_DIR.glob("*.json")
                    if not p.stem.startswith("_")))

    total_stamped = 0
    for slug in slugs:
        try:
            r = stamp_topic(slug, dry_run=args.dry_run)
            prefix = "[DRY] " if args.dry_run else ""
            if r["stamped"]:
                print(f"{prefix}{slug}  (start={r.get('start','')})")
                for s in r["stamped"]:
                    print(f"  + {s['key']:4s} [{s['label']:20s}] → deadline {s['deadline']}")
                for s in r["skipped"]:
                    print(f"  ~ {s['key']:4s} [{s['label']:20s}] skipped ({s['reason']})")
                total_stamped += len(r["stamped"])
            elif r["status"] != "ok":
                print(f"  {slug}: {r['status']}")
        except Exception as e:
            print(f"ERROR {slug}: {e}")

    print(f"\nTotal deadlines {'would be ' if args.dry_run else ''}stamped: {total_stamped}")


if __name__ == "__main__":
    main()
