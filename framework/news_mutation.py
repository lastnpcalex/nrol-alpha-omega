"""
news_mutation — multi-round subagent-mediated news scan helpers.

Architecture (see skills/news-scan.md):

  For each topic, each round R:
    Stage 1: Search subagents run in PARALLEL, fully isolated:
               - one per hypothesis (mandate: evidence updating H{i}
                 IN EITHER DIRECTION — bidirectional, not confirmation)
               - one wildcard (mandate: news bearing on the topic
                 question, hypothesis-agnostic)
             Each returns a structured ARTICLE list.
    Stage 2: For each unique article, dispatch a fresh match subagent
             (existing flow in framework.indicator_match_subagent +
             framework.pipeline). Decision: INDICATOR:<id> or PARK.
    Round-end: stop if no novel articles AND no indicator fires.

  Why this design is safe even with creative mutation:
    - bayesian_update() refuses non-indicator paths (engine gate)
    - causal_event_id de-correlates same-event indicators
    - duplicate text/informationChain rejection in add_evidence
    - pre-committed indicator LRs are immune to query-source bias
    - "either direction" framing per hypothesis prevents confirmation
      prompting; wildcard surfaces schema-gap signals separately
"""

import re
from typing import Optional


SEARCH_RESPONSE_FORMAT = """\
Format your response as one block per article. Output ONLY ARTICLE blocks
(no preamble, no postscript, no commentary):

ARTICLE
HEADLINE: <one line>
URL: <url>
SOURCE: <publication or wire>
DATE: <YYYY-MM-DD if known else unknown>
RELEVANCE: <one sentence: why this article bears on the search mandate>
END

If no articles meet the mandate, output exactly:
NO_ARTICLES
"""


def _format_topic_anchor(topic: dict) -> str:
    """Compact topic context line for search subagents."""
    meta = topic.get("meta", {}) or {}
    title = meta.get("title") or topic.get("slug") or "?"
    question = (
        meta.get("question")
        or meta.get("statement")
        or meta.get("description")
        or title
    )
    horizon = (
        meta.get("horizon")
        or meta.get("resolutionDate")
        or meta.get("endDate")
        or "unspecified"
    )
    return (
        f"Topic: {title}\n"
        f"Question: {question}\n"
        f"Resolution horizon: {horizon}"
    )


def _format_hypothesis(topic: dict, hypothesis_key: str) -> str:
    h = (topic.get("model", {}).get("hypotheses") or {}).get(hypothesis_key, {})
    if not isinstance(h, dict):
        return f"{hypothesis_key}: {h}"
    label = h.get("label", "")
    desc = h.get("description", "")
    posterior = h.get("posterior", "?")
    line = f"{hypothesis_key}: {label} (current posterior {posterior})"
    if desc:
        line += f"\n  Description: {desc}"
    return line


def _format_pre_committed_queries(topic: dict, limit: int = 20) -> str:
    queries = topic.get("searchQueries") or []
    if not queries:
        return "(no pre-committed queries on this topic)"
    qlines = "\n".join(f"  - {q}" for q in queries[:limit])
    return f"Topic's pre-committed search queries (use as starting points):\n{qlines}"


def _format_prior_articles(prior_articles: list, limit: int = 30) -> str:
    if not prior_articles:
        return ""
    lines = [
        "",
        "Articles already surfaced in earlier rounds — DO NOT re-find these. "
        "Read them for vocabulary clues, then search DIFFERENT angles:",
    ]
    for a in prior_articles[:limit]:
        h = (a.get("headline") or "").strip()[:140]
        url = (a.get("url") or "").strip()
        lines.append(f"  - {h} [{url}]")
    return "\n".join(lines)


