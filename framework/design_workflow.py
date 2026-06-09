"""
design_workflow — orchestration helpers for topic-design v2.

Provides prompt builders + parsers for the adversarial review steps that
run inside the topic-design skill. Subagent dispatch happens in the skill
conversation via the Agent tool (fresh context per dispatch — anti-anchoring).

Where adversarial review fires (per epistemic-leverage analysis):

  Phase 2 (priors)         — RED/BLUE per topic. Priors are the
                             documented failure mode. High leverage.
  Phase 3 (indicators)     — Per-indicator shape review (resolution-
                             disguise check) via lint_indicator_shape +
                             SET-LEVEL red/blue per topic (catches
                             compound/cluster patterns one-pass).
  Phase 4 (actor model)    — Operator review only. Adversarial review
                             produces list-padding here, not insight.
  Phase 5 (data feeds)     — Lint (each indicator has a feed) + operator.
                             Operational, not epistemic.
  Final design gate        — generate_review_prompt from
                             topic_design_gate.py (whole-topic
                             integration check).

Revision loop: each phase has a max of 3 operator-driven revision
iterations before the topic is either accepted or killed. State tracked
in governance.design_workflow.

Multi-topic parallel: prompt builders are pure functions; the skill
dispatches batches across topics simultaneously and parses responses.
"""

import json
import re
from typing import Optional


MAX_REVISION_LOOPS = 3
DEFAULT_BUDGET = {
    "max_turns": 6,
    "max_web_searches": 3,
    "max_topic_searches": 5,
}


# ============================================================================
# Phase 2: priors review (red/blue per topic)
# ============================================================================

def _format_hypotheses_with_priors(hypotheses: dict) -> str:
    out = []
    for k, h in hypotheses.items():
        if isinstance(h, dict):
            out.append(
                f"  {k}: {h.get('label', '')} | midpoint={h.get('midpoint', '?')} "
                f"{h.get('unit', '')} | prior={h.get('posterior', '?')}"
            )
        else:
            out.append(f"  {k}: {h}")
    return "\n".join(out) if out else "  (none)"


def build_priors_red_team_prompt(topic_draft: dict, budget: dict = None) -> str:
    """
    Red team prompt for Phase 2 priors review.

    The red team's job: argue the priors are anchored, miscalibrated,
    or asymmetric. Specifically:
      - Are the priors anchored to recent salient events / availability bias?
      - Is the prior distribution shape (e.g., flat / single-peaked / skewed)
        appropriate to the underlying uncertainty, or does it pretend to
        know more / less than the operator actually does?
      - Are adjacent hypotheses' priors monotonic when they shouldn't be,
        or non-monotonic when they should be?
      - Does the prior on the modal hypothesis exceed what base rates
        would suggest?
    """
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    meta = topic_draft.get("meta", {})
    hyps = topic_draft.get("model", {}).get("hypotheses", {})

    return f"""You are the RED TEAM reviewer for the PRIORS of a Bayesian forecasting topic.
Your job: argue that these priors are miscalibrated.

Specifically, investigate:
  1. ANCHORING — Are the priors anchored to recent salient events,
     availability bias, or operator narrative? Compare to base rates
     for similar questions in similar domains over similar horizons.
  2. ASYMMETRY — Is the distribution shape (peaked / flat / skewed)
     appropriate? Or does it claim more/less knowledge than is warranted?
  3. MODAL DOMINANCE — Does the prior on the most-favored hypothesis
     exceed what base rates would justify at this stage of the question?
  4. TAIL UNDER-WEIGHTING — Are extreme outcomes (H1 or H_max) given
     enough mass? In high-uncertainty domains, priors that put <5% on
     a tail often understate genuine uncertainty.
  5. JUSTIFICATIONS — Do the operator's stated justifications for each
     prior actually support the number, or are they post-hoc?

==== TOPIC ====
Slug: {meta.get("slug", "?")}
Title: {meta.get("title", "?")}
Question: {meta.get("question", "?")}
Resolution: {meta.get("resolution", "?")}
Resolution date: {meta.get("resolutionDate", "?")}

==== HYPOTHESES + PRIORS ====
{_format_hypotheses_with_priors(hyps)}

Operator justifications (if provided, in topic.meta.priorJustifications):
{json.dumps(meta.get("priorJustifications", {}), indent=2)}

==== TOOLS ====
- WebSearch (budget: {budget['max_web_searches']} calls) — base rates,
  comparable historical questions, expert prior estimates
- topic_search (budget: {budget['max_topic_searches']} calls) — prior
  evidence on adjacent topics in this project

==== OUTPUT FORMAT ====
Investigate the priors. Return a structured response:

OBJECTION 1: <one specific concern about a specific hypothesis's prior>
EVIDENCE: <what you found that supports the objection>
SUGGESTED REVISION: <new prior value or distribution shape with brief justification>

OBJECTION 2: ...
(Up to 4 objections. If priors look defensible, output: NO_OBJECTIONS: <one-line reason>)

VERDICT: WEAK_PRIORS | DEFENSIBLE_PRIORS | STRONG_PRIORS
SUMMARY: <one sentence overall>
"""


