"""
NRL-Alpha Omega — News Triage Layer

Takes a headline (or short text) and returns what it means across all
active topics: which indicators it matches, what the pre-committed
posterior effects would be, source trust, R_t status, and a recommended
action.

Design informed by SOC alert triage architecture (cybersecurity):
  - Match incoming signal against pre-registered indicators
  - Enrich with context (source trust, staleness, dependencies)
  - Score severity and recommend action
  - Document reasoning for audit trail

Three output modes:
  INDICATOR_MATCH  — headline matches a pre-registered indicator
  TOPIC_RELEVANT   — no indicator match, but semantically relevant to a topic
  IRRELEVANT       — no match to any active topic

The TOPIC_RELEVANT mode addresses Millidge's flexibility critique:
the system doesn't only see what it pre-registered. It also flags novel
events that touch a topic's domain without matching a specific indicator.
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

TOPICS_DIR = Path(__file__).parent.parent / "topics"


def triage(headline: str, source: str = None, topic_loader=None) -> dict:
    """
    Triage a news headline against all active topics.

    Parameters
    ----------
    headline : str
        The news headline or short text to triage.
    source : str, optional
        Source name (e.g. "Reuters", "CENTCOM") for trust scoring.
    topic_loader : callable, optional
        Function() -> list[dict] returning all active topics.

    Returns
    -------
    dict with:
        headline: str
        source: str | None
        timestamp: str (ISO)
        matches: list of per-topic match results (see _triage_topic)
        top_action: str — highest-priority action across all topics
        summary: str — one-line human-readable summary
    """
    if topic_loader is None:
        topics = _load_all_active()
    else:
        topics = topic_loader()

    matches = []
    for topic in topics:
        result = _triage_topic(headline, source, topic)
        if result["relevance"] != "IRRELEVANT":
            matches.append(result)

    # Sort: INDICATOR_MATCH first, then TOPIC_RELEVANT, by severity
    action_priority = {"UPDATE_CYCLE": 0, "LOG_EVIDENCE": 1, "MONITOR": 2, "REVIEW": 3}
    matches.sort(key=lambda m: action_priority.get(m["action"], 99))

    top_action = matches[0]["action"] if matches else "IGNORE"
    summary = _build_summary(headline, matches)

    return {
        "headline": headline,
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matches": matches,
        "top_action": top_action,
        "summary": summary,
    }


def _triage_topic(headline: str, source: str, topic: dict) -> dict:
    """
    Check one headline against one topic's indicators, watchpoints,
    search queries, and general domain keywords.

    Returns dict with:
        slug, title, relevance, action, indicator_matches,
        watchpoint_matches, keyword_matches, rt_status,
        source_trust, pre_committed_effects, explanation
    """
    slug = topic.get("meta", {}).get("slug", "")
    title = topic.get("meta", {}).get("title", slug)
    headline_lower = headline.lower()
    headline_words = set(re.findall(r'\b\w{3,}\b', headline_lower))

    indicator_matches = _match_indicators(headline_lower, headline_words, topic)
    watchpoint_matches = _match_watchpoints(headline_lower, headline_words, topic)
    keyword_matches = _match_keywords(headline_lower, headline_words, topic)

    # Determine relevance level
    if indicator_matches:
        relevance = "INDICATOR_MATCH"
    elif watchpoint_matches or len(keyword_matches) >= 3:
        relevance = "TOPIC_RELEVANT"
    elif len(keyword_matches) >= 1:
        # Weak relevance — only if multiple keyword groups match
        relevance = "TOPIC_RELEVANT" if len(keyword_matches) >= 2 else "IRRELEVANT"
    else:
        relevance = "IRRELEVANT"

    if relevance == "IRRELEVANT":
        return {"slug": slug, "title": title, "relevance": "IRRELEVANT",
                "action": "IGNORE", "indicator_matches": [],
                "watchpoint_matches": [], "keyword_matches": []}

    # Determine action
    if indicator_matches:
        # Check tier of matched indicators
        has_tier1 = any(m["tier"] == "tier1_critical" for m in indicator_matches)
        has_tier2 = any(m["tier"] == "tier2_strong" for m in indicator_matches)
        if has_tier1:
            action = "UPDATE_CYCLE"
        elif has_tier2:
            action = "UPDATE_CYCLE"
        else:
            action = "LOG_EVIDENCE"
    elif watchpoint_matches:
        action = "MONITOR"
    else:
        action = "REVIEW"

    # Get R_t status for this topic
    rt_status = _get_rt_status(topic)

    # Get source trust if available
    source_trust = _get_source_trust(source, topic) if source else None

    # Get pre-committed effects from matched indicators
    pre_committed = []
    for m in indicator_matches:
        pre_committed.append({
            "indicator_id": m["id"],
            "tier": m["tier"],
            "posterior_effect": m["posterior_effect"],
            "status": m["current_status"],
        })

    # Build explanation
    explanation = _build_explanation(
        relevance, indicator_matches, watchpoint_matches,
        keyword_matches, rt_status, source_trust
    )

    # Check cross-topic dependency implications
    dep_implications = _check_dependency_implications(headline_lower, topic)

    return {
        "slug": slug,
        "title": title,
        "relevance": relevance,
        "action": action,
        "indicator_matches": indicator_matches,
        "watchpoint_matches": watchpoint_matches,
        "keyword_matches": keyword_matches,
        "rt_status": rt_status,
        "source_trust": source_trust,
        "pre_committed_effects": pre_committed,
        "dependency_implications": dep_implications,
        "explanation": explanation,
    }


def _match_indicators(headline_lower: str, headline_words: set,
                      topic: dict) -> list[dict]:
    """
    Match headline against all indicators in the topic.
    Uses keyword extraction from indicator descriptions.
    """
    matches = []
    tiers = topic.get("indicators", {}).get("tiers", {})

    for tier_key, indicators in tiers.items():
        for ind in indicators:
            desc = ind.get("desc", "").lower()
            ind_id = ind.get("id", "")

            # Extract significant words from indicator description
            desc_words = set(re.findall(r'\b\w{4,}\b', desc))
            # Remove common stopwords
            stopwords = {"must", "will", "that", "this", "with", "from",
                         "have", "been", "were", "their", "about", "would",
                         "could", "should", "into", "more", "than", "also",
                         "other", "some", "each", "which", "when", "where",
                         "what", "action", "signal", "observable", "verified",
                         "indicator", "near", "certain", "strong", "clear"}
            desc_words -= stopwords

            if not desc_words:
                continue

            # Score: fraction of indicator keywords found in headline
            overlap = headline_words & desc_words
            if len(desc_words) > 0:
                score = len(overlap) / len(desc_words)
            else:
                score = 0

            # Also check for key phrases (2+ word sequences)
            phrase_match = _check_phrase_match(headline_lower, desc)

            if score >= 0.35 or phrase_match:
                matches.append({
                    "id": ind_id,
                    "tier": tier_key,
                    "desc": ind.get("desc", ""),
                    "posterior_effect": ind.get("posteriorEffect", ""),
                    "current_status": ind.get("status", "NOT_FIRED"),
                    "match_score": round(score, 3),
                    "matched_words": sorted(overlap),
                    "phrase_match": phrase_match,
                })

    # Sort by match score descending
    matches.sort(key=lambda m: -m["match_score"])
    return matches


def _check_phrase_match(headline: str, description: str) -> bool:
    """Check if any significant 2-3 word phrase from the description appears in the headline."""
    desc_words = description.split()
    for i in range(len(desc_words) - 1):
        bigram = f"{desc_words[i]} {desc_words[i+1]}"
        # Only check meaningful bigrams (both words > 3 chars, not stopwords)
        words = bigram.split()
        if all(len(w) > 3 for w in words):
            clean = re.sub(r'[^\w\s]', '', bigram)
            if clean and clean in headline:
                return True
    return False


def _match_watchpoints(headline_lower: str, headline_words: set,
                       topic: dict) -> list[dict]:
    """Match headline against topic watchpoints."""
    matches = []
    for wp in topic.get("watchpoints", []):
        if isinstance(wp, str):
            event = wp.lower()
            watch = ""
        else:
            event = wp.get("event", "").lower()
            watch = wp.get("watch", "").lower()
        combined = f"{event} {watch}"

        event_words = set(re.findall(r'\b\w{4,}\b', combined))
        overlap = headline_words & event_words

        if len(overlap) >= 2:
            matches.append({
                "event": wp if isinstance(wp, str) else wp.get("event", ""),
                "watch": "" if isinstance(wp, str) else wp.get("watch", ""),
                "matched_words": sorted(overlap),
            })

    return matches


def _match_keywords(headline_lower: str, headline_words: set,
                    topic: dict) -> list[str]:
    """
    Check headline against topic-level keywords derived from:
    - topic title and question
    - hypothesis labels
    - actor names
    - search queries
    - data feed labels
    """
    keyword_sources = []

    # Title and question
    meta = topic.get("meta", {})
    keyword_sources.append(meta.get("title", ""))
    keyword_sources.append(meta.get("question", ""))

    # Hypothesis labels
    for h in topic.get("model", {}).get("hypotheses", {}).values():
        keyword_sources.append(h.get("label", ""))

    # Actor names
    actors = topic.get("actorModel", {}).get("actors", {})
    for actor in actors.values():
        keyword_sources.append(actor.get("name", ""))

    # Search queries
    for sq in topic.get("searchQueries", []):
        keyword_sources.append(sq)

    # Data feed labels
    for feed in topic.get("dataFeeds", {}).values():
        keyword_sources.append(feed.get("label", ""))

    # Extract all significant words
    all_kw = set()
    for src in keyword_sources:
        words = set(re.findall(r'\b\w{4,}\b', src.lower()))
        # Remove generic words
        generic = {"what", "will", "when", "does", "this", "that", "with",
                    "from", "have", "been", "their", "about", "would",
                    "could", "specific", "measurable", "rate", "price",
                    "current", "value", "units", "baseline", "status",
                    "active", "routine", "before", "after", "since",
                    "until", "during", "between", "latest", "today"}
        words -= generic
        all_kw.update(words)

    matched = sorted(headline_words & all_kw)
    return matched


def _get_rt_status(topic: dict) -> dict:
    """Get R_t regime from governance snapshot or compute it."""
    gov = topic.get("governance", {})
    if gov and "rt" in gov:
        return {
            "rt": gov["rt"].get("rt", 0),
            "regime": gov["rt"].get("regime", "UNKNOWN"),
            "worst_hypothesis": gov["rt"].get("worst_hypothesis"),
        }
    # Fall back to computing
    try:
        from governor import compute_topic_rt
        rt = compute_topic_rt(topic)
        return {
            "rt": rt.get("rt", 0),
            "regime": rt.get("regime", "UNKNOWN"),
            "worst_hypothesis": rt.get("worst_hypothesis"),
        }
    except (ImportError, Exception):
        return {"rt": 0, "regime": "UNKNOWN", "worst_hypothesis": None}


def _get_source_trust(source: str, topic: dict) -> dict:
    """Look up source trust from the topic's calibration or base priors."""
    if not source:
        return None

    # Check topic-level calibration
    cal = topic.get("sourceCalibration", {})
    effective = cal.get("effectiveTrust", {})
    if source in effective:
        return {"source": source, "trust": effective[source], "origin": "topic_calibration"}

    # Check source DB
    try:
        from framework.source_db import load_db
        db = load_db()
        src_profile = db.get("sources", {}).get(source)
        if src_profile and src_profile.get("effectiveTrust") is not None:
            return {"source": source, "trust": src_profile["effectiveTrust"],
                    "origin": "cross_topic_db"}
    except (ImportError, Exception):
        pass

    # Check static base priors
    try:
        from framework.calibrate import SOURCE_TRUST
        if source in SOURCE_TRUST:
            return {"source": source, "trust": SOURCE_TRUST[source],
                    "origin": "base_prior"}
    except (ImportError, Exception):
        pass

    return {"source": source, "trust": 0.5, "origin": "unknown_source"}


