"""
News-observation pipeline: builds prompts for the OBSERVE-aware matcher and
the 3-stage debate (advocate / rebut / jury), parses their outputs, and
applies decisions through the engine.

This generalizes what was hormuz-specific in the proof step. Used by:
  - skills/news-scan.md (any topic, any scan)
  - skills/topic-design.md (Phase 3.5 news-flow validation, when added)

All decisions route through engine entry points:
  OBSERVE -> framework.pipeline.apply_observation
  FIRE    -> framework.pipeline.process_evidence(fired_indicator_id=...)
  PARK    -> framework.pipeline.process_evidence(fired_indicator_id=None)
  IGNORE  -> skip

No engine gate is bypassed. Existing clamps, decorrelation, evidence_refs,
calibrationStatus, lens, lr_decay, and confidence_inflation gates apply
unchanged.
"""

import json
import re
from pathlib import Path

from engine import load_topic
from framework.pipeline import apply_observation, process_evidence, log_activity
from framework.news_mutation import article_to_evidence_entry, stamp_last_scanned
from framework.indicator_schema import iter_indicators_for_topic


def _to_int_idx(idx) -> int:
    if isinstance(idx, (int, float)):
        return int(idx)
    try:
        if isinstance(idx, str) and "." in idx:
            return int(float(idx))
        return int(idx)
    except (ValueError, TypeError):
        return 0


def _sort_key(idx):
    if isinstance(idx, (int, float)):
        return (int(idx), 0.0)
    if isinstance(idx, str):
        if "." in idx:
            parts = idx.split(".")
            try:
                return (int(parts[0]), float(f"0.{parts[1]}"))
            except ValueError:
                return (0, 0.0)
        try:
            return (int(idx), 0.0)
        except ValueError:
            return (0, 0.0)
    return (0, 0.0)


def walk_indicators(topic: dict) -> list:
    """Return a flat list of every indicator dict in the topic schema."""
    return [
        ind for _tier, ind in iter_indicators_for_topic(topic)
        if isinstance(ind, dict) and "id" in ind and "likelihoods" in ind
    ]


# Full system purpose framing — included in every agent's standing instructions.
# This is the SHARED context every subagent gets so they can recognize when their
# specific task is producing output that violates the system's actual goal,
# not just the constraints in their narrow prompt.
SYSTEM_PURPOSE_FRAMING = """=== SYSTEM PURPOSE (READ FIRST) ===

You are part of the NROL-AO Bayesian framework. Its purpose:

1. TRACK calibrated, auditable probabilistic beliefs over uncertain questions
   — news, current events, "the current thing." Each topic has hypotheses
   with priors that move toward whichever hypothesis the evidence supports
   as new evidence arrives.

2. THE SYSTEM IS HUMAN-AI COLLABORATION. LLM strengths (Bayesian math, code,
   structured extraction at scale) are paired with LLM weaknesses (missing
   the spirit of things, confusing constraints with purposes, accepting
   technically-clean inferences that produce wrong-direction updates) via
   MECHANISTIC PATCHES — pre-committed indicators, pre-committed LRs,
   gates, this debate structure. The patches exist because LLM judgment
   alone is insufficient; structural decisions get flagged for operator
   review rather than absorbed silently.

3. PRE-COMMITMENT IS A CONSTRAINT, NOT THE PURPOSE. Pre-committed indicators
   prevent context-anchored vibes-LR (the failure mode that pegged 17
   topics at clamp ceilings). But pre-commitment is in service of the
   purpose: posteriors that move correctly toward whichever hypothesis the
   evidence supports. An update that respects pre-commitment but pushes
   posteriors in a direction OPPOSITE to what the article actually
   supports is a system failure — the constraint is intact but the
   purpose is violated.

4. DIRECTIONAL ALIGNMENT CHECK. For any proposed update:
   - First identify the article's directional content: does this article
     report evidence that, intuitively, supports faster reopen / slower
     reopen / no reopen / etc? (Use the topic's hypotheses to frame.)
   - Then check whether the proposed update (via the chosen indicator's
     LR vector) pushes posterior in a CONSISTENT direction.
   - If they don't align — for example, recovery news being routed
     through an indicator whose LR vector favors the no-recovery
     hypothesis — that is NOT just a missed update, it is a
     wrong-direction update. Refuse it. Flag SCHEMA_GAP.

5. SCHEMA_GAP IS A LEGITIMATE OUTCOME. If an article reports evidence in
   a direction the schema has no observable for, the correct outcome is
   not "force-fit it into the closest available observable" — it is
   "flag this as a schema gap for the operator." The schema being
   incomplete is something the operator can fix; a wrong-direction
   update polluting posterior history is harder to undo.

=== END SYSTEM PURPOSE ===

"""


# ------------------------ PROMPT BUILDERS ----------------------------