def build_priors_blue_team_prompt(topic_draft: dict, red_team_response: str,
                                   budget: dict = None) -> str:
    """
    Blue team prompt for Phase 2 priors review — defends against red team.
    """
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    meta = topic_draft.get("meta", {})
    hyps = topic_draft.get("model", {}).get("hypotheses", {})

    return f"""You are the BLUE TEAM reviewer for the PRIORS of a Bayesian forecasting topic.
The red team has raised objections. Your job: respond to each, either by
defending the prior as-is OR conceding and proposing a more conservative
revision.

You are NOT a sycophant. If a red team objection is correct, concede it.
If it's wrong (red team itself anchored on a different bias, mistook the
question, or the suggested revision is worse), defend the original.

==== TOPIC ====
Slug: {meta.get("slug", "?")}
Question: {meta.get("question", "?")}
Hypotheses + priors:
{_format_hypotheses_with_priors(hyps)}

==== RED TEAM RESPONSE ====
{red_team_response}

==== TOOLS ====
- WebSearch (budget: {budget['max_web_searches']} calls)
- topic_search (budget: {budget['max_topic_searches']} calls)

==== OUTPUT FORMAT ====
For each red-team OBJECTION, respond:

RESPONSE TO OBJECTION 1:
ACTION: DEFEND | CONCEDE | PARTIAL_CONCEDE
REASONING: <why>
COUNTER-EVIDENCE (if defending): <what supports the original prior>
PROPOSED REVISION (if conceding): <new value>

RESPONSE TO OBJECTION 2: ...

OVERALL VERDICT: PRIORS_OK_AS_DRAFTED | PRIORS_NEED_REVISION | PRIORS_NEED_MAJOR_REWORK
SUMMARY: <one sentence>
"""


# ============================================================================
# Phase 3: indicator-SET review (one debate per topic, not per indicator)
# ============================================================================

def _format_indicator_summary(indicators: list, max_inds: int = 50) -> str:
    out = []
    for i, ind in enumerate(indicators[:max_inds]):
        if not isinstance(ind, dict):
            continue
        lr = ind.get("likelihoods", {})
        max_h = max(lr.items(), key=lambda kv: kv[1])[0] if lr else "?"
        out.append(
            f"  {i+1}. id={ind.get('id', '?')} | tier={ind.get('_tier', ind.get('tier', '?'))} "
            f"| shape={ind.get('shape', '?')} | favors={max_h} "
            f"| desc: {(ind.get('desc', '') or '')[:100]}"
        )
    if len(indicators) > max_inds:
        out.append(f"  ... ({len(indicators) - max_inds} more omitted)")
    return "\n".join(out) if out else "  (no indicators)"


