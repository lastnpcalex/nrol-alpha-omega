#!/usr/bin/env python3
"""
NRL-Alpha Omega — Contradiction Detection System
====================================================

Detects contradictions between new evidence entries and recent history.
Three modes:
  DIRECT    — explicit negation via antonym pairs / negation markers
  MAGNITUDE — same metric, significantly different numeric values
  TEMPORAL  — same event claimed at different times

No external dependencies — Python stdlib only (re, datetime).

Usage:
    from framework.contradictions import detect_contradictions, get_unresolved_contradictions

    hits = detect_contradictions(topic, new_entry)
    # hits = [{"type": "DIRECT", "entry_a": ..., "entry_b": ..., "reason": ...}, ...]
"""

import sys
import re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOOKBACK = 50  # how many recent evidence entries to compare against

# Antonym pairs for DIRECT contradiction detection.
# Each tuple is (word_a, word_b) — if one text has word_a and the other has
# word_b, and they share subject overlap, it's a candidate contradiction.
ANTONYM_PAIRS = [
    ("opened", "closed"),
    ("open", "closed"),
    ("open", "shut"),
    ("confirmed", "denied"),
    ("confirm", "deny"),
    ("seized", "not seized"),
    ("rising", "falling"),
    ("rose", "fell"),
    ("increase", "decrease"),
    ("increased", "decreased"),
    ("alive", "dead"),
    ("advancing", "retreating"),
    ("advance", "retreat"),
    ("attacked", "defended"),
    ("escalation", "de-escalation"),
    ("escalate", "de-escalate"),
    ("blockade", "transit"),
    ("blocked", "unblocked"),
    ("suspended", "resumed"),
    ("suspend", "resume"),
    ("approved", "rejected"),
    ("approve", "reject"),
    ("ceasefire", "offensive"),
    ("peace", "war"),
    ("allied", "hostile"),
    ("withdrawal", "deployment"),
    ("withdraw", "deploy"),
    ("success", "failure"),
    ("victory", "defeat"),
    ("captured", "lost"),
    ("gained", "lost"),
]

NEGATION_MARKERS = [
    "not", "no", "never", "denied", "denies", "deny",
    "false", "debunked", "fake", "fabricated", "incorrect",
    "retracted", "disproven", "unconfirmed", "refuted",
    "walked back", "reversed", "contradicted",
]

# Thresholds for MAGNITUDE contradictions
PRICE_THRESHOLD = 0.10   # 10% divergence for prices / monetary values
COUNT_THRESHOLD = 0.20   # 20% divergence for counts / quantities

# Words that signal a price / monetary context
PRICE_CONTEXT = {"$", "brent", "wti", "crude", "price", "barrel", "bbl", "gas", "gallon"}
# Words that signal a count context
COUNT_CONTEXT = {"ships", "transits", "killed", "dead", "kia", "troops", "casualties",
                 "sorties", "missiles", "drones", "vessels", "tankers"}


# ---------------------------------------------------------------------------
# Topic data structure initializer
# ---------------------------------------------------------------------------

def ensure_contradiction_tracker(topic: dict) -> dict:
    """
    Ensure topic has a contradictionTracker structure. Creates it if missing.
    Returns the tracker dict.

    Structure:
        topic["contradictionTracker"] = {
            "unresolved": [
                {
                    "type": "DIRECT" | "MAGNITUDE" | "TEMPORAL",
                    "entry_a_time": str,   # ISO timestamp of older entry
                    "entry_a_text": str,
                    "entry_b_time": str,   # ISO timestamp of newer entry
                    "entry_b_text": str,
                    "reason": str,         # human-readable explanation
                    "detected": str,       # ISO timestamp of detection
                }
            ],
            "resolved": [
                {
                    ...same fields as above...,
                    "resolution": "A_CORRECT" | "B_CORRECT" | "BOTH_PARTIAL" | "SUPERSEDED",
                    "resolvedAt": str,     # ISO timestamp
                }
            ]
        }
    """
    if "contradictionTracker" not in topic:
        topic["contradictionTracker"] = {
            "unresolved": [],
            "resolved": [],
        }
    tracker = topic["contradictionTracker"]
    if "unresolved" not in tracker:
        tracker["unresolved"] = []
    if "resolved" not in tracker:
        tracker["resolved"] = []
    return tracker


