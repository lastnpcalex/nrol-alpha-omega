#!/usr/bin/env python3
"""
NRL-Alpha Omega — Evidence Compaction System
================================================

Summarizes old evidence entries into higher-level assertions to keep
the evidenceLog manageable without losing information.

Compacted entries are archived in topic["compactedEvidence"] and can
be restored at any time via restore_from_compaction().

Safety invariants:
  - NEVER compacts entries with claimState=CONTESTED
  - NEVER compacts entries referenced in unresolved contradictions
  - Original entries are always preserved in archived_entries

No external dependencies — Python stdlib only.
"""

import sys
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 timestamp, tolerating several common formats."""
    s = s.rstrip("Z").replace("+00:00", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {s!r}")


def _first_sentence(text: str) -> str:
    """Extract the first sentence from a text string."""
    if not text:
        return ""
    # Split on sentence-ending punctuation followed by space or end
    match = re.match(r"([^.!?]*[.!?])", text)
    if match:
        return match.group(1).strip()
    # No sentence-ending punctuation — return first 120 chars
    return text[:120].strip()


def _week_label(dt: datetime) -> str:
    """Return a period label like '2026-03-W1' for the 2-week window."""
    iso_year, iso_week, _ = dt.isocalendar()
    # Group into 2-week windows: W1 = weeks 1-2, W2 = weeks 3-4, etc.
    window = (iso_week - 1) // 2 + 1
    return f"{iso_year}-{dt.month:02d}-W{window}"


def _period_string(entries: list) -> str:
    """Build a 'YYYY-MM-DD/YYYY-MM-DD' period string from entries."""
    times = []
    for e in entries:
        try:
            times.append(_parse_iso(e["time"]))
        except (KeyError, ValueError):
            continue
    if not times:
        return "unknown"
    times.sort()
    return f"{times[0].strftime('%Y-%m-%d')}/{times[-1].strftime('%Y-%m-%d')}"


def _get_contested_times(topic: dict) -> set:
    """Collect timestamps of entries referenced in unresolved contradictions."""
    times = set()
    tracker = topic.get("contradictionTracker", {})
    for c in tracker.get("unresolved", []):
        if "entry_a_time" in c:
            times.add(c["entry_a_time"])
        if "entry_b_time" in c:
            times.add(c["entry_b_time"])
    return times


# ---------------------------------------------------------------------------
# 1. identify_compactable
# ---------------------------------------------------------------------------

def identify_compactable(topic: dict, max_age_days: int = 14,
                         min_entries: int = 10) -> list:
    """
    Group old evidence entries into 2-week windows eligible for compaction.

    Returns a list of groups, where each group is a list of evidence indices
    (positions in topic["evidenceLog"]).

    Exclusions:
      - Entries newer than max_age_days
      - Entries with claimState == "CONTESTED"
      - Entries referenced in topic["contradictionTracker"]["unresolved"]
      - Entries already tagged "COMPACTED"
    """
    evidence_log = topic.get("evidenceLog", [])
    if not evidence_log:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    contested_times = _get_contested_times(topic)

    # Collect eligible indices grouped by 2-week window
    windows = {}  # window_label -> [indices]
    for i, entry in enumerate(evidence_log):
        # Skip already-compacted summary entries
        if entry.get("tag") == "COMPACTED":
            continue

        # Skip contested entries
        if entry.get("claimState") == "CONTESTED":
            continue

        # Parse timestamp
        try:
            entry_time = _parse_iso(entry.get("time", ""))
        except ValueError:
            continue

        # Must be older than cutoff
        if entry_time >= cutoff:
            continue

        # Skip if referenced in unresolved contradictions
        if entry.get("time", "") in contested_times:
            continue

        label = _week_label(entry_time)
        windows.setdefault(label, []).append(i)

    # Only return groups that meet min_entries threshold
    groups = []
    for label in sorted(windows.keys()):
        indices = windows[label]
        if len(indices) >= min_entries:
            groups.append(indices)

    return groups


# ---------------------------------------------------------------------------
# 2. compact_group
# ---------------------------------------------------------------------------

def compact_group(topic: dict, indices: list) -> dict:
    """
    Compact a group of evidence entries into a summary dict.

    Args:
        topic: the topic state
        indices: list of evidence log indices to compact

    Returns a compaction record with summary, breakdowns, key claims,
    and the full archived entries.
    """
    evidence_log = topic.get("evidenceLog", [])
    entries = [evidence_log[i] for i in indices]

    # --- Breakdowns ---
    tag_counts = Counter(e.get("tag", "UNKNOWN") for e in entries)
    source_counts = Counter()
    for e in entries:
        src = e.get("source", "unknown")
        # Split combined sources like "AP+Guardian"
        for part in re.split(r"[+,/&]", src):
            part = part.strip()
            if part:
                source_counts[part] += 1

    # --- Key claims (effectiveWeight >= 0.8, sorted descending) ---
    weighted = []
    for e in entries:
        w = e.get("effectiveWeight", 0.0)
        if isinstance(w, (int, float)) and w >= 0.8:
            weighted.append(e)
    weighted.sort(key=lambda x: x.get("effectiveWeight", 0), reverse=True)
    key_claims = weighted[:5]

    # --- Summary generation (heuristic, no LLM) ---
    period = _period_string(entries)

    # Group first sentences by tag, sorted by weight
    by_tag = {}
    for e in entries:
        tag = e.get("tag", "UNKNOWN")
        by_tag.setdefault(tag, []).append(e)

    # Sort each tag group by effectiveWeight descending
    all_sorted = sorted(entries,
                        key=lambda x: x.get("effectiveWeight", 0),
                        reverse=True)

    # Top 3 claims for summary text
    top_sentences = []
    for e in all_sorted[:3]:
        sentence = _first_sentence(e.get("text", ""))
        if sentence:
            top_sentences.append(sentence)

    if top_sentences:
        summary = f"{period}: {len(entries)} entries. Key: {'; '.join(top_sentences)}"
    else:
        summary = f"{period}: {len(entries)} entries compacted."

    # --- Compact ID ---
    compact_id = f"compact-{period.replace('/', '-')}"

    return {
        "id": compact_id,
        "period": period,
        "created": _now_iso(),
        "summary": summary,
        "tag_breakdown": dict(tag_counts),
        "source_breakdown": dict(source_counts),
        "key_claims": key_claims,
        "original_count": len(entries),
        "original_indices": sorted(indices),
        "archived_entries": entries,
    }


# ---------------------------------------------------------------------------
# 3. apply_compaction
# ---------------------------------------------------------------------------

def apply_compaction(topic: dict, groups: list) -> list:
    """
    Execute compaction for multiple groups.

    For each group:
      1. Build the compaction record via compact_group()
      2. Store the record in topic["compactedEvidence"]
      3. Replace the original entries in evidenceLog with a single
         summary entry (tag=COMPACTED, provenance=DERIVED)

    Groups are processed in reverse index order so that earlier indices
    remain valid as later entries are removed.

    Args:
        topic: the topic state (mutated in place)
        groups: list of groups (each a list of evidence indices),
                as returned by identify_compactable()

    Returns a list of compaction records that were applied.
    """
    if "compactedEvidence" not in topic:
        topic["compactedEvidence"] = []

    evidence_log = topic.get("evidenceLog", [])
    records = []

    # Build all compaction records first (before mutating the log)
    group_records = []
    for indices in groups:
        record = compact_group(topic, indices)
        group_records.append((indices, record))

    # Sort groups by their first index descending so removals don't
    # shift indices of groups not yet processed
    group_records.sort(key=lambda gr: gr[0][0], reverse=True)

    for indices, record in group_records:
        # Archive
        topic["compactedEvidence"].append(record)

        # Build the summary entry that replaces the group
        # Effective weight = max weight of key_claims (preserves signal)
        key_claim_weights = [
            kc.get("effectiveWeight", 0.0)
            for kc in record.get("key_claims", [])
            if isinstance(kc.get("effectiveWeight"), (int, float))
        ]
        summary_weight = max(key_claim_weights) if key_claim_weights else 0.3

        summary_entry = {
            "time": record["created"],
            "tag": "COMPACTED",
            "text": record["summary"],
            "provenance": "DERIVED",
            "source": f"compaction:{record['id']}",
            "posteriorImpact": "NONE",
            "ledger": "FACT",
            "claimState": "SUPPORTED",
            "effectiveWeight": round(summary_weight, 4),
            "compactId": record["id"],
        }

        # Remove original entries (reverse order to preserve indices)
        sorted_indices = sorted(indices, reverse=True)
        for idx in sorted_indices:
            if idx < len(evidence_log):
                evidence_log.pop(idx)

        # Insert summary entry at the position of the earliest original
        insert_pos = min(indices)
        if insert_pos > len(evidence_log):
            insert_pos = len(evidence_log)
        evidence_log.insert(insert_pos, summary_entry)

        records.append(record)

    return records


# ---------------------------------------------------------------------------
# 4. restore_from_compaction
# ---------------------------------------------------------------------------

def restore_from_compaction(topic: dict, compact_id: str) -> int:
    """
    Reverse a compaction: restore original entries back into evidenceLog.

    Finds the compaction record by ID, locates the COMPACTED summary entry
    in the evidence log, replaces it with the archived originals, and
    removes the record from compactedEvidence.

    Args:
        topic: the topic state (mutated in place)
        compact_id: the ID of the compaction to reverse

    Returns the number of entries restored.

    Raises:
        KeyError: if compact_id not found
    """
    compacted = topic.get("compactedEvidence", [])
    evidence_log = topic.get("evidenceLog", [])

    # Find the compaction record
    record = None
    record_idx = None
    for i, r in enumerate(compacted):
        if r["id"] == compact_id:
            record = r
            record_idx = i
            break

    if record is None:
        raise KeyError(f"Compaction record not found: {compact_id!r}")

    # Find the summary entry in evidenceLog
    summary_idx = None
    for i, entry in enumerate(evidence_log):
        if entry.get("compactId") == compact_id:
            summary_idx = i
            break

    if summary_idx is None:
        raise KeyError(f"Summary entry for {compact_id!r} not found in evidenceLog")

    # Remove the summary entry
    evidence_log.pop(summary_idx)

    # Insert archived entries at the same position
    archived = record["archived_entries"]
    for offset, entry in enumerate(archived):
        evidence_log.insert(summary_idx + offset, entry)

    # Remove the compaction record
    compacted.pop(record_idx)

    return len(archived)


# ---------------------------------------------------------------------------
# 5. auto_compact
# ---------------------------------------------------------------------------

def auto_compact(topic: dict, threshold: int = 150) -> dict:
    """
    If evidenceLog exceeds threshold entries, identify and apply compaction.

    Args:
        topic: the topic state (mutated in place)
        threshold: minimum evidenceLog length to trigger compaction

    Returns a summary dict:
        {
            "triggered": bool,
            "log_size_before": int,
            "log_size_after": int,
            "groups_compacted": int,
            "entries_compacted": int,
            "compact_ids": [str, ...],
        }
    """
    evidence_log = topic.get("evidenceLog", [])
    size_before = len(evidence_log)

    result = {
        "triggered": False,
        "log_size_before": size_before,
        "log_size_after": size_before,
        "groups_compacted": 0,
        "entries_compacted": 0,
        "compact_ids": [],
    }

    if size_before <= threshold:
        return result

    groups = identify_compactable(topic)
    if not groups:
        return result

    result["triggered"] = True
    records = apply_compaction(topic, groups)

    result["log_size_after"] = len(topic.get("evidenceLog", []))
    result["groups_compacted"] = len(records)
    result["entries_compacted"] = sum(r["original_count"] for r in records)
    result["compact_ids"] = [r["id"] for r in records]

    return result
