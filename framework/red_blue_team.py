"""
red_blue_team — adversarial review for proposed indicator additions.

Two-team structured analytic technique:
  - Red team argues against the proposed indicator: P(E|¬H) is higher than
    LRs claim, counterexamples exist, confounders aren't accounted for
  - Blue team defends: similar past patterns held, no obvious counter-evidence,
    LRs are appropriately calibrated

Each team is dispatched as a fresh Claude Code subagent via the Agent tool
during the cleanup-indicator-sweep skill conversation. Fresh context per
team prevents the cross-context anchoring that pegged 17 topics.

The subagents have native access to Claude Code harness tools: Bash (to
invoke topic_search), WebSearch, WebFetch, Read. No external API key
required — execution runs through the harness.

This module is pure-Python helpers:
  - build_red_team_prompt / build_blue_team_prompt — construct the prompts
    the skill passes to the Agent tool
  - parse_team_response — extract structured JSON output from subagent reply
  - format_debate_envelope — assemble the final record stored with the
    proposal

The cleanup skill dispatches the agents and feeds their responses back here
for parsing.
"""

import json
import re
from typing import Optional


DEFAULT_BUDGET = {
    "max_turns": 8,
    "max_web_searches": 5,
    "max_web_fetches": 3,
    "max_topic_searches": 10,
}


def build_red_team_prompt(
    proposal: dict,
    topic_meta: dict,
    budget: dict = None,
) -> str:
    """
    Build a prompt for the red team subagent.

    Args:
        proposal: the proposed indicator addition. Must include id, desc,
                  posteriorEffect, likelihoods, optionally causal_event_id.
        topic_meta: topic context — {slug, title, question, hypotheses}
        budget: tool-call budget. Default is DEFAULT_BUDGET.

    Returns: a string prompt suitable for an Agent-tool subagent or direct
             Anthropic SDK call.
    """
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    hyps_text = "\n".join(
        f"  {k}: {h.get('label', '')} (current posterior: {h.get('posterior', '?')})"
        for k, h in topic_meta.get("hypotheses", {}).items()
    )
    likelihoods_text = json.dumps(proposal.get("likelihoods", {}), indent=2)

    return f"""You are the RED TEAM reviewer for a proposed indicator addition.
Your job: argue against this indicator's pre-committed likelihoods.

Specifically, you must investigate:
  1. P(E | ¬H) — the probability of seeing this evidence under hypotheses
     OTHER than the one the indicator favors. If P(E|¬H) is high, the LR
     ratio is wrong.
  2. Counterexamples — past patterns where similar evidence appeared but
     the predicted outcome did NOT occur.
  3. Confounders — alternative causes the LRs don't account for.
  4. Base rates — how common is this kind of evidence in general (not just
     when the favored hypothesis is true)?

==== TOPIC ====
Slug: {topic_meta.get("slug", "?")}
Title: {topic_meta.get("title", "?")}
Question: {topic_meta.get("question", "?")}
Hypotheses:
{hyps_text}

==== PROPOSED INDICATOR ====
ID: {proposal.get("id", "?")}
Description: {proposal.get("desc", "")}
Pre-committed posteriorEffect: {proposal.get("posteriorEffect", "")}
Pre-committed likelihoods (P(E|H_i)):
{likelihoods_text}
Causal event id: {proposal.get("causal_event_id", "(none)")}
Tier: {proposal.get("_tier", "?")}

==== TOOLS AVAILABLE ====
- topic_search(query): search project's accumulated evidence
  (evidenceLog, cold storage, source DB). Free, fast, high-signal.
  Use this FIRST.
  Budget: up to {budget['max_topic_searches']} calls.
- WebSearch(query): general web search via Claude Code harness.
  Budget: up to {budget['max_web_searches']} calls.
- WebFetch(url): fetch a specific page.
  Budget: up to {budget['max_web_fetches']} calls.
- Bash: you can run any python command including
  `python -c "from framework.topic_search import search_evidence; ..."`

==== BUDGET ====
You have {budget['max_turns']} reasoning turns. Conclude before exhausting them.

==== REQUIRED OUTPUT FORMAT ====
At the end of your investigation, output EXACTLY this JSON structure
inside a fenced code block, after your analysis:

```json
{{
  "verdict": "STRONG_OBJECTION" | "WEAK_OBJECTION" | "NO_OBJECTION",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "claims": [
    {{
      "claim": "<one-sentence objection>",
      "evidence": "<specific finding from search>",
      "source": "<topic_search:topic_slug:ev_id | web:url | analysis>"
    }}
  ],
  "p_e_given_not_h_assessment": "<your estimate of P(E|¬H) and reasoning>",
  "lr_recommendations": {{
    "<H_i>": "<your suggested LR if different from proposed, with reason>"
  }},
  "summary": "<2-3 sentence summary of the strongest objection>"
}}
```

Be specific. Cite findings. Don't generate plausible-sounding objections
without evidence — that's the failure mode this gate exists to prevent.
"""