def build_hypothesis_search_prompt(
    topic: dict,
    hypothesis_key: str,
    round_num: int,
    *,
    time_window: str = "last 12 hours",
    prior_articles: Optional[list] = None,
) -> str:
    """
    Per-hypothesis search subagent prompt.

    MANDATE IS BIDIRECTIONAL: find news that would update H{i} in either
    direction (supporting OR contradicting). This is the structural fix
    for confirmation-prompted search — the subagent isn't told to hunt
    for H{i}-confirming vocabulary, it's told to hunt for axis-relevant
    evidence. An article suggesting H{i} fails is in-scope, not just
    articles suggesting H{i} holds.
    """
    anchor = _format_topic_anchor(topic)
    hyp_text = _format_hypothesis(topic, hypothesis_key)

    if round_num <= 1:
        round_block = (
            f"This is round 1. Use the topic's pre-committed search queries "
            f"as starting points, but you may generate your own queries too. "
            f"Search the {time_window}.\n\n"
            + _format_pre_committed_queries(topic)
        )
    else:
        round_block = (
            f"This is round {round_num}. Earlier rounds already surfaced the "
            f"articles below. Generate NEW queries that explore DIFFERENT "
            f"vocabulary, angles, actors, or instruments. Search the "
            f"{time_window}."
            + _format_prior_articles(prior_articles or [])
        )

    return (
        "You are a search subagent for a Bayesian estimation framework. "
        "You operate fully isolated from other subagents.\n\n"
        f"{anchor}\n\n"
        "You are dedicated to ONE hypothesis on this topic:\n\n"
        f"{hyp_text}\n\n"
        "YOUR MANDATE: Find recent news that would update our probability "
        "estimate of this hypothesis IN EITHER DIRECTION — supporting "
        "evidence OR contradicting evidence. Both are in-scope. Do NOT "
        "bias toward only supporting articles.\n\n"
        f"{round_block}\n\n"
        "Use WebSearch. Issue 2-4 queries. For each promising hit, briefly "
        "check relevance. Return only articles you'd flag as worth "
        "processing through the indicator schema.\n\n"
        f"{SEARCH_RESPONSE_FORMAT}"
    )


def build_wildcard_search_prompt(
    topic: dict,
    round_num: int,
    *,
    time_window: str = "last 12 hours",
    prior_articles: Optional[list] = None,
) -> str:
    """
    Wildcard (hypothesis-agnostic) search subagent prompt.

    Catches developments that the per-hypothesis searchers might miss
    because the news doesn't fit any existing hypothesis frame. Articles
    surfaced ONLY by wildcard that match no indicator are the highest-
    value parked entries — they're schema-gap signals.
    """
    anchor = _format_topic_anchor(topic)

    if round_num <= 1:
        round_block = (
            f"This is round 1. Search the {time_window} for any recent news "
            f"bearing on this topic question. Do NOT filter by which "
            f"hypothesis the news supports — your job is breadth.\n\n"
            + _format_pre_committed_queries(topic)
        )
    else:
        round_block = (
            f"This is round {round_num}. Earlier rounds already surfaced the "
            f"articles below. Generate queries that explore DIFFERENT "
            f"vocabulary or angles — especially developments that don't fit "
            f"the obvious framing of this question. Search the "
            f"{time_window}."
            + _format_prior_articles(prior_articles or [])
        )

    return (
        "You are a wildcard search subagent for a Bayesian estimation "
        "framework. You operate fully isolated from other subagents.\n\n"
        f"{anchor}\n\n"
        "YOUR MANDATE: Find recent news bearing on the topic question. NO "
        "hypothesis filter — your job is hypothesis-agnostic breadth. You "
        "catch developments that the per-hypothesis searchers might miss "
        "because they don't fit any existing hypothesis frame.\n\n"
        f"{round_block}\n\n"
        "Use WebSearch. Issue 2-4 queries. Return any articles plausibly "
        "relevant to the topic question, even if you can't tell which "
        "hypothesis they update.\n\n"
        f"{SEARCH_RESPONSE_FORMAT}"
    )


_ARTICLE_SPLIT_RE = re.compile(r"\n\s*ARTICLE\s*\n", re.IGNORECASE)
_FIELD_RE = re.compile(
    r"^\s*(HEADLINE|URL|SOURCE|DATE|RELEVANCE)\s*:\s*(.*)$",
    re.IGNORECASE,
)


def parse_search_response(text: str) -> list[dict]:
    """
    Parse a search subagent's response into a structured article list.

    Tolerant of formatting variation. Returns [] for NO_ARTICLES, empty,
    or unparseable input.
    """
    if not text:
        return []
    stripped = text.strip()
    if not stripped:
        return []
    if "NO_ARTICLES" in stripped[:80].upper():
        return []

    blocks = _ARTICLE_SPLIT_RE.split("\n" + stripped)
    articles = []
    for block in blocks[1:]:  # skip preamble
        if "\nEND" in block:
            block = block.split("\nEND", 1)[0]
        elif re.search(r"^\s*END\s*$", block, re.MULTILINE):
            block = re.split(r"^\s*END\s*$", block, maxsplit=1, flags=re.MULTILINE)[0]
        article = {}
        for line in block.strip().splitlines():
            m = _FIELD_RE.match(line)
            if m:
                article[m.group(1).lower()] = m.group(2).strip()
        if article.get("headline"):
            articles.append({
                "headline": article.get("headline", ""),
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "date": article.get("date", ""),
                "relevance": article.get("relevance", ""),
            })
    return articles