def build_matcher_prompt(topic: dict, articles: list) -> str:
    """
    Build the OBSERVE-aware matcher prompt for one topic + its surfaced
    articles. Indicators with `observable` blocks accept OBSERVE actions;
    others remain binary (FIRE/PARK).
    """
    slug = topic["meta"]["slug"]
    inds = walk_indicators(topic)
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: evidence-extraction matcher.")
    lines.append("For each article, decide ONE of: OBSERVE / FIRE / PARK / IGNORE / SCHEMA_GAP.")
    lines.append("")
    lines.append("Apply the directional alignment check (see SYSTEM PURPOSE above) to every")
    lines.append("OBSERVE/FIRE proposal. If an article reports evidence whose direction is")
    lines.append("not covered by any observable in the schema, return SCHEMA_GAP rather than")
    lines.append("force-fitting it into a wrong-direction observable.")
    lines.append("")
    lines.append("## Topic")
    lines.append(slug)
    lines.append(f"Question: {topic['meta'].get('question', '')}")
    lines.append("")
    lines.append("## Hypotheses (current posteriors)")
    for hk, hv in topic["model"]["hypotheses"].items():
        label = hv.get("label") or hv.get("desc") or hv.get("description") or "?"
        lines.append(f"  {hk}: {label[:120]} — posterior {hv.get('posterior')}")
    lines.append("")
    lines.append("## Indicators")
    lines.append("")
    lines.append("Indicators with an `observable` block accept OBSERVE actions: extract a")
    lines.append("numeric value matching the indicator's metric, in the metric's native units")
    lines.append("(MATCH the units of threshold_value and baseline). The engine mechanically")
    lines.append("derives a partial-strength LR.")
    lines.append("")
    lines.append("Indicators without `observable` are binary: FIRE only when the literal")
    lines.append("threshold language in `desc` is met. Otherwise PARK.")
    lines.append("")
    for ind in inds:
        lines.append(f"### {ind['id']}")
        lines.append(f"  desc: {ind['desc']}")
        lines.append(f"  status: {ind.get('status', '?')}")
        lines.append(f"  committed_LR: {ind['likelihoods']}")
        if "observable" in ind:
            ob = ind["observable"]
            lines.append("  OBSERVABLE:")
            lines.append(f"    metric: {ob['metric']}")
            lines.append(f"    family: {ob['family']}")
            lines.append(f"    threshold_value: {ob['threshold_value']}")
            lines.append(f"    baseline: {ob['baseline']}")
            lines.append(f"    direction: {ob['direction']}")
            if ob.get("_note"):
                lines.append(f"    _note: {ob['_note']}")
        else:
            lines.append("  observable: NONE (binary; FIRE only on literal threshold match)")
        lines.append("")
    lines.append("## Articles to evaluate")
    lines.append("")
    for i, item in enumerate(articles, start=1):
        art = item.get("article", item)
        lines.append(f"[A{i}] {art.get('headline', '')}")
        lines.append(f"  URL: {art.get('url', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  DATE: {art.get('date', '')}")
        lines.append(f"  CHANNELS: {item.get('channels', [])}")
        if art.get("relevance"):
            lines.append(f"  RELEVANCE: {art.get('relevance', '')}")
        if art.get("excerpt"):
            # Extracted article body (bounded upstream). Snippets alone rarely
            # carry the numeric values OBSERVE needs — the excerpt is where
            # threshold data actually lives.
            lines.append(f"  EXCERPT: {art.get('excerpt', '')}")
        
        # Candidate list selection
        matched_strict = []
        matched_soft = []
        matched_anti = []
        
        art_body = (art.get("headline", "") + " " + art.get("excerpt", "") + " " + art.get("relevance", "")).lower()
        stop_words = {"the", "and", "for", "with", "this", "that", "are", "was", "were", "has", "had", "been", "from", "its", "not", "but", "who", "they", "will"}
        
        for tier, ind in iter_indicators_for_topic(topic):
            id_parts = re.split(r"[-_\s]", ind.get("id", "").lower())
            desc_words = re.findall(r"\b\w{3,}\b", ind.get("desc", "").lower())
            search_terms = set(id_parts + desc_words) - stop_words
            
            is_match = False
            for term in search_terms:
                if len(term) >= 3 and term in art_body:
                    is_match = True
                    break
            
            if is_match:
                if tier == "anti_indicators":
                    matched_anti.append(ind)
                elif "observable" in ind:
                    matched_soft.append(ind)
                else:
                    matched_strict.append(ind)
                    
        # Fallbacks to list all if no keywords matched (to ensure completeness)
        all_strict = []
        all_soft = []
        all_anti = []
        for tier, ind in iter_indicators_for_topic(topic):
            if tier == "anti_indicators":
                all_anti.append(ind)
            elif "observable" in ind:
                all_soft.append(ind)
            else:
                all_strict.append(ind)
                
        display_strict = matched_strict if matched_strict else all_strict
        display_soft = matched_soft if matched_soft else all_soft
        display_anti = matched_anti if matched_anti else all_anti
        
        lines.append("  CANDIDATE INDICATORS FOR THIS ARTICLE:")
        lines.append("    * Strict Indicators:")
        for ind in display_strict:
            lines.append(f"      - {ind['id']}: {ind['desc']}")
        lines.append("    * Soft Observables:")
        for ind in display_soft:
            ob = ind["observable"]
            lines.append(f"      - {ind['id']}: {ind['desc']} (metric: {ob['metric']}, threshold: {ob['threshold_value']}, baseline: {ob['baseline']}, direction: {ob['direction']})")
        lines.append("    * Anti-Indicators:")
        for ind in display_anti:
            lines.append(f"      - {ind['id']}: {ind['desc']}")
        lines.append("    * Unmatched Directional Evidence:")
        lines.append("      - If the article contains real evidence that does not fit any indicator above, recommend SCHEMA_GAP with a description of the missing indicator direction.")
        lines.append("")
    lines.append("## TASK")
    lines.append("")
    lines.append("For each article, decide ONE action:")
    lines.append("")
    lines.append("- OBSERVE <indicator_id> AT <numeric_value>")
    lines.append("    The article reports a numeric/count value of the indicator's metric AND")
    lines.append("    the indicator's LR direction is consistent with the article's content.")
    lines.append("    Value MUST be in the same units as threshold_value/baseline.")
    lines.append("- FIRE <indicator_id>")
    lines.append("    Only for binary indicators (no observable block) when literal threshold")
    lines.append("    is met AND firing direction matches article content. Forecasts and analyst")
    lines.append("    opinion DO NOT FIRE.")
    lines.append("- PARK")
    lines.append("    Topic-relevant but neither extractable value nor binary fire (no schema")
    lines.append("    gap; observable just doesn't trigger here).")
    lines.append("- SCHEMA_GAP <direction_description>")
    lines.append("    Article reports topic-relevant evidence in a direction the schema has no")
    lines.append("    observable for. Example: article reports recovery in a topic where only")
    lines.append("    decline indicators have observables. Describe the gap so operator can")
    lines.append("    address. Use this INSTEAD of forcing the article through a wrong-direction")
    lines.append("    observable.")
    lines.append("- IGNORE")
    lines.append("    Not topic-relevant, OR pure rhetoric (forecasts, market odds, opinions).")
    lines.append("")
    lines.append("Strict lint:")
    lines.append("- Forecasts ('analyst expects X'), Polymarket/Kalshi odds = RHETORIC = IGNORE")
    lines.append("- Be conservative on OBSERVE values you're not sure about — PARK over guessing.")
    lines.append("- DIRECTIONAL ALIGNMENT IS NON-NEGOTIABLE. If proposing OBSERVE/FIRE on an")
    lines.append("  indicator whose LR vector pushes posterior in a direction inconsistent with")
    lines.append("  the article's content (recovery news → indicator favoring no-recovery, or")
    lines.append("  vice versa), STOP — this is exactly what SCHEMA_GAP is for.")
    lines.append("")
    lines.append("## Output (one block per article, no preamble)")
    lines.append("")
    lines.append("DECISION")
    lines.append("ARTICLE: A<n>")
    lines.append("ACTION: OBSERVE <indicator_id> AT <value> | FIRE <indicator_id> | PARK | SCHEMA_GAP <description> | IGNORE")
    lines.append("TAG: RHETORIC | EVENT | DATA | POLICY | MARKET")
    lines.append("CLAIM: <one factual sentence>")
    lines.append("REASON: <one sentence; cite metric/value if OBSERVE; for SCHEMA_GAP describe the missing observable direction>")
    return "\n".join(lines)