def build_indicator_set_red_team_prompt(topic_draft: dict, budget: dict = None) -> str:
    """
    Red team prompt for Phase 3 indicator-SET review.

    Reviews the WHOLE indicator suite at once, not per indicator. Catches:
      - Compound projection (multiple indicators favoring same H without
        independence)
      - Cluster suspicion (suspiciously similar LRs across cluster of inds)
      - Direction drift (anti-indicators that aren't actually anti)
      - Coverage gaps (hypotheses with no negative-direction indicators)
      - Recycled-intel risk (indicators whose data sources overlap heavily)

    Plus per-indicator concerns surfaced as specific callouts.
    """
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    meta = topic_draft.get("meta", {})
    hyps = topic_draft.get("model", {}).get("hypotheses", {})
    inds_block = topic_draft.get("indicators", {})

    all_indicators = []
    for tier_name in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in inds_block.get("tiers", {}).get(tier_name, []) or []:
            d = dict(ind)
            d["_tier"] = tier_name
            all_indicators.append(d)
    for ind in inds_block.get("anti_indicators", []) or []:
        d = dict(ind)
        d["_tier"] = "anti"
        all_indicators.append(d)

    return f"""You are the RED TEAM reviewer for the INDICATOR SUITE of a Bayesian topic.
Your job: argue this indicator set is poorly designed AS A WHOLE.

Set-level failure modes to investigate:
  1. COMPOUND PROJECTION — Do many indicators favor the same hypothesis
     without independence? If so, firing them all would over-update.
  2. CLUSTER SUSPICION — Are several indicators describing variations of
     the same observable with suspiciously similar LRs?
  3. CAUSAL OVERLAP — Do indicators share underlying events without
     declaring causal_event_id? If so, de-correlation will fail.
  4. DIRECTION DRIFT — Do anti-indicators actually move the H they target
     in the OPPOSITE direction, or do their LRs look nearly identical to
     pro-indicators (failure to be genuinely adversarial)?
  5. COVERAGE GAPS — Is there a hypothesis with only positive indicators
     (can only gain probability) or only negative (can only lose)?
  6. RECYCLED INTEL — Do multiple indicators rely on the same primary
     source or wire feed, creating false independence?
  7. RESOLUTION DISGUISE (per-indicator spot check) — Are any
     indicators actually resolution events disguised as evidence?

Plus surface per-indicator concerns where they exist.

==== TOPIC ====
Slug: {meta.get("slug", "?")}
Question: {meta.get("question", "?")}
Hypotheses:
{_format_hypotheses_with_priors(hyps)}

==== INDICATOR SUITE ({len(all_indicators)} total) ====
{_format_indicator_summary(all_indicators)}

==== TOOLS ====
- topic_search (budget: {budget['max_topic_searches']} calls)
- WebSearch (budget: {budget['max_web_searches']} calls)

==== OUTPUT FORMAT ====
SET_OBJECTION 1: <set-level concern, e.g. "5 of 8 indicators favor H3 share an event_id_root">
SUGGESTED FIX: <how to address: merge / split / add anti / declare causal_event_id>

SET_OBJECTION 2: ...

PER_INDICATOR_CALLOUT: <indicator_id> — <specific concern>
(zero or more of these)

COVERAGE_GAPS: <list of hypotheses with asymmetric coverage, or NONE>

OVERALL VERDICT: SET_NEEDS_REWORK | SET_NEEDS_REVISION | SET_OK
SUMMARY: <one sentence>
"""


def build_indicator_set_blue_team_prompt(topic_draft: dict,
                                          red_team_response: str,
                                          budget: dict = None) -> str:
    """Blue team prompt — defends the indicator suite against set-level red team."""
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    meta = topic_draft.get("meta", {})

    return f"""You are the BLUE TEAM reviewer defending an indicator suite against red-team objections.

You are NOT a sycophant. Concede where red is right; defend where the
original design holds up. Address EACH red-team objection.

==== TOPIC ====
Slug: {meta.get("slug", "?")}
Question: {meta.get("question", "?")}

==== RED TEAM RESPONSE ====
{red_team_response}

==== TOOLS ====
- topic_search (budget: {budget['max_topic_searches']} calls)
- WebSearch (budget: {budget['max_web_searches']} calls)

==== OUTPUT FORMAT ====
RESPONSE TO SET_OBJECTION 1:
ACTION: DEFEND | CONCEDE | PARTIAL_CONCEDE
REASONING: <why>
PROPOSED FIX (if conceding): <change>

(continue for each red objection and per-indicator callout)

OVERALL VERDICT: SUITE_OK_AS_DRAFTED | SUITE_NEEDS_REVISION | SUITE_NEEDS_REWORK
SUMMARY: <one sentence>
"""


# ============================================================================
# Response parsers
# ============================================================================

_VERDICT_RE = re.compile(
    r"(?:OVERALL\s+)?VERDICT\s*:\s*(\w[\w_]*)",
    re.IGNORECASE | re.MULTILINE,
)