def _article_key(article: dict) -> str:
    """Stable dedup key. Prefer URL; fall back to a normalized headline."""
    url = (article.get("url") or "").strip()
    if url:
        return f"url::{url}"
    headline = (article.get("headline") or "").strip().lower()
    headline = re.sub(r"\s+", " ", headline)[:160]
    return f"hl::{headline}"


def dedupe_articles(article_lists_by_channel: dict) -> tuple[list, dict]:
    """
    Dedupe articles surfaced across multiple channels (H1, H2, ..., wildcard).

    Args:
        article_lists_by_channel: {"H1": [...], "H2": [...], "wildcard": [...]}

    Returns:
        (deduped_articles, surfaced_via_map)
          deduped_articles: list of unique article dicts; each has a
            "surfaced_via" field listing channels that found it.
          surfaced_via_map: {dedup_key: [channel_names]}
    """
    seen = {}        # key -> article dict
    surfaced = {}    # key -> [channel names]
    for channel, articles in (article_lists_by_channel or {}).items():
        for a in articles or []:
            key = _article_key(a)
            if not key or key in ("url::", "hl::"):
                continue
            if key in seen:
                if channel not in surfaced[key]:
                    surfaced[key].append(channel)
            else:
                seen[key] = dict(a)
                surfaced[key] = [channel]
    deduped = []
    for key, a in seen.items():
        a["surfaced_via"] = list(surfaced[key])
        deduped.append(a)
    return deduped, surfaced


def filter_novel_articles(
    deduped_articles: list,
    prior_articles: list,
) -> list:
    """
    Drop articles already surfaced in earlier rounds.

    Used to compute "novel articles this round" for the stopping check.
    """
    prior_keys = {_article_key(a) for a in (prior_articles or [])}
    return [a for a in deduped_articles if _article_key(a) not in prior_keys]


def article_to_evidence_entry(
    article: dict,
    *,
    round_num: int,
    default_tag: str = "EVENT",
) -> dict:
    """
    Convert a search-surfaced article into a process_evidence entry dict.

    Stamps provenance fields:
      - surfaced_via: list of channels that found this article
      - scanRound: int round number
      - queryProvenance: short string tagging the search-stage origin

    These ride along in the evidence entry. add_evidence() preserves
    unrecognized fields, so they survive into evidenceLog for audit.
    """
    surfaced = list(article.get("surfaced_via") or [])
    if surfaced:
        prov = f"round {round_num} via {','.join(surfaced)}"
    else:
        prov = f"round {round_num}"
    headline = (article.get("headline") or "").strip()
    relevance = (article.get("relevance") or "").strip()
    text = headline if not relevance else f"{headline} — {relevance}"
    return {
        "tag": default_tag,
        "tags": [default_tag],
        "text": text,
        "source": (article.get("source") or "").strip() or "unknown",
        "url": (article.get("url") or "").strip(),
        "surfaced_via": surfaced,
        "scanRound": round_num,
        "queryProvenance": prov,
    }


def round_should_continue(
    *,
    round_num: int,
    max_rounds: int,
    novel_article_count: int,
    indicator_fire_count: int,
) -> tuple[bool, str]:
    """
    Decide whether to run another round.

    Continue when:
      - round_num < max_rounds, AND
      - this round produced novel articles, AND
      - either round_num == 1 (first round always counts even with 0 fires),
        OR there was at least one indicator fire (otherwise we're paying
        compute for parked entries — diminishing returns)

    Returns (continue: bool, reason: str).
    """
    if round_num >= max_rounds:
        return False, f"reached max_rounds={max_rounds}"
    if novel_article_count == 0:
        return False, "no novel articles surfaced this round"
    if round_num >= 2 and indicator_fire_count == 0:
        return False, "no indicator fires after round 1 — diminishing returns"
    return True, "continuing — novel articles present"


def _format_window_label(hours: float) -> str:
    """Human-friendly window label for embedding in search prompts.
    Subagents pass this to WebSearch; format affects readability, not
    semantics."""
    if hours < 48:
        n = max(1, round(hours))
        return f"last {n} hours"
    n = max(1, round(hours / 24))
    return f"last {n} days"