def build_advocate_prompt(topic: dict, articles: list, candidates: list) -> str:
    """
    Round-1 advocate prompt. `candidates` is a list of dicts:
        [{idx: int, claim: str, action_raw: str, reason: str}, ...]
    The advocate argues for the best action for each article.
    """
    slug = topic["meta"]["slug"]
    inds = walk_indicators(topic)
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: ADVOCATE for evidence integration. You argue for integrating")
    lines.append("evidence into posterior updates — but only when directional alignment holds.")
    lines.append("If an article reports evidence in a direction the schema has no observable")
    lines.append("for, your correct verdict is SCHEMA_GAP, not force-fitting.")
    lines.append("")
    lines.append("CONTEXT")
    lines.append(f"Topic: {slug}")
    lines.append(f"Question: {topic['meta'].get('question', '')}")
    lines.append("")
    lines.append("Hypotheses:")
    for hk, hv in topic["model"]["hypotheses"].items():
        label = hv.get("label") or hv.get("desc") or ""
        lines.append(f"  {hk}: {label[:120]} (current posterior {hv.get('posterior')})")
    lines.append("")
    lines.append("YOUR JOB")
    lines.append("These articles were evaluated by a strict matcher. For EACH article, present")
    lines.append("the strongest case for its final action. You can propose to:")
    lines.append("  - COMMIT: confirm the matcher's proposed OBSERVE or FIRE action is correct.")
    lines.append("  - PARK: park the article (keep it parked, or demote an OBSERVE/FIRE to PARK).")
    lines.append("  - WITHDRAW: ignore the article as irrelevant or pure rhetoric.")
    lines.append("  - DUPLICATE_OF A<n>: flag that this article is duplicate coverage of the exact same event as A<n>.")
    lines.append("  - SCHEMA_GAP <description>: flag that there is a schema gap.")
    lines.append("")
    lines.append("INDICATORS (reference)")
    for ind in inds:
        lines.append(f"  {ind['id']}")
        lines.append(f"    desc: {ind['desc'][:200]}")
        if "observable" in ind:
            ob = ind["observable"]
            lines.append(f"    OBSERVABLE: metric={ob['metric']}; threshold={ob['threshold_value']}; baseline={ob['baseline']}; direction={ob['direction']}")
        lines.append(f"    LR: {ind['likelihoods']}")
    lines.append("")
    lines.append("CANDIDATE ARTICLES")
    lines.append("")
    for c in candidates:
        idx = c["idx"]
        art = articles[_to_int_idx(idx) - 1].get("article", articles[_to_int_idx(idx) - 1])
        act_raw = c["action_raw"]
        lines.append(f"[A{idx}] {art.get('headline', '')}")
        lines.append(f"  URL: {art.get('url', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  CANDIDATE_ACTION: {act_raw}")
        lines.append(f"  CLAIM: {c['claim']}")
        lines.append(f"  MATCHER_REASON: {c['reason']}")
        if art.get("relevance"):
            lines.append(f"  RELEVANCE: {art.get('relevance', '')}")
        lines.append("")
    lines.append("OUTPUT (one block per candidate article, no preamble):")
    lines.append("ADVOCATE")
    lines.append("ARTICLE: A<n>")
    lines.append("VERDICT: COMMIT | PARK | WITHDRAW | DUPLICATE_OF A<n> | SCHEMA_GAP <description>")
    lines.append("PROPOSED_ACTION: <e.g. OBSERVE indicator_id AT value, or FIRE indicator_id, or PARK, or IGNORE>")
    lines.append("CITE: <exact phrase or number from the article if proposing COMMIT/OBSERVE/FIRE>")
    lines.append("INFERENCE: <one sentence stating any conversion done>")
    lines.append("REASON: <one sentence case for this verdict>")
    lines.append("END")
    return "\n".join(lines)