# ---------------------------------------------------------------------------
# Text utilities (stdlib only)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation except $, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s$\-.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_nouns(text: str) -> set:
    """
    Cheap noun extraction: words that are 3+ chars, not common stopwords.
    Good enough for subject overlap detection without NLP libs.
    """
    stopwords = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "had", "her", "was", "one", "our", "out", "has", "have", "been",
        "from", "that", "this", "with", "they", "will", "what", "when",
        "make", "like", "just", "over", "such", "take", "than", "them",
        "very", "some", "could", "would", "about", "after", "their",
        "which", "other", "into", "more", "also", "been", "since",
        "says", "said", "per", "via", "between", "through", "during",
        "before", "because", "where", "there", "being", "those", "each",
        "report", "reports", "reported", "according", "sources", "source",
        "new", "now", "still", "may", "likely",
    }
    words = set(re.findall(r"[a-z]{3,}", _normalize(text)))
    return words - stopwords


def _extract_numbers(text: str) -> list:
    """
    Extract (number, context_words) tuples from text.
    Context words = the 3 words before and after the number.
    """
    results = []
    normalized = _normalize(text)
    tokens = normalized.split()

    for i, token in enumerate(tokens):
        # Strip leading $ sign for matching
        clean = token.lstrip("$").rstrip("%")
        try:
            value = float(clean)
        except ValueError:
            continue

        # Grab context window (3 words before, 3 after)
        start = max(0, i - 3)
        end = min(len(tokens), i + 4)
        context = set(tokens[start:end]) - {token}
        results.append((value, context))

    return results