def _check_dependency_implications(headline_lower: str,
                                   topic: dict) -> list[dict]:
    """
    Check if this headline, by affecting this topic, would have
    downstream dependency implications.
    """
    try:
        from framework.dependencies import scan_downstream
        slug = topic.get("meta", {}).get("slug", "")
        downstream = scan_downstream(slug)
        if downstream:
            return [{
                "downstream_slug": d["slug"],
                "downstream_title": d.get("title", d["slug"]),
                "note": f"This topic is upstream of {d['slug']} — "
                        f"a posterior shift here may stale its assumptions",
            } for d in downstream]
    except (ImportError, Exception):
        pass
    return []


def _build_explanation(relevance: str, indicator_matches: list,
                       watchpoint_matches: list, keyword_matches: list,
                       rt_status: dict, source_trust: dict) -> str:
    """Build a one-paragraph human-readable explanation."""
    parts = []

    if relevance == "INDICATOR_MATCH":
        ind = indicator_matches[0]
        parts.append(
            f"Matches indicator {ind['id']} ({ind['tier'].replace('_', ' ')})"
        )
        if ind["current_status"] == "FIRED":
            parts.append("(already fired)")
        elif ind["current_status"] == "PARTIAL":
            parts.append("(partially confirmed — may complete)")
        else:
            parts.append(f"Pre-committed effect: {ind['posterior_effect']}")

    elif relevance == "TOPIC_RELEVANT":
        if watchpoint_matches:
            wp = watchpoint_matches[0]
            parts.append(f"Matches watchpoint: {wp['event']}")
        if keyword_matches:
            parts.append(f"Domain keywords: {', '.join(keyword_matches[:5])}")
        parts.append(
            "No pre-registered indicator match — review whether this "
            "warrants a new indicator or evidence log entry"
        )

    if rt_status and rt_status["regime"] in ("DANGEROUS", "RUNAWAY"):
        parts.append(
            f"R_t is {rt_status['regime']} — this topic is evidence-starved, "
            f"even tangential intel has value"
        )

    if source_trust:
        t = source_trust["trust"]
        if t >= 0.85:
            parts.append(f"Source trust: {t:.0%} (high)")
        elif t >= 0.5:
            parts.append(f"Source trust: {t:.0%} (moderate)")
        else:
            parts.append(f"Source trust: {t:.0%} (low — cross-reference before acting)")

    return ". ".join(parts) + "." if parts else "No relevant matches."


def _build_summary(headline: str, matches: list) -> str:
    """Build a one-line summary across all topic matches."""
    if not matches:
        return f"No active topics matched: \"{headline[:60]}...\""

    if len(matches) == 1:
        m = matches[0]
        return (f"{m['action']}: \"{headline[:50]}...\" → {m['slug']} "
                f"({m['relevance'].replace('_', ' ').lower()})")

    slugs = [m["slug"] for m in matches]
    top = matches[0]
    return (f"{top['action']}: \"{headline[:50]}...\" → "
            f"{len(matches)} topics ({', '.join(slugs[:3])})")


def _load_all_active() -> list[dict]:
    """Load all active topics from disk."""
    topics = []
    if not TOPICS_DIR.exists():
        return topics
    for path in TOPICS_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
            if t.get("meta", {}).get("status") == "ACTIVE":
                topics.append(t)
        except (json.JSONDecodeError, OSError):
            continue
    return topics