def compute_time_window(
    topic: dict,
    *,
    tempo_floor_hours: int = 12,
    buffer_hours: float = 1.0,
    max_lookback_days: int = 30,
) -> dict:
    """
    Compute the per-topic time window for the next news scan.

    The hardcoded "last 12 hours" default assumes you scan every 12h.
    If you scan every 24h, a 12h window misses half the news. If a topic
    hasn't been scanned in a week, a 12h window misses 6 days. This helper
    adapts the window to actual scan cadence.

    Logic:
      window_hours = clamp(
        elapsed_since_last_scan + buffer,
        floor   = tempo_floor_hours,
        ceiling = max_lookback_days * 24
      )

    Floor exists so high-frequency scanning (e.g. every minute) doesn't
    search a 60-second window — the tempo floor stays the lower bound.
    Ceiling exists so a months-stale topic doesn't pull thousands of
    results on resume; older news is usually already irrelevant or
    already-processed.

    Buffer accounts for clock skew + the gap between scan-start and
    lastScanned-stamp; dedup will eat any genuine overlap.

    Returns:
        {
          "hours":  float,    # window length used
          "label":  str,      # "last 18 hours" / "last 5 days" — for prompts
          "reason": str,      # why this length was picked (logged in report)
          "capped": bool,     # True if hit the max_lookback ceiling
        }
    """
    from datetime import datetime, timezone

    last_scanned = (topic.get("meta") or {}).get("lastScanned")
    now = datetime.now(timezone.utc)

    if not last_scanned:
        return {
            "hours": float(tempo_floor_hours),
            "label": _format_window_label(float(tempo_floor_hours)),
            "reason": f"first scan (no prior lastScanned) — using tempo floor {tempo_floor_hours}h",
            "capped": False,
        }

    try:
        ls_dt = datetime.fromisoformat(last_scanned.replace("Z", "+00:00"))
        if ls_dt.tzinfo is None:
            ls_dt = ls_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (now - ls_dt).total_seconds() / 3600.0
    except Exception:
        return {
            "hours": float(tempo_floor_hours),
            "label": _format_window_label(float(tempo_floor_hours)),
            "reason": f"could not parse lastScanned={last_scanned!r}; using floor {tempo_floor_hours}h",
            "capped": False,
        }

    adaptive = elapsed_hours + buffer_hours
    ceiling = max_lookback_days * 24

    if adaptive < tempo_floor_hours:
        return {
            "hours": float(tempo_floor_hours),
            "label": _format_window_label(float(tempo_floor_hours)),
            "reason": (
                f"last scan {elapsed_hours:.1f}h ago, under tempo floor — "
                f"using floor {tempo_floor_hours}h"
            ),
            "capped": False,
        }

    if adaptive > ceiling:
        return {
            "hours": float(ceiling),
            "label": _format_window_label(float(ceiling)),
            "reason": (
                f"last scan {elapsed_hours/24:.1f}d ago — capped at "
                f"max_lookback {max_lookback_days}d (older news likely irrelevant or processed)"
            ),
            "capped": True,
        }

    return {
        "hours": adaptive,
        "label": _format_window_label(adaptive),
        "reason": (
            f"adaptive: {elapsed_hours:.1f}h since last scan + {buffer_hours}h buffer"
        ),
        "capped": False,
    }


def stamp_last_scanned(slug: str, time_iso: str = None) -> str:
    """
    Update topic.meta.lastScanned to time_iso (default: now).

    Always call this at scan completion — even when zero articles were
    found — so the next scan has an accurate lower bound. Without this
    stamp, a topic that yielded "no news" looks like a long gap on the
    next scan and triggers a wasteful wide-window search.

    Returns the timestamp written.
    """
    import sys
    from pathlib import Path
    from datetime import datetime, timezone

    repo = str(Path(__file__).parent.parent)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from engine import load_topic, save_topic

    if time_iso is None:
        time_iso = datetime.now(timezone.utc).isoformat()
    topic = load_topic(slug)
    topic.setdefault("meta", {})["lastScanned"] = time_iso
    save_topic(topic)
    return time_iso


def budget_for_scan(num_topics: int) -> int:
    """
    Recommended max_rounds per topic based on scan breadth.

      single-topic deep scan : 3 rounds (mutation pays off)
      small sweep (2-5)      : 2 rounds (round 1 + one mutation pass)
      large sweep (>5)       : 1 round  (pre-committed only; cost-bounded)

    Skill may override per operator preference.
    """
    if num_topics <= 1:
        return 3
    if num_topics <= 5:
        return 2
    return 1