def build_rebut_prompt(topic: dict, articles: list, advocate_moves: list,
                       strict_reasons: dict) -> str:
    """
    Round-2 rebut prompt. `advocate_moves` is the list of ADVOCATE
    proposals. `strict_reasons` maps idx -> {claim, reason, action_raw}
    from the original strict matcher output.
    """
    slug = topic["meta"]["slug"]
    inds = walk_indicators(topic)
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: REBUT. Skeptically scrutinize the advocate's proposed actions.")
    lines.append("Beyond inference quality, you specifically check duplicate-event grouping and directional alignment:")
    lines.append("does the advocate's proposed update push posterior in a direction consistent with the article's actual content?")
    lines.append("")
    lines.append("CONTEXT")
    lines.append(f"Topic: {slug}")
    lines.append(f"Question: {topic['meta'].get('question', '')}")
    lines.append("")
    lines.append("Hypotheses:")
    for hk, hv in topic["model"]["hypotheses"].items():
        label = hv.get("label") or hv.get("desc") or ""
        lines.append(f"  {hk}: {label[:120]} (current posterior {hv.get('posterior')})")
    lines.append("")
    lines.append("YOUR JOB")
    lines.append("An ADVOCATE has proposed actions for these candidate articles. Skeptically scrutinize each.")
    lines.append("Check for: directional alignment, factual citation, correct metrics/units, over-interpretation, and duplicates.")
    lines.append("")
    lines.append("INDICATORS (reference)")
    for tier, ind in iter_indicators_for_topic(topic):
        lines.append(f"  ID: {ind['id']}")
        lines.append(f"    desc: {ind['desc']}")
        lines.append(f"    type: {'anti-indicator' if tier == 'anti_indicators' else 'tier indicator'}")
        if "observable" in ind:
            ob = ind["observable"]
            lines.append(f"    OBSERVABLE: metric={ob['metric']}; threshold={ob['threshold_value']}; baseline={ob['baseline']}; direction={ob['direction']}")
        lines.append(f"    LR: {ind['likelihoods']}")
        lines.append("")
    lines.append("ADVOCATE'S PROPOSALS")
    lines.append("")
    for mv in advocate_moves:
        idx = mv["idx"]
        art = articles[_to_int_idx(idx) - 1].get("article", articles[_to_int_idx(idx) - 1])
        sr = strict_reasons.get(idx) or strict_reasons.get(str(idx)) or {}
        lines.append(f"[A{idx}] {art.get('headline', '')}")
        lines.append(f"  URL: {art.get('url', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  MATCHER_CANDIDATE: {sr.get('action_raw', '')}")
        lines.append(f"  MATCHER_REASON: {sr.get('reason', '')}")
        lines.append(f"  ADVOCATE_VERDICT: {mv['verdict']}")
        lines.append(f"  ADVOCATE_PROPOSED: {mv['proposed_action']}")
        lines.append(f"  ADVOCATE_CITE: {mv['cite']}")
        lines.append(f"  ADVOCATE_INFERENCE: {mv['inference']}")
        lines.append(f"  ADVOCATE_REASON: {mv['reason']}")
        lines.append("")
    lines.append("OUTPUT (one block per article above):")
    lines.append("REBUT")
    lines.append("ARTICLE: A<n>")
    lines.append("VERDICT: COMMIT | PARK | WITHDRAW | DUPLICATE_OF A<n> | SCHEMA_GAP <description>")
    lines.append("OBJECTION: <one specific flaw or objection, if disagreeing with advocate>")
    lines.append("CORRECTED_ACTION: <e.g. OBSERVE indicator AT value, or FIRE indicator if correcting action>")
    lines.append("REASON: <one sentence>")
    lines.append("END")
    return "\n".join(lines)