def _extract_dates(text: str) -> list:
    """Extract ISO-ish dates from text (YYYY-MM-DD patterns)."""
    return re.findall(r"\d{4}-\d{2}-\d{2}", text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Core detection functions
# ---------------------------------------------------------------------------

def check_negation_contradiction(new_text: str, existing_text: str) -> str | None:
    """
    Detect direct contradiction via antonym pairs and negation markers.

    Returns a reason string if contradiction found, else None.
    """
    new_norm = _normalize(new_text)
    old_norm = _normalize(existing_text)
    new_words = set(new_norm.split())
    old_words = set(old_norm.split())

    # Check antonym pairs
    for word_a, word_b in ANTONYM_PAIRS:
        # Check both directions
        if word_a in new_norm and word_b in old_norm:
            return f"Antonym pair detected: new has '{word_a}', existing has '{word_b}'"
        if word_b in new_norm and word_a in old_norm:
            return f"Antonym pair detected: new has '{word_b}', existing has '{word_a}'"

    # Check negation markers flipping the same claim
    for marker in NEGATION_MARKERS:
        if marker in new_words and marker not in old_words:
            # New entry negates something — check if they share enough subject matter
            overlap = _extract_nouns(new_text) & _extract_nouns(existing_text)
            if len(overlap) >= 2:
                return (f"Negation marker '{marker}' in new entry, absent from existing. "
                        f"Shared subjects: {', '.join(sorted(overlap)[:5])}")
        if marker in old_words and marker not in new_words:
            overlap = _extract_nouns(new_text) & _extract_nouns(existing_text)
            if len(overlap) >= 2:
                return (f"Negation marker '{marker}' in existing entry, absent from new. "
                        f"Shared subjects: {', '.join(sorted(overlap)[:5])}")

    return None


def check_numeric_contradiction(new_entry: dict, existing: dict,
                                threshold: float = 0.10) -> str | None:
    """
    Check for MAGNITUDE contradiction: same metric, significantly different values.

    Extracts numbers from both entries, matches by surrounding context words,
    and flags if the divergence exceeds the threshold.

    Args:
        threshold: default 0.10 (10%). Automatically bumped to COUNT_THRESHOLD
                   if the context suggests a count rather than a price.

    Returns reason string if contradiction found, else None.
    """
    new_nums = _extract_numbers(new_entry.get("text", ""))
    old_nums = _extract_numbers(existing.get("text", ""))

    if not new_nums or not old_nums:
        return None

    for new_val, new_ctx in new_nums:
        for old_val, old_ctx in old_nums:
            # Need at least 1 shared context word to consider them the same metric
            shared_ctx = new_ctx & old_ctx
            if not shared_ctx:
                continue

            # Skip if either value is zero (avoids division by zero, and zero
            # values are often day-counts or ordinals, not metrics)
            if old_val == 0 or new_val == 0:
                continue

            # Determine applicable threshold based on context
            all_ctx = new_ctx | old_ctx
            if all_ctx & COUNT_CONTEXT:
                effective_threshold = max(threshold, COUNT_THRESHOLD)
            elif all_ctx & PRICE_CONTEXT:
                effective_threshold = min(threshold, PRICE_THRESHOLD)
            else:
                effective_threshold = threshold

            divergence = abs(new_val - old_val) / max(abs(old_val), abs(new_val))
            if divergence > effective_threshold:
                return (f"Numeric divergence {divergence:.1%} on metric "
                        f"(context: {', '.join(sorted(shared_ctx)[:3])}): "
                        f"new={new_val}, existing={old_val}, "
                        f"threshold={effective_threshold:.0%}")

    return None


# ---------------------------------------------------------------------------
# Feed mismatch detection
# ---------------------------------------------------------------------------

FEED_CONTRADICTION_KEYWORDS = {
    "transits_day": {
        "low_indicators": ["standstill", "zero transit", "no ships", "blocked",
                           "halted", "shut down", "no vessels"],
        "high_indicators": ["resumed", "flowing", "heavy traffic",
                            "normal operations", "fully open"],
        "low_threshold": 5,
        "high_threshold": 30,
    },
    "brent": {
        "low_indicators": ["oil plunges", "prices crash", "crude falling",
                           "oil collapses", "price drop"],
        "high_indicators": ["oil surges", "record high", "prices spike",
                            "crude soars", "oil skyrockets"],
        "low_threshold": 60,
        "high_threshold": 110,
    },
    "wti": {
        "low_indicators": ["oil plunges", "prices crash", "crude falling"],
        "high_indicators": ["oil surges", "record high", "prices spike"],
        "low_threshold": 55,
        "high_threshold": 105,
    },
}


def check_feed_mismatch(new_entry: dict, topic: dict) -> str | None:
    """
    Check if new evidence text contradicts current dataFeed values.
    Returns reason string if mismatch found, else None.
    """
    feeds = topic.get("dataFeeds", {})
    text_lower = new_entry.get("text", "").lower()

    for feed_id, rules in FEED_CONTRADICTION_KEYWORDS.items():
        feed = feeds.get(feed_id)
        if not feed or feed.get("value") is None:
            continue
        value = feed["value"]
        if not isinstance(value, (int, float)):
            continue

        # Text says low but data says high
        if value >= rules["high_threshold"]:
            for kw in rules["low_indicators"]:
                if kw in text_lower:
                    return (f"FEED_MISMATCH: text says '{kw}' but {feed_id}="
                            f"{value} (>={rules['high_threshold']})")

        # Text says high but data says low
        if value <= rules["low_threshold"]:
            for kw in rules["high_indicators"]:
                if kw in text_lower:
                    return (f"FEED_MISMATCH: text says '{kw}' but {feed_id}="
                            f"{value} (<={rules['low_threshold']})")

    return None


def detect_contradictions(topic: dict, new_entry: dict) -> list:
    """
    Check a new evidence entry against recent evidence (last LOOKBACK entries).

    Runs three detection passes:
      1. DIRECT  — negation / antonym contradiction
      2. MAGNITUDE — numeric value divergence
      3. TEMPORAL — same event, different dates

    Returns list of contradiction dicts. Also appends any hits to
    topic["contradictionTracker"]["unresolved"].

    Each hit:
        {
            "type": "DIRECT" | "MAGNITUDE" | "TEMPORAL",
            "entry_a_time": str,
            "entry_a_text": str,
            "entry_b_time": str,
            "entry_b_text": str,
            "reason": str,
            "detected": str,
        }
    """
    tracker = ensure_contradiction_tracker(topic)
    evidence_log = topic.get("evidenceLog", [])
    recent = evidence_log[-LOOKBACK:]
    new_text = new_entry.get("text", "")
    new_time = new_entry.get("time", _now_iso())
    hits = []

    for existing in recent:
        old_text = existing.get("text", "")
        old_time = existing.get("time", "")

        # Skip self-comparison (same timestamp + same text)
        if old_time == new_time and old_text == new_text:
            continue

        # --- DIRECT contradiction ---
        # Require subject overlap >= 2 nouns before checking polarity
        noun_overlap = _extract_nouns(new_text) & _extract_nouns(old_text)
        if len(noun_overlap) >= 2:
            reason = check_negation_contradiction(new_text, old_text)
            if reason:
                hit = _make_hit("DIRECT", old_time, old_text,
                                new_time, new_text, reason, severity="HIGH")
                hits.append(hit)
                tracker["unresolved"].append(hit)
                continue  # one hit per pair is enough

        # --- MAGNITUDE contradiction ---
        reason = check_numeric_contradiction(new_entry, existing)
        if reason:
            hit = _make_hit("MAGNITUDE", old_time, old_text,
                            new_time, new_text, reason, severity="MEDIUM")
            hits.append(hit)
            tracker["unresolved"].append(hit)
            continue

        # --- TEMPORAL contradiction ---
        # Same tag + high noun overlap but different dates embedded in text
        if (new_entry.get("tag") == existing.get("tag")
                and len(noun_overlap) >= 2):
            new_dates = _extract_dates(new_text)
            old_dates = _extract_dates(old_text)
            if new_dates and old_dates:
                # If they mention different dates for what looks like the same event
                if set(new_dates) != set(old_dates):
                    reason = (f"Same tag '{new_entry.get('tag')}' and shared subjects "
                              f"({', '.join(sorted(noun_overlap)[:4])}), but different "
                              f"dates: new mentions {new_dates}, "
                              f"existing mentions {old_dates}")
                    hit = _make_hit("TEMPORAL", old_time, old_text,
                                    new_time, new_text, reason, severity="LOW")
                    hits.append(hit)
                    tracker["unresolved"].append(hit)

    # --- FEED_MISMATCH contradiction (text vs structured data feeds) ---
    feed_reason = check_feed_mismatch(new_entry, topic)
    if feed_reason:
        hit = _make_hit("FEED_MISMATCH", "", "", new_time, new_text,
                        feed_reason, severity="HIGH")
        hits.append(hit)
        tracker["unresolved"].append(hit)

    return hits


# ---------------------------------------------------------------------------
# Contradiction management
# ---------------------------------------------------------------------------

def get_unresolved_contradictions(topic: dict) -> list:
    """Return all unresolved contradictions from the tracker."""
    tracker = ensure_contradiction_tracker(topic)
    return list(tracker["unresolved"])


def resolve_contradiction(topic: dict, index: int, resolution: str) -> dict:
    """
    Move a contradiction from unresolved to resolved.

    Args:
        index: position in the unresolved list
        resolution: one of A_CORRECT, B_CORRECT, BOTH_PARTIAL, SUPERSEDED

    Returns the resolved contradiction dict.
    Raises IndexError if index is out of range, ValueError if resolution invalid.
    """
    valid_resolutions = {"A_CORRECT", "B_CORRECT", "BOTH_PARTIAL", "SUPERSEDED"}
    if resolution not in valid_resolutions:
        raise ValueError(f"Invalid resolution '{resolution}'. "
                         f"Must be one of: {', '.join(sorted(valid_resolutions))}")

    tracker = ensure_contradiction_tracker(topic)
    if index < 0 or index >= len(tracker["unresolved"]):
        raise IndexError(f"Index {index} out of range. "
                         f"{len(tracker['unresolved'])} unresolved contradictions.")

    contradiction = tracker["unresolved"].pop(index)
    contradiction["resolution"] = resolution
    contradiction["resolvedAt"] = _now_iso()
    tracker["resolved"].append(contradiction)
    return contradiction


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_hit(ctype: str, a_time: str, a_text: str,
              b_time: str, b_text: str, reason: str,
              severity: str = "MEDIUM") -> dict:
    return {
        "type": ctype,
        "severity": severity,
        "entry_a_time": a_time,
        "entry_a_text": a_text,
        "entry_b_time": b_time,
        "entry_b_text": b_text,
        "reason": reason,
        "detected": _now_iso(),
    }