def build_blue_team_prompt(
    proposal: dict,
    topic_meta: dict,
    budget: dict = None,
) -> str:
    """Build a prompt for the blue team subagent (defends the proposal)."""
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    hyps_text = "\n".join(
        f"  {k}: {h.get('label', '')} (current posterior: {h.get('posterior', '?')})"
        for k, h in topic_meta.get("hypotheses", {}).items()
    )
    likelihoods_text = json.dumps(proposal.get("likelihoods", {}), indent=2)

    return f"""You are the BLUE TEAM reviewer for a proposed indicator addition.
Your job: defend this indicator's pre-committed likelihoods with evidence.

Specifically, you must investigate:
  1. Supporting cases — past patterns where similar evidence appeared and
     the predicted outcome DID occur. Cite specifics.
  2. Mechanism — what's the causal pathway from this observable to the
     favored hypothesis? Is it plausible and well-grounded?
  3. LR magnitudes — are the proposed LRs appropriately calibrated, neither
     over-confident nor under-confident?

==== TOPIC ====
Slug: {topic_meta.get("slug", "?")}
Title: {topic_meta.get("title", "?")}
Question: {topic_meta.get("question", "?")}
Hypotheses:
{hyps_text}

==== PROPOSED INDICATOR ====
ID: {proposal.get("id", "?")}
Description: {proposal.get("desc", "")}
Pre-committed posteriorEffect: {proposal.get("posteriorEffect", "")}
Pre-committed likelihoods (P(E|H_i)):
{likelihoods_text}
Causal event id: {proposal.get("causal_event_id", "(none)")}
Tier: {proposal.get("_tier", "?")}

==== TOOLS AVAILABLE ====
- topic_search(query): project's accumulated evidence. Use FIRST.
  Budget: up to {budget['max_topic_searches']} calls.
- WebSearch(query): general web search.
  Budget: up to {budget['max_web_searches']} calls.
- WebFetch(url): fetch a specific page.
  Budget: up to {budget['max_web_fetches']} calls.
- Bash: run python topic_search if needed.

==== BUDGET ====
You have {budget['max_turns']} reasoning turns.

==== REQUIRED OUTPUT FORMAT ====
At the end of your investigation, output EXACTLY this JSON structure
inside a fenced code block, after your analysis:

```json
{{
  "verdict": "STRONG_DEFENSE" | "WEAK_DEFENSE" | "NO_DEFENSE",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "claims": [
    {{
      "claim": "<one-sentence supporting argument>",
      "evidence": "<specific finding from search>",
      "source": "<topic_search:... | web:... | analysis>"
    }}
  ],
  "lr_assessment": "<your view on LR magnitudes — too high, too low, well-calibrated>",
  "summary": "<2-3 sentence summary of the strongest defense>"
}}
```

Be specific. Don't generate plausible-sounding defenses without evidence.
"""


def parse_team_response(response_text: str) -> dict:
    """
    Extract the structured JSON output from a team's response text.

    Looks for ```json ... ``` fenced block and parses it.

    Returns: parsed dict, or {"_parse_error": "..."} if not parseable.
    """
    if not response_text:
        return {"_parse_error": "empty response"}

    # Find ```json ... ``` block (allow lowercase or omitted lang)
    pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    matches = re.findall(pattern, response_text, re.DOTALL)
    if not matches:
        # Fallback: try to find any JSON object with verdict
        pattern2 = r"\{[^{]*\"verdict\"[^}]*\}"
        matches = re.findall(pattern2, response_text, re.DOTALL)
    if not matches:
        return {"_parse_error": "no JSON block found", "_raw": response_text[:500]}

    # Try last match (in case agent printed examples earlier)
    for candidate in reversed(matches):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return {"_parse_error": "JSON unparseable", "_raw": response_text[:500]}


def format_debate_envelope(
    proposal: dict,
    red_report: dict,
    blue_report: dict,
    budget_used: Optional[dict] = None,
) -> dict:
    """
    Combine red + blue team outputs into a debate record stored with the
    proposal envelope. The operator sees this when reviewing.

    Returns:
        {
            proposal: <original proposal dict>,
            red_team: <parsed red team output>,
            blue_team: <parsed blue team output>,
            verdict_summary: "<aggregated read>",
            recommendation: "approve" | "revise" | "reject",
            budget_used: {topic_searches, web_searches, web_fetches},
        }
    """
    red_verdict = red_report.get("verdict", "?")
    blue_verdict = blue_report.get("verdict", "?")

    # Heuristic recommendation (operator can override)
    if red_verdict == "STRONG_OBJECTION":
        rec = "revise"
    elif red_verdict == "WEAK_OBJECTION" and blue_verdict in ("STRONG_DEFENSE",):
        rec = "approve"
    elif red_verdict == "NO_OBJECTION":
        rec = "approve"
    else:
        rec = "review"

    return {
        "proposal": proposal,
        "red_team": red_report,
        "blue_team": blue_report,
        "verdict_summary": f"red={red_verdict}, blue={blue_verdict}",
        "recommendation": rec,
        "budget_used": budget_used or {},
    }


def get_team_prompts(proposal: dict, topic_meta: dict, budget: dict = None) -> dict:
    """
    Convenience: return both team prompts in one call.

    The cleanup skill dispatches each via Claude Code's Agent tool:
        Agent(description="red team for <id>",
              prompt=prompts["red"], subagent_type="general-purpose")
        Agent(description="blue team for <id>",
              prompt=prompts["blue"], subagent_type="general-purpose")
    Then feeds each agent's response back to parse_team_response.
    """
    return {
        "red": build_red_team_prompt(proposal, topic_meta, budget),
        "blue": build_blue_team_prompt(proposal, topic_meta, budget),
    }