def build_jury_prompt(topic: dict, articles: list, advocate_moves: list,
                      rebuts: dict) -> str:
    """
    Round-3 jury prompt. `rebuts` maps idx -> {verdict, objection,
    corrected_action, reason}. The jury renders final per-article verdict.
    """
    slug = topic["meta"]["slug"]
    inds = walk_indicators(topic)
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: JURY. Render final per-article verdict. You did not participate")
    lines.append("in advocate or rebut rounds — fresh, calibrated voter.")
    lines.append("")
    lines.append("=== JUDGE'S STANDING INSTRUCTIONS ===")
    lines.append("1. DIRECTIONAL ALIGNMENT IS NON-NEGOTIABLE. Reject wrong-direction updates.")
    lines.append("2. JURY IS NOT LIMITED TO THE ADVOCATE'S PROPOSED INDICATOR. If the advocate proposed")
    lines.append("   a wrong or suboptimal indicator, but another listed observable or anti-indicator")
    lines.append("   clearly matches, you may verdict OBSERVE <indicator_id> AT <value> or FIRE <indicator_id>")
    lines.append("   using that existing precommitted indicator.")
    lines.append("3. SCHEMA GAPS. If the evidence direction is real but no listed indicator captures it at all,")
    lines.append("   return SCHEMA_GAP <description_of_gap> (not a force-fit).")
    lines.append("4. DEFAULT IS PARK. If strict thresholds fail and no other listed observable/anti-indicator matches,")
    lines.append("   or in genuine doubt, return PARK (this remains the default reviewable outcome).")
    lines.append("5. DUPLICATES. If this article covers the exact same event as another, select DUPLICATE_OF A<n>.")
    lines.append("=== END STANDING INSTRUCTIONS ===")
    lines.append("")
    lines.append(f"TOPIC: {slug}")
    lines.append(f"Question: {topic['meta'].get('question', '')}")
    lines.append("")
    lines.append("Hypotheses:")
    for hk, hv in topic["model"]["hypotheses"].items():
        label = hv.get("label") or hv.get("desc") or ""
        lines.append(f"  {hk}: {label[:120]} (current posterior {hv.get('posterior')})")
    lines.append("")
    lines.append("INDICATORS (reference)")
    for tier, ind in iter_indicators_for_topic(topic):
        lines.append(f"  ID: {ind['id']}")
        lines.append(f"    desc: {ind['desc']}")
        lines.append(f"    type: {'anti-indicator' if tier == 'anti_indicators' else 'tier indicator'}")
        if "observable" in ind:
            ob = ind["observable"]
            lines.append(f"    OBSERVABLE: metric={ob['metric']}; threshold={ob['threshold_value']}; baseline={ob['baseline']}; direction={ob['direction']}")
        lines.append(f"    LR: {ind['likelihoods']}")
        lines.append("")
    lines.append("CASES")
    lines.append("")
    for mv in advocate_moves:
        idx = mv["idx"]
        art = articles[_to_int_idx(idx) - 1].get("article", articles[_to_int_idx(idx) - 1])
        rb = rebuts.get(idx) or rebuts.get(str(idx)) or {}
        lines.append(f"=== A{idx} ===")
        lines.append(f"  HEADLINE: {art.get('headline', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  ADVOCATE_PROPOSED: {mv['proposed_action']}")
        lines.append(f"  ADVOCATE_VERDICT: {mv['verdict']}")
        lines.append(f"  ADVOCATE_CITE: {mv['cite']}")
        lines.append(f"  ADVOCATE_INFERENCE: {mv['inference']}")
        lines.append(f"  ADVOCATE_REASON: {mv['reason']}")
        lines.append(f"  REBUT_VERDICT: {rb.get('verdict', 'MISSING')}")
        if rb.get("objection"):
            lines.append(f"  REBUT_OBJECTION: {rb['objection']}")
        if rb.get("corrected_action"):
            lines.append(f"  REBUT_CORRECTED: {rb['corrected_action']}")
        lines.append(f"  REBUT_REASON: {rb.get('reason', '')}")
        lines.append("")
    lines.append("OUTPUT (one block per case):")
    lines.append("JURY")
    lines.append("ARTICLE: A<n>")
    lines.append("VERDICT: COMMIT | PARK | WITHDRAW | DUPLICATE_OF A<n> | SCHEMA_GAP <description> | OBSERVE <indicator_id> AT <value> | FIRE <indicator_id>")
    lines.append("RATIONALE: <one sentence reflecting how you weighed advocate vs rebut or selected an alternative indicator>")
    lines.append("END")
    return "\n".join(lines)


# ------------------------ PARSERS ----------------------------

