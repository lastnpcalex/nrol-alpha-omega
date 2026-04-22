"""
stamp_resolution_dates.py — Stamp meta.resolutionDate on topics whose question
implies a fixed assessment date (e.g. "where will rate be on Dec 31, 2026?",
"assessed July 1, 2026", topics with a year in the slug like
calibration-fed-rate-2026).

Unlike resolution_deadline (per-hypothesis), resolutionDate is topic-level.
When the date passes, governance_report flags the topic as overdue for
resolution but does NOT auto-resolve — the operator must decide which
hypothesis won.

Rules:
  - If meta.resolutionDate is already set, never overwrite.
  - Extract from resolution / question text (highest specificity wins).
  - Fall back to slug year suffix (-YYYY -> YYYY-12-31).
  - Topics with no detectable resolution date are skipped.

Usage:
    python framework/stamp_resolution_dates.py --all
    python framework/stamp_resolution_dates.py --topic <slug>
    python framework/stamp_resolution_dates.py --all --dry-run
"""

import re
import sys
import argparse
import calendar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine import load_topic, save_topic, _now_iso, _add_evidence_raw

TOPICS_DIR = Path(__file__).parent.parent / "topics"

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_date_from_text(text):
    """Return YYYY-MM-DD for the most specific date expression in text, or None."""
    if not text:
        return None
    s = text.lower()

    m = re.search(
        r'\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|'
        r'aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+'
        r'(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})',
        s
    )
    if m:
        mo = MONTH_NAMES[m.group(1)]
        day = int(m.group(2))
        yr = int(m.group(3))
        return f"{yr:04d}-{mo:02d}-{day:02d}"

    m = re.search(
        r'\b(\d{1,2})(?:st|nd|rd|th)?\s+'
        r'(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|'
        r'aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+'
        r'(\d{4})',
        s
    )
    if m:
        mo = MONTH_NAMES[m.group(2)]
        day = int(m.group(1))
        yr = int(m.group(3))
        return f"{yr:04d}-{mo:02d}-{day:02d}"

    m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r'\bq([1-4])\s*(\d{4})\b', s)
    if m:
        q = int(m.group(1))
        yr = int(m.group(2))
        month_end = q * 3
        last_day = calendar.monthrange(yr, month_end)[1]
        return f"{yr:04d}-{month_end:02d}-{last_day:02d}"

    m = re.search(r'\b(?:by|end of|through|during|in)\s+(20\d{2})\b', s)
    if m:
        return f"{m.group(1)}-12-31"

    return None


def _derive_resolution_date(topic):
    """Return (date, source) or (None, None)."""
    meta = topic.get("meta", {})
    for field in ("resolution", "question", "title"):
        d = _parse_date_from_text(meta.get(field, ""))
        if d:
            return d, field
    slug = meta.get("slug", "")
    m = re.search(r'-(\d{4})\b', slug)
    if m:
        yr = m.group(1)
        return f"{yr}-12-31", "slug_year"
    return None, None


def stamp_topic(slug, dry_run=False):
    t = load_topic(slug)
    existing = t.get("meta", {}).get("resolutionDate")
    if existing:
        return {"slug": slug, "status": "already_set",
                "date": existing, "source": "existing"}

    date, source = _derive_resolution_date(t)
    if date is None:
        return {"slug": slug, "status": "no_date_detected",
                "date": None, "source": None}

    if not dry_run:
        t["meta"]["resolutionDate"] = date
        _add_evidence_raw(t, {
            "time": _now_iso(),
            "tag": "INTEL",
            "text": (f"RESOLUTION-DATE STAMPED: meta.resolutionDate={date} "
                     f"(derived from meta.{source}). When past, governance flags "
                     f"topic as overdue for resolution."),
            "provenance": "DERIVED",
            "posteriorImpact": "NONE",
            "ledger": "DECISION",
            "claimState": "SUPPORTED",
            "effectiveWeight": 1.0,
        })
        save_topic(t)

    return {"slug": slug, "status": "stamped", "date": date, "source": source}


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
            rows.append(stamp_topic(slug, dry_run=args.dry_run))
        except Exception as e:
            rows.append({"slug": slug, "status": f"ERROR: {e}",
                         "date": None, "source": None})

    print(f"{'Topic':<45} {'Status':<20} {'Date':<12} {'Source':<15}")
    print("-" * 92)
    for r in rows:
        date = r.get("date") or "-"
        source = r.get("source") or "-"
        print(f"{r['slug']:<45} {r['status']:<20} {date:<12} {source:<15}")

    stamped = [r for r in rows if r["status"] == "stamped"]
    print(f"\n{len(stamped)} topics stamped. "
          f"{sum(1 for r in rows if r['status']=='already_set')} already had date. "
          f"{sum(1 for r in rows if r['status']=='no_date_detected')} undetected.")


if __name__ == "__main__":
    main()