def parse_review_response(text: str, expected_verdicts: list = None) -> dict:
    """
    Generic parser for red/blue team responses. Pulls the OVERALL VERDICT
    line and the SUMMARY line. Returns the raw text for the operator to
    inspect.

    Args:
        text: subagent response text
        expected_verdicts: list of valid verdict strings (uppercase). If
                           the parsed verdict isn't in this list, marks
                           as 'unknown' but doesn't error.

    Returns: {"verdict": str, "summary": str, "raw": str (truncated)}
    """
    if not text:
        return {"verdict": "error", "summary": "empty response", "raw": ""}

    matches = _VERDICT_RE.findall(text)
    verdict = matches[-1].upper() if matches else "UNKNOWN"
    if expected_verdicts and verdict not in [v.upper() for v in expected_verdicts]:
        verdict_status = f"unrecognized ({verdict})"
    else:
        verdict_status = verdict

    summary_m = re.search(r"SUMMARY\s*:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    summary = summary_m.group(1).strip() if summary_m else "(no SUMMARY line)"

    return {
        "verdict": verdict_status,
        "summary": summary,
        "raw": text[-2000:],
    }


# ============================================================================
# Debate envelope formatter
# ============================================================================

def format_debate_envelope(phase: str, topic_slug: str, draft_summary: str,
                            red_response: str, blue_response: str,
                            iteration: int = 1) -> dict:
    """
    Compose the final review record for an operator gate.

    Returns dict ready to write to topic.governance.design_workflow.<phase>.
    """
    red_parsed = parse_review_response(red_response)
    blue_parsed = parse_review_response(blue_response)

    return {
        "phase": phase,
        "topic_slug": topic_slug,
        "iteration": iteration,
        "draft_summary": draft_summary,
        "red_team": red_parsed,
        "blue_team": blue_parsed,
        "operator_decision": None,  # filled in by operator: APPROVE / REVISE / KILL
        "operator_notes": None,
    }


# ============================================================================
# Revision-loop tracking
# ============================================================================

def get_revision_count(topic: dict, phase: str) -> int:
    """How many revision iterations has this topic been through for `phase`?"""
    return (topic.get("governance", {})
                 .get("design_workflow", {})
                 .get(phase, {})
                 .get("iteration", 0))


def can_revise(topic: dict, phase: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason). False if topic has hit MAX_REVISION_LOOPS
    on this phase — operator must accept current draft or kill the topic.
    """
    n = get_revision_count(topic, phase)
    if n >= MAX_REVISION_LOOPS:
        return False, (
            f"Topic has hit max revision loops ({MAX_REVISION_LOOPS}) on "
            f"phase {phase!r}. Operator must accept current draft or kill the topic."
        )
    return True, f"{n}/{MAX_REVISION_LOOPS} revisions used on {phase!r}"


def record_phase_envelope(topic: dict, phase: str, envelope: dict) -> dict:
    """Stamp envelope into topic.governance.design_workflow.<phase>. Mutates and returns topic."""
    gov = topic.setdefault("governance", {})
    workflow = gov.setdefault("design_workflow", {})
    workflow[phase] = envelope
    return topic


# ============================================================================
# Multi-topic batch helpers
# ============================================================================

def build_phase2_batch_prompts(topic_drafts: dict[str, dict]) -> dict[str, dict]:
    """
    For multi-topic Phase 2 review:
    Returns {slug: {"red_prompt": str, "blue_prompt_template": str}}

    Blue prompt is a template — caller must fill in red team response after
    red team subagents return. Use build_priors_blue_team_prompt(draft, red_resp).
    """
    out = {}
    for slug, draft in topic_drafts.items():
        out[slug] = {
            "red_prompt": build_priors_red_team_prompt(draft),
            "blue_prompt_builder": lambda d=draft: lambda red_resp:
                build_priors_blue_team_prompt(d, red_resp),
        }
    return out


def build_phase3_batch_prompts(topic_drafts: dict[str, dict]) -> dict[str, dict]:
    """For multi-topic Phase 3 indicator-set review."""
    out = {}
    for slug, draft in topic_drafts.items():
        out[slug] = {
            "red_prompt": build_indicator_set_red_team_prompt(draft),
            "blue_prompt_builder": lambda d=draft: lambda red_resp:
                build_indicator_set_blue_team_prompt(d, red_resp),
        }
    return out
