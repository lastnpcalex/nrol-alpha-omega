"""
indicator_match_subagent — prompt construction + response parsing for the
subagent-based match decision used in process_headline.

The actual subagent dispatch happens in the calling skill (news-scan.md
or triage.md) via Claude Code's Agent tool. This module provides:

  - build_match_prompt(headline, source, topic_meta, indicators) -> str
  - parse_match_decision(response_text) -> dict

Each subagent invocation is fresh-context (anti-anchoring property).
The subagent reads the headline + topic indicator schema, outputs a
structured one-line decision: INDICATOR: <id> or PARK: <reason>.

Compared to embedding-based matching, the subagent catches:
  - negation flips ("Iran refused" vs "Iran proposed")
  - threshold specificity (numeric requirements not embedded in topic similarity)
  - causal direction (counterfactual mentions of opposite outcomes)
  - out-of-domain phrasing (the LLM has broad training)

These are the cases that pegged 17 topics — operators went freeform because
embeddings/keywords failed to surface the right indicator. Subagents fix
that without committing to freeform LRs.
"""

import json
import re
from typing import Optional


def _format_hypotheses(topic_meta: dict) -> str:
    hyps = topic_meta.get("hypotheses", {}) or {}
    lines = []
    for k, h in hyps.items():
        if isinstance(h, dict):
            lines.append(f"  {k}: {h.get('label', '')} (current posterior {h.get('posterior', '?')})")
        else:
            lines.append(f"  {k}: {h}")
    return "\n".join(lines) if lines else "  (none)"


def _format_indicators(indicators: list, max_chars: int = 200) -> str:
    """Format indicator list for the prompt. Truncate descriptions to max_chars."""
    out_lines = []
    for ind in indicators:
        if not isinstance(ind, dict):
            continue
        if not ind.get("id"):
            continue
        desc = (ind.get("desc", "") or "")[:max_chars]
        status = ind.get("status", "?")
        tier = ind.get("_tier", "?")
        likelihoods = ind.get("likelihoods")
        lr_summary = ""
        if likelihoods:
            lr_pairs = sorted(likelihoods.items())
            max_h = max(lr_pairs, key=lambda x: x[1])[0] if lr_pairs else "?"
            lr_summary = f" [favors {max_h}]"
        out_lines.append(
            f"  - id={ind['id']!r} | tier={tier} | status={status}{lr_summary}\n"
            f"    desc: {desc}"
        )
    return "\n".join(out_lines) if out_lines else "  (no indicators authored on this topic)"


def build_match_prompt(
    headline: str,
    source: str,
    topic_meta: dict,
    indicators: list,
    *,
    extra_context: Optional[str] = None,
) -> str:
    """
    Build a prompt for one (headline, topic) match-decision subagent.

    Args:
        headline: the news text
        source: source name
        topic_meta: {slug, title, question, hypotheses}
        indicators: list of indicator dicts (each with id, desc, status,
                    likelihoods, optional _tier).
                    Pass framework.indicator_match.collect_topic_indicators(topic).

    Returns: the prompt string. Caller dispatches via Agent tool.
    """
    return f"""You are deciding whether a single piece of evidence fires any of a topic's
existing indicators. Output a structured one-line decision.

==== EVIDENCE ====
Headline: {headline}
Source: {source}
{('Context: ' + extra_context) if extra_context else ''}

==== TOPIC ====
Slug: {topic_meta.get("slug", "?")}
Title: {topic_meta.get("title", "?")}
Question: {topic_meta.get("question", "?")}
Hypotheses:
{_format_hypotheses(topic_meta)}

==== INDICATOR SCHEMA ====
ONLY these indicators can fire. Do NOT invent new ones. If none match,
output PARK.

{_format_indicators(indicators)}

==== DECISION CRITERIA ====
For each candidate indicator, ask in order:
  1. Does the evidence describe the OBSERVABLE that the indicator's `desc`
     specifies? (Topic-level similarity is NOT enough — the specific
     observable must match.)
  2. Does the evidence meet the indicator's THRESHOLD? (e.g., if the
     indicator says "core CPI ≥ 0.5% MoM for 2 consecutive months" and
     the evidence says "core CPI 0.4%", the threshold is NOT met.)
  3. Is the evidence's directional implication CONSISTENT with the
     indicator's pre-committed LR direction? (Negation flips fail this.)

Fire only if all three are TRUE. Otherwise PARK.

If multiple indicators match: pick the one whose `desc` is most specific
to this evidence (tier3 > tier2 > tier1 when both apply, since tier3 is
more granular). If still tied, pick the one already FIRED (so decay
applies; prevents over-counting).

==== OUTPUT FORMAT ====
Return EXACTLY one line:

  INDICATOR: <indicator_id>

OR

  PARK: <one-sentence reason — what specifically fails to match>

No other output. No prose explanation. No code blocks.
"""


_DECISION_RE = re.compile(
    r"\b(INDICATOR|PARK)\s*:\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_match_decision(response_text: str) -> dict:
    """
    Parse subagent response into a structured decision.

    Returns:
        {
            "action": "fire" | "park" | "error",
            "indicator_id": str | None,
            "reason": str | None,
            "raw": <truncated response text>,
        }
    """
    if not response_text:
        return {"action": "error", "indicator_id": None,
                "reason": "empty response", "raw": ""}

    matches = _DECISION_RE.findall(response_text or "")
    if not matches:
        return {
            "action": "error",
            "indicator_id": None,
            "reason": "no INDICATOR: or PARK: line found",
            "raw": response_text[-300:],
        }

    # Take the LAST match (subagent might have shown an example earlier)
    keyword, value = matches[-1]
    keyword = keyword.upper()
    value = value.strip().strip(".,;:")

    if keyword == "INDICATOR":
        return {
            "action": "fire",
            "indicator_id": value,
            "reason": None,
            "raw": response_text[-300:],
        }
    else:  # PARK
        return {
            "action": "park",
            "indicator_id": None,
            "reason": value or "subagent decided no match",
            "raw": response_text[-300:],
        }