# Line-based: a block ends after its REASON line. The older pattern required
# a literal END terminator (".*?END" with DOTALL|IGNORECASE) — models that
# separate blocks with blank lines instead emit no END, and the lazy scan
# then swallowed entire subsequent blocks until it hit "end" as a substring
# of ordinary words ("depending", "ended"). Observed live: 12 blocks emitted,
# 1 parsed. END lines, when present, are simply ignored between blocks.
_DECISION_BLOCK = re.compile(
    r"DECISION\s*\n"
    r"ARTICLE:\s*A(\d+(?:\.\d+)?)\s*\n"
    r"ACTION:\s*([^\n]+)\n?"
    r"(?:TAG:\s*([^\n]*)\n?)?"
    r"(?:CLAIM:\s*([^\n]*)\n?)?"
    r"(?:REASON:\s*([^\n]*)\n?)?",
    re.IGNORECASE,
)
_OBSERVE_RE = re.compile(r"^OBSERVE\s+(\S+)\s+AT\s+(-?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
_FIRE_RE = re.compile(r"^FIRE\s+(\S+)\s*$", re.IGNORECASE)
# Like _DECISION_BLOCK: line-based, END terminator optional. Models that
# separate blocks with blank lines emit no END; requiring it made every
# block silently unparseable and the whole debate stage a no-op.
_ADV_BLOCK = re.compile(
    r"ADVOCATE\s*\n"
    r"ARTICLE:\s*A(\d+(?:\.\d+)?)\s*\n"
    r"VERDICT:\s*([^\n]+)\s*\n?"
    r"(?:PROPOSED_ACTION:\s*([^\n]+)\n?)?"
    r"(?:CITE:\s*([^\n]+)\n?)?"
    r"(?:INFERENCE:\s*([^\n]+)\n?)?"
    r"(?:REASON:\s*([^\n]+)\n?)?",
    re.MULTILINE | re.IGNORECASE,
)
_REB_BLOCK = re.compile(
    r"REBUT\s*\n"
    r"ARTICLE:\s*A(\d+(?:\.\d+)?)\s*\n"
    r"VERDICT:\s*([^\n]+)\s*\n?"
    r"(?:OBJECTION:\s*([^\n]+)\n?)?"
    r"(?:CORRECTED_ACTION:\s*([^\n]+)\n?)?"
    r"(?:REASON:\s*([^\n]+)\n?)?",
    re.MULTILINE | re.IGNORECASE,
)
_JURY_BLOCK = re.compile(
    r"JURY\s*\n"
    r"ARTICLE:\s*A(\d+(?:\.\d+)?)\s*\n"
    r"VERDICT:\s*([^\n]+)\n?"
    r"(?:RATIONALE:\s*([^\n]+)\n?)?",
    re.MULTILINE | re.IGNORECASE,
)


def parse_action(action_raw: str) -> dict:
    """Parse one ACTION line into structured form."""
    a = action_raw.strip().rstrip(".,;:")
    m = _OBSERVE_RE.match(a)
    if m:
        return {"kind": "OBSERVE", "indicator_id": m.group(1),
                "value": float(m.group(2))}
    m = _FIRE_RE.match(a)
    if m:
        return {"kind": "FIRE", "indicator_id": m.group(1)}
    upper = a.upper()
    if upper.startswith("SCHEMA_GAP"):
        # Capture description after "SCHEMA_GAP "
        desc = a[len("SCHEMA_GAP"):].strip()
        return {"kind": "SCHEMA_GAP", "description": desc}
    if upper.startswith("PARK"):
        return {"kind": "PARK"}
    if upper.startswith("IGNORE"):
        return {"kind": "IGNORE"}
    return {"kind": "ERROR", "raw": a}


def parse_matcher_output(text: str) -> list:
    """Parse strict-matcher DECISION blocks. Returns list of dicts."""
    out = []
    for m in _DECISION_BLOCK.finditer(text):
        idx_str = m.group(1)
        idx = idx_str if "." in idx_str else int(idx_str)
        out.append({
            "idx": idx,
            "action": parse_action(m.group(2)),
            "tag": (m.group(3) or "EVENT").strip().upper().split()[0]
                   if m.group(3) else "EVENT",
            "claim": (m.group(4) or "").strip(),
            "reason": (m.group(5) or "").strip(),
        })

    # Uniquify idx for multiple decisions on the same article
    counts = {}
    for d in out:
        idx = d["idx"]
        counts[idx] = counts.get(idx, 0) + 1
        
    seen = {}
    for d in out:
        idx = d["idx"]
        if counts[idx] > 1:
            seen[idx] = seen.get(idx, 0) + 1
            d["idx"] = f"{idx}.{seen[idx] - 1}"

    return out


def parse_advocate_output(text: str) -> list:
    out = []
    for m in _ADV_BLOCK.finditer(text):
        idx_str = m.group(1)
        idx = idx_str if "." in idx_str else int(idx_str)
        out.append({
            "idx": idx,
            "verdict": m.group(2).strip().upper(),
            "proposed_action": (m.group(3) or "").strip(),
            "cite": (m.group(4) or "").strip(),
            "inference": (m.group(5) or "").strip(),
            "reason": (m.group(6) or "").strip(),
        })
    return out


def parse_rebut_output(text: str) -> dict:
    """Returns {idx: {verdict, objection, corrected_action, reason}}."""
    out = {}
    for m in _REB_BLOCK.finditer(text):
        idx_str = m.group(1)
        idx = idx_str if "." in idx_str else int(idx_str)
        out[idx] = {
            "verdict": m.group(2).strip().upper(),
            "objection": (m.group(3) or "").strip(),
            "corrected_action": (m.group(4) or "").strip(),
            "reason": (m.group(5) or "").strip(),
        }
    return out


def parse_jury_output(text: str) -> dict:
    """Returns {idx: {verdict_raw, action_dict, rationale}}."""
    out = {}
    for m in _JURY_BLOCK.finditer(text):
        idx_str = m.group(1)
        idx = idx_str if "." in idx_str else int(idx_str)
        verdict_raw = m.group(2).strip()
        verdict_upper = verdict_raw.upper()
        
        if verdict_upper.startswith("COMMIT"):
            inner = verdict_raw[len("COMMIT"):].strip()
            if inner:
                action = parse_action(inner)
            else:
                action = {"kind": "COMMIT"}
        elif verdict_upper.startswith("DUPLICATE_OF"):
            parent = verdict_upper[len("DUPLICATE_OF"):].strip().lstrip("A")
            if "." in parent:
                parent_idx = parent
            else:
                try:
                    parent_idx = int(parent)
                except ValueError:
                    parent_idx = 0
            action = {"kind": "DUPLICATE_OF", "parent_idx": parent_idx}
        elif verdict_upper.startswith("SCHEMA_GAP"):
            desc = verdict_raw[len("SCHEMA_GAP"):].strip()
            action = {"kind": "SCHEMA_GAP", "description": desc}
        elif verdict_upper.startswith("WITHDRAW") or verdict_upper.startswith("IGNORE"):
            action = {"kind": "IGNORE"}
        elif verdict_upper.startswith("PARK"):
            action = {"kind": "PARK"}
        elif verdict_upper.startswith("MOVE_TO"):
            inner = verdict_raw[len("MOVE_TO"):].strip()
            action = parse_action(inner)
        elif verdict_upper.startswith("OBSERVE") or verdict_upper.startswith("FIRE"):
            action = parse_action(verdict_raw)
        else:
            action = {"kind": "PARK"}  # Default fallback
            
        out[idx] = {
            "verdict_raw": verdict_raw,
            "action": action,
            "rationale": (m.group(3) or "").strip(),
        }
    return out


# ------------------------ APPLY DRIVER ----------------------------

def group_decisions_by_duplicates(articles: list, decisions: list) -> tuple[list[dict], dict[int, list[dict]]]:
    """
    Group same-batch decisions by duplicates.
    Returns (canonical_decisions, duplicate_map) where duplicate_map maps canonical idx -> list of duplicate decisions.
    """
    by_idx = {d["idx"]: d for d in decisions}
    canonical_decisions = []
    duplicate_map = {}
    
    dup_idxs = set()
    
    # 1. Process explicit DUPLICATE_OF relationships from the deliberator
    for d in decisions:
        action = d["action"]
        if action["kind"] == "DUPLICATE_OF":
            parent_idx = action.get("parent_idx")
            # Lookup parent_d robustly
            parent_d = by_idx.get(parent_idx)
            if parent_d is None:
                if isinstance(parent_idx, int):
                    # Try string representations like "2.0"
                    for k in by_idx:
                        if isinstance(k, str) and k.startswith(f"{parent_idx}."):
                            parent_d = by_idx[k]
                            parent_idx = k
                            break
                elif isinstance(parent_idx, str):
                    try:
                        parent_int = int(float(parent_idx))
                        parent_d = by_idx.get(parent_int)
                        if parent_d:
                            parent_idx = parent_int
                    except (ValueError, TypeError):
                        pass
            
            if parent_d and parent_d["action"]["kind"] in {"FIRE", "OBSERVE"}:
                duplicate_map.setdefault(parent_idx, []).append(d)
                dup_idxs.add(d["idx"])
                    
    # 2. Process implicit same-batch duplicates (indicator/value similarity fallback)
    observe_groups = {}  # key: (indicator_id, value_rounded) -> list of decisions
    fire_groups = {}     # key: indicator_id -> list of decisions
    
    for d in decisions:
        if d["idx"] in dup_idxs:
            continue
        action = d["action"]
        if action["kind"] == "OBSERVE":
            key = (action["indicator_id"], round(float(action["value"]), 2))
            observe_groups.setdefault(key, []).append(d)
        elif action["kind"] == "FIRE":
            fire_groups.setdefault(action["indicator_id"], []).append(d)
            
    # For each group, the first one is canonical, others are duplicates of it
    for key, group in observe_groups.items():
        canonical = group[0]
        canonical_decisions.append(canonical)
        if len(group) > 1:
            for dup in group[1:]:
                duplicate_map.setdefault(canonical["idx"], []).append(dup)
                dup_idxs.add(dup["idx"])
                
    for key, group in fire_groups.items():
        canonical = group[0]
        canonical_decisions.append(canonical)
        if len(group) > 1:
            for dup in group[1:]:
                duplicate_map.setdefault(canonical["idx"], []).append(dup)
                dup_idxs.add(dup["idx"])
                
    # Add all other non-duplicate, non-observe, non-fire decisions
    for d in decisions:
        if d["idx"] not in dup_idxs and d["action"]["kind"] not in {"OBSERVE", "FIRE"}:
            canonical_decisions.append(d)
            
    canonical_decisions.sort(key=lambda d: _sort_key(d["idx"]))
    return canonical_decisions, duplicate_map


def apply_decisions(slug: str, articles: list, decisions: list,
                    *, jury_overrides: dict = None) -> dict:
    """
    Apply per-article decisions through the engine, resolving duplicates.
    """
    jury_overrides = jury_overrides or {}
    
    # Fold overrides into the decisions list before grouping
    for d in decisions:
        idx = d.get("idx")
        override = jury_overrides.get(idx) or jury_overrides.get(str(idx))
        if override:
            action = override["action"]
            if action["kind"] == "COMMIT":
                pass  # keep original action
            else:
                d["action"] = action
            d["jury_override"] = True
            d["reason"] = f"jury: {override.get('rationale') or 'override'}"

    # Group by duplicates
    canonical_decisions, duplicate_map = group_decisions_by_duplicates(articles, decisions)

    summary = {
        "observe": 0, "fire": 0, "park": 0, "ignore": 0,
        "schema_gap": 0,
        "engine_rejections": 0, "missing": 0, "errors": 0,
        "rejection_msgs": [], "results": [],
        "schema_gaps": [],
        "bundled_groups": [],
    }

    for d in canonical_decisions:
        idx = d["idx"]
        art = articles[_to_int_idx(idx) - 1]
        action = d["action"]
        kind = action["kind"]
        
        if kind == "IGNORE":
            summary["ignore"] += 1
            continue
            
        if kind == "SCHEMA_GAP":
            inner = art.get("article", art)
            from framework.pipeline import log_schema_gap
            log_schema_gap(slug, {
                "headline": inner.get("headline", ""),
                "url": inner.get("url", ""),
                "source": inner.get("source", ""),
                "claim": d.get("claim", ""),
                "missing_direction": action.get("description", ""),
                "matcher_reason": d.get("reason", ""),
            })
            summary["schema_gap"] += 1
            summary["schema_gaps"].append({
                "article": f"A{idx}",
                "headline": inner.get("headline", ""),
                "missing_direction": action.get("description", ""),
            })
            continue

        # Check if there are duplicates for this canonical decision
        dups = duplicate_map.get(idx, [])
        if dups:
            try:
                why = f"Duplicate coverage of canonical article A{idx}"
                secondary_evidence_ids = []
                for dup_d in dups:
                    sec_idx = dup_d["idx"]
                    sec_art = articles[_to_int_idx(sec_idx) - 1]
                    sec_inner = sec_art.get("article", sec_art)
                    sec_inner.setdefault("surfaced_via", sec_art.get("channels", []))
                    sec_entry = article_to_evidence_entry(
                        sec_inner, round_num=1,
                        default_tag=dup_d.get("tag", "EVENT") or "EVENT",
                    )
                    sec_entry["claim"] = dup_d.get("claim") or sec_entry.get("text", "")
                    sec_result = process_evidence(
                        slug=slug, entry=sec_entry,
                        fired_indicator_id=None,
                        reason=why,
                    )
                    log_activity(sec_result, platform="news-scan")
                    summary["park"] += 1
                    if sec_result.get("evidence_id"):
                        secondary_evidence_ids.append(sec_result["evidence_id"])
            except Exception as e:
                summary["engine_rejections"] += 1
                summary["rejection_msgs"].append(
                    f"BUNDLE-PARK({len(dups)+1}) {action.get('indicator_id')}: {type(e).__name__}: {str(e)[:200]}"
                )
                continue

            inner = art.get("article", art)
            inner.setdefault("surfaced_via", art.get("channels", []))
            entry = article_to_evidence_entry(
                inner, round_num=1,
                default_tag=d.get("tag", "EVENT") or "EVENT",
            )
            combined_claim = " | ".join([d.get("claim", "")] + [dup_d.get("claim", "") for dup_d in dups if dup_d.get("claim")])
            entry["claim"] = combined_claim or entry.get("text", "")
            entry["evidence_refs"] = list(secondary_evidence_ids)
            entry["bundled_articles"] = [f"A{idx}"] + [f"A{dup_d['idx']}" for dup_d in dups]

            try:
                if kind == "OBSERVE":
                    result = apply_observation(
                        slug=slug, entry=entry,
                        indicator_id=action["indicator_id"],
                        observed_value=action["value"],
                    )
                    summary["observe"] += 1
                elif kind == "FIRE":
                    result = process_evidence(
                        slug=slug, entry=entry,
                        fired_indicator_id=action["indicator_id"],
                        reason=d.get("reason"),
                    )
                    summary["fire"] += 1
                else:
                    result = process_evidence(
                        slug=slug, entry=entry,
                        fired_indicator_id=None,
                        reason=d.get("reason"),
                    )
                    summary["park"] += 1

                log_activity(result, platform="news-scan")
                summary["bundled_groups"].append({
                    "kind": kind,
                    "indicator_id": action.get("indicator_id"),
                    "value": action.get("value"),
                    "n_articles": len(dups) + 1,
                    "articles": entry["bundled_articles"],
                    "secondary_refs": secondary_evidence_ids,
                    "before": result.get("posteriors_before"),
                    "after": result.get("posteriors_after"),
                })
            except Exception as e:
                summary["engine_rejections"] += 1
                summary["rejection_msgs"].append(
                    f"BUNDLED({len(dups)+1}) {action.get('indicator_id')}: {type(e).__name__}: {str(e)[:200]}"
                )
        else:
            inner = art.get("article", art)
            inner.setdefault("surfaced_via", art.get("channels", []))
            entry = article_to_evidence_entry(inner, round_num=1,
                                              default_tag=d.get("tag", "EVENT") or "EVENT")
            entry["claim"] = d.get("claim") or entry.get("text", "")
            if inner.get("url") or inner.get("headline"):
                entry["evidence_refs"] = [inner.get("url") or inner.get("headline")]

            try:
                if kind == "OBSERVE":
                    result = apply_observation(
                        slug=slug, entry=entry,
                        indicator_id=action["indicator_id"],
                        observed_value=action["value"],
                    )
                    log_activity(result, platform="news-scan")
                    summary["observe"] += 1
                elif kind == "FIRE":
                    result = process_evidence(
                        slug=slug, entry=entry,
                        fired_indicator_id=action["indicator_id"],
                        reason=d.get("reason"),
                    )
                    log_activity(result, platform="news-scan")
                    summary["fire"] += 1
                elif kind == "PARK":
                    result = process_evidence(
                        slug=slug, entry=entry,
                        fired_indicator_id=None,
                        reason=d.get("reason"),
                    )
                    log_activity(result, platform="news-scan")
                    summary["park"] += 1
                else:
                    summary["errors"] += 1
                    continue
                # evidence_id is surfaced so the MCP safe-policy brief can
                # give operators a row handle for a freshness-downgraded
                # PARK, instead of a downgrade marker they can only act on
                # by re-reading the full on-disk digest packet (the sandbox
                # break-out bait brief=true exists to keep them out of).
                summary["results"].append({
                    "article": f"A{idx}", "kind": kind,
                    "indicator_id": action.get("indicator_id"),
                    "value": action.get("value"),
                    "evidence_id": result.get("evidence_id"),
                    "before": result.get("posteriors_before"),
                    "after": result.get("posteriors_after"),
                })
            except Exception as e:
                summary["engine_rejections"] += 1
                summary["rejection_msgs"].append(
                    f"A{idx} {kind} {action.get('indicator_id', '?')}: {type(e).__name__}: {str(e)[:200]}"
                )

    try:
        stamp_last_scanned(slug)
    except Exception:
        pass

    try:
        from framework.schema_gap_resolver import should_dispatch_resolver
        from engine import load_topic
        _topic_now = load_topic(slug)
        should, reason = should_dispatch_resolver(_topic_now)
        summary["resolver_should_dispatch"] = should
        summary["resolver_reason"] = reason
    except Exception as e:
        summary["resolver_should_dispatch"] = False
        summary["resolver_reason"] = f"check failed: {type(e).__name__}: {e}"

    return summary


# ------------------------ TOP-LEVEL HELPERS ----------------------------

def get_parks_with_reasons(matcher_decisions: list) -> list:
    """Extract parked entries with their strict reasoning, for advocate prompt."""
    return [
        {"idx": d["idx"], "claim": d["claim"], "park_reason": d["reason"]}
        for d in matcher_decisions if d["action"]["kind"] == "PARK"
    ]


def get_candidates_with_reasons(matcher_decisions: list) -> list:
    """Extract candidate entries (FIRE, OBSERVE, PARK) for advocate prompt."""
    out = []
    for d in matcher_decisions:
        kind = d["action"]["kind"]
        if kind in {"FIRE", "OBSERVE", "PARK"}:
            if kind == "OBSERVE":
                act_str = f"OBSERVE {d['action']['indicator_id']} AT {d['action']['value']}"
            elif kind == "FIRE":
                act_str = f"FIRE {d['action']['indicator_id']}"
            else:
                act_str = "PARK"
            out.append({
                "idx": d["idx"],
                "claim": d["claim"],
                "action_raw": act_str,
                "reason": d["reason"],
            })
    return out


def get_strict_reasons_map(matcher_decisions: list) -> dict:
    """{idx: {claim, reason, action_raw}} for rebut/jury context."""
    out = {}
    for d in matcher_decisions:
        kind = d["action"]["kind"]
        if kind == "OBSERVE":
            act_str = f"OBSERVE {d['action']['indicator_id']} AT {d['action']['value']}"
        elif kind == "FIRE":
            act_str = f"FIRE {d['action']['indicator_id']}"
        else:
            act_str = kind
        out[d["idx"]] = {
            "claim": d["claim"],
            "reason": d["reason"],
            "action_raw": act_str,
        }
    return out


def filter_advocate_moves(advocate_blocks: list) -> list:
    """Just the ADVOCATE entries that propose a COMMIT verdict (acting as moves or confirmations)."""
    return [a for a in advocate_blocks if a["verdict"] == "COMMIT"]
