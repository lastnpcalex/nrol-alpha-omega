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


def walk_indicators(topic: dict) -> list:
    """Return a flat list of every indicator dict in the topic schema."""
    out = []
    inds = topic.get("indicators") or {}
    tiers = inds.get("tiers") or {}
    for tier_list in tiers.values():
        if isinstance(tier_list, list):
            for ind in tier_list:
                if isinstance(ind, dict) and "id" in ind and "likelihoods" in ind:
                    out.append(ind)
    for ind in inds.get("anti_indicators") or []:
        if isinstance(ind, dict) and "id" in ind and "likelihoods" in ind:
            out.append(ind)
    return out


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
    lines.append("END")
    return "\n".join(lines)


def build_advocate_prompt(topic: dict, articles: list, parks: list) -> str:
    """
    Round-1 advocate prompt. `parks` is a list of dicts:
        [{idx: int, claim: str, park_reason: str}, ...]
    The advocate may only propose OBSERVE on indicators with observable blocks.
    """
    slug = topic["meta"]["slug"]
    inds = walk_indicators(topic)
    inds_with_obs = [i for i in inds if "observable" in i]
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: ADVOCATE for evidence integration. You argue for moving PARKed")
    lines.append("articles INTO posterior updates — but only when directional alignment holds.")
    lines.append("If an article reports evidence in a direction the schema has no observable")
    lines.append("for, your correct verdict is SCHEMA_GAP, not ARGUE_MOVE through a wrong-")
    lines.append("direction observable.")
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
    lines.append("These articles were PARK'd by a strict matcher. For EACH article, present")
    lines.append("the strongest defensible case to MOVE it OUT of PARK by proposing OBSERVE,")
    lines.append("IF such a case exists. Single-step inference is allowed (e.g., weekly→monthly")
    lines.append("conversion) as long as you state the conversion. No defensible case → PARK_OK.")
    lines.append("")
    lines.append("RULES")
    lines.append("- Only propose OBSERVE on indicators with `observable` blocks (listed below).")
    lines.append("  Binary indicators were already correctly evaluated.")
    lines.append("- Your proposed value MUST be CITABLE — reference a phrase or number from the")
    lines.append("  article. The value must be in the metric's native units (matching threshold")
    lines.append("  and baseline units in the observable block).")
    lines.append("- If no defensible case, output PARK_OK.")
    lines.append("")
    lines.append("INDICATORS WITH OBSERVABLE BLOCKS")
    for ind in inds_with_obs:
        ob = ind["observable"]
        lines.append(f"  {ind['id']}")
        lines.append(f"    desc: {ind['desc'][:200]}")
        lines.append(f"    metric: {ob['metric']}")
        lines.append(f"    family: {ob['family']}")
        lines.append(f"    threshold_value: {ob['threshold_value']}; baseline: {ob['baseline']}; direction: {ob['direction']}")
        lines.append(f"    LR_at_threshold: {ind['likelihoods']}")
    lines.append("")
    lines.append("PARKED ARTICLES")
    lines.append("")
    for p in parks:
        art = articles[p["idx"] - 1].get("article", articles[p["idx"] - 1])
        lines.append(f"[A{p['idx']}] {art.get('headline', '')}")
        lines.append(f"  URL: {art.get('url', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  CLAIM: {p['claim']}")
        lines.append(f"  STRICT_PARK_REASON: {p['park_reason']}")
        if art.get("relevance"):
            lines.append(f"  RELEVANCE: {art.get('relevance', '')}")
        lines.append("")
    lines.append("OUTPUT (one block per parked article, no preamble):")
    lines.append("ADVOCATE")
    lines.append("ARTICLE: A<n>")
    lines.append("VERDICT: ARGUE_MOVE | PARK_OK")
    lines.append("PROPOSED_ACTION: OBSERVE <indicator_id> AT <value>     # only ARGUE_MOVE")
    lines.append("CITE: <exact phrase or number from the article>          # only ARGUE_MOVE")
    lines.append("INFERENCE: <one sentence stating any conversion done>    # only ARGUE_MOVE")
    lines.append("REASON: <one sentence>")
    lines.append("END")
    return "\n".join(lines)


def build_rebut_prompt(topic: dict, articles: list, advocate_moves: list,
                       strict_reasons: dict) -> str:
    """
    Round-2 rebut prompt. `advocate_moves` is the list of ARGUE_MOVE
    proposals from advocate output. `strict_reasons` maps idx -> {claim,
    reason} from the original strict matcher output.
    """
    slug = topic["meta"]["slug"]
    inds = walk_indicators(topic)
    inds_with_obs = [i for i in inds if "observable" in i]
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: REBUT. Skeptically scrutinize the advocate's proposed moves.")
    lines.append("Beyond inference quality, you specifically check directional alignment: does")
    lines.append("the advocate's proposed update push posterior in a direction consistent with")
    lines.append("the article's actual content? An update that's technically clean but pushes")
    lines.append("the wrong direction is a failure mode you must catch.")
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
    lines.append("An ADVOCATE has argued these PARKed articles should be moved to OBSERVE.")
    lines.append("Skeptically scrutinize each. Specifically check:")
    lines.append("1. Is the CITED phrase actually in the article (not invented)?")
    lines.append("2. Is the INFERENCE valid (baseline conversion, units, metric)?")
    lines.append("3. Is the proposed VALUE in the right ballpark, or wildly off?")
    lines.append("4. Is the proposed INDICATOR the correct one for the observation?")
    lines.append("5. Did the advocate over-interpret? (e.g., 'X transits on day-1' may not generalize)")
    lines.append("")
    lines.append("RULES")
    lines.append("- Be skeptical but fair. Sound advocate arguments → NO_REBUT.")
    lines.append("- Real flaws → REBUT with specific objection.")
    lines.append("- Indicator right but number off → CORRECT_VALUE with corrected number.")
    lines.append("")
    lines.append("INDICATORS WITH OBSERVABLE BLOCKS (reference)")
    for ind in inds_with_obs:
        ob = ind["observable"]
        lines.append(f"  {ind['id']} | metric={ob['metric']} | family={ob['family']} | threshold={ob['threshold_value']} | baseline={ob['baseline']} | direction={ob['direction']}")
    lines.append("")
    lines.append("ADVOCATE'S ARGUMENTS")
    lines.append("")
    for mv in advocate_moves:
        art = articles[mv["idx"] - 1].get("article", articles[mv["idx"] - 1])
        sr = strict_reasons.get(mv["idx"], {})
        lines.append(f"[A{mv['idx']}] {art.get('headline', '')}")
        lines.append(f"  URL: {art.get('url', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  STRICT_PARK_REASON: {sr.get('reason', '')}")
        lines.append(f"  ADVOCATE_PROPOSED: {mv['proposed_action']}")
        lines.append(f"  ADVOCATE_CITE: {mv['cite']}")
        lines.append(f"  ADVOCATE_INFERENCE: {mv['inference']}")
        lines.append(f"  ADVOCATE_REASON: {mv['reason']}")
        lines.append("")
    lines.append("OUTPUT (one block per article above):")
    lines.append("REBUT")
    lines.append("ARTICLE: A<n>")
    lines.append("VERDICT: REBUT | NO_REBUT | CORRECT_VALUE")
    lines.append("OBJECTION: <one specific flaw>           # REBUT or CORRECT_VALUE only")
    lines.append("CORRECTED_ACTION: OBSERVE <indicator_id> AT <value>          # CORRECT_VALUE only")
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
    inds_with_obs = [i for i in inds if "observable" in i]
    lines = [SYSTEM_PURPOSE_FRAMING]
    lines.append("YOUR ROLE: JURY. Render final per-article verdict. You did not participate")
    lines.append("in advocate or rebut rounds — fresh, calibrated voter.")
    lines.append("")
    lines.append("=== JUDGE'S STANDING INSTRUCTIONS — READ FIRST ===")
    lines.append("")
    lines.append("1. THE POINT OF THIS SYSTEM. Pre-committed indicators with pre-committed LRs")
    lines.append("   PREVENT context-anchored vibes-LR — the failure mode that previously pegged")
    lines.append("   17 topics at clamp ceilings. Inference QUALITY is what you judge here;")
    lines.append("   evidence QUANTITY is enforced by separate engine gates.")
    lines.append("")
    lines.append("2. WEIGHT OF VOTES. Accepting an OBSERVE applies a real LR shift via")
    lines.append("   mechanical engine evaluation. Engine clamps, decorrelation, and")
    lines.append("   confidence_inflation gates protect against runaway, but small wrong shifts")
    lines.append("   accumulate. Rejecting keeps PARK; that signal is missed for THIS scan, but")
    lines.append("   if the world-state persists the next scan can recover.")
    lines.append("")
    lines.append("3. DEFAULT IS PARK. In genuine doubt, KEEP_PARK. Advocate carries burden of")
    lines.append("   proof: cite from article, sound inference, value in correct ballpark/units.")
    lines.append("   Any of these shaky → KEEP_PARK.")
    lines.append("")
    lines.append("4. IF REBUT RAISED A SPECIFIC FLAW (phase-transition artifact, invented")
    lines.append("   baseline, unit mismatch, wrong indicator), that flaw must be addressed.")
    lines.append("")
    lines.append("5. CORRECT_VALUE proposals from rebut: use them if better-grounded than")
    lines.append("   advocate's number; otherwise stick to advocate's value or KEEP_PARK.")
    lines.append("")
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
    lines.append("INDICATORS WITH OBSERVABLE BLOCKS")
    for ind in inds_with_obs:
        ob = ind["observable"]
        lines.append(f"  {ind['id']} | metric={ob['metric']} | family={ob['family']} | threshold={ob['threshold_value']} | baseline={ob['baseline']} | direction={ob['direction']}")
    lines.append("")
    lines.append("CASES")
    lines.append("")
    for mv in advocate_moves:
        art = articles[mv["idx"] - 1].get("article", articles[mv["idx"] - 1])
        rb = rebuts.get(mv["idx"], {})
        lines.append(f"=== A{mv['idx']} ===")
        lines.append(f"  HEADLINE: {art.get('headline', '')}")
        lines.append(f"  SOURCE: {art.get('source', '')}")
        lines.append(f"  ADVOCATE_PROPOSED: {mv['proposed_action']}")
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
    lines.append("VERDICT: MOVE_TO OBSERVE <indicator_id> AT <value> | KEEP_PARK")
    lines.append("RATIONALE: <one sentence reflecting how you weighed advocate vs rebut>")
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
    r"ARTICLE:\s*A(\d+)\s*\n"
    r"ACTION:\s*([^\n]+)\n?"
    r"(?:TAG:\s*([^\n]*)\n?)?"
    r"(?:CLAIM:\s*([^\n]*)\n?)?"
    r"(?:REASON:\s*([^\n]*)\n?)?",
    re.IGNORECASE,
)
_OBSERVE_RE = re.compile(r"^OBSERVE\s+(\S+)\s+AT\s+(-?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
_FIRE_RE = re.compile(r"^FIRE\s+(\S+)\s*$", re.IGNORECASE)
_ADV_BLOCK = re.compile(
    r"ADVOCATE\s*\n"
    r"ARTICLE:\s*A(\d+)\s*\n"
    r"VERDICT:\s*([A-Z_]+)\s*\n"
    r"(?:PROPOSED_ACTION:\s*([^\n]+)\n)?"
    r"(?:CITE:\s*([^\n]+)\n)?"
    r"(?:INFERENCE:\s*([^\n]+)\n)?"
    r"(?:REASON:\s*([^\n]+)\n)?"
    r"END",
    re.MULTILINE,
)
_REB_BLOCK = re.compile(
    r"REBUT\s*\n"
    r"ARTICLE:\s*A(\d+)\s*\n"
    r"VERDICT:\s*([A-Z_]+)\s*\n"
    r"(?:OBJECTION:\s*([^\n]+)\n)?"
    r"(?:CORRECTED_ACTION:\s*([^\n]+)\n)?"
    r"(?:REASON:\s*([^\n]+)\n)?"
    r"END",
    re.MULTILINE,
)
_JURY_BLOCK = re.compile(
    r"JURY\s*\n"
    r"ARTICLE:\s*A(\d+)\s*\n"
    r"VERDICT:\s*([^\n]+)\n"
    r"(?:RATIONALE:\s*([^\n]+)\n)?"
    r"END",
    re.MULTILINE,
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
        out.append({
            "idx": int(m.group(1)),
            "action": parse_action(m.group(2)),
            "tag": (m.group(3) or "EVENT").strip().upper().split()[0]
                   if m.group(3) else "EVENT",
            "claim": (m.group(4) or "").strip(),
            "reason": (m.group(5) or "").strip(),
        })
    return out


def parse_advocate_output(text: str) -> list:
    out = []
    for m in _ADV_BLOCK.finditer(text):
        out.append({
            "idx": int(m.group(1)),
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
        out[int(m.group(1))] = {
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
        verdict_raw = m.group(2).strip()
        if verdict_raw.upper().startswith("MOVE_TO"):
            inner = verdict_raw[len("MOVE_TO"):].strip()
            action = parse_action(inner)
        else:
            action = {"kind": "PARK"}  # KEEP_PARK
        out[int(m.group(1))] = {
            "verdict_raw": verdict_raw,
            "action": action,
            "rationale": (m.group(3) or "").strip(),
        }
    return out


# ------------------------ APPLY DRIVER ----------------------------

def apply_decisions(slug: str, articles: list, decisions: list,
                    *, jury_overrides: dict = None) -> dict:
    """
    Apply per-article decisions through the engine. `jury_overrides` is an
    optional {idx: action_dict} map from the debate stage that supersedes
    PARK decisions where the jury voted MOVE_TO.

    Returns a summary dict with counts per outcome and per-article results.
    """
    by_idx = {d["idx"]: d for d in decisions}
    jury_overrides = jury_overrides or {}

    summary = {
        "observe": 0, "fire": 0, "park": 0, "ignore": 0,
        "schema_gap": 0,
        "engine_rejections": 0, "missing": 0, "errors": 0,
        "rejection_msgs": [], "results": [],
        "schema_gaps": [],
        "bundled_groups": [],
    }

    # Bundle OBSERVE actions on the same (indicator_id, rounded value) so
    # multiple articles reporting the SAME underlying event become ONE
    # evidence entry with multiple evidence_refs. Without this, six articles
    # about Project Freedom would attempt six separate updates and trip the
    # confidence_inflation gate (>15pp shift with <2 evidence_refs).
    observe_groups = {}  # key: (indicator_id, value_rounded) -> list of (idx, decision, art)
    for i, art in enumerate(articles, start=1):
        d = by_idx.get(i)
        if d is None:
            continue
        action = d["action"]
        if i in jury_overrides and action["kind"] == "PARK":
            action = jury_overrides[i]
        if action["kind"] == "OBSERVE":
            key = (action["indicator_id"], round(float(action["value"]), 2))
            observe_groups.setdefault(key, []).append((i, d, art, action))

    # Track which idxs were applied as part of bundled OBSERVE
    bundled_idxs = set()

    # Apply bundled OBSERVE groups first.
    # Pre-park secondary articles (each gets its own evidenceLog entry +
    # evidence_id), then run apply_observation on the canonical first
    # article passing the secondary evidence_ids as refs. This way the
    # engine's confidence_inflation gate sees a multi-ref evidence base
    # made of REAL evidence_ids that resolve to log entries — and any
    # repetition_as_validation dedup runs correctly across them.
    for (ind_id, value), group in observe_groups.items():
        if len(group) == 1:
            continue  # singleton — let per-article loop handle it normally

        # Step 1: park the N-1 secondary articles, collect their evidence_ids
        secondary_evidence_ids = []
        try:
            for sec_idx, sec_d, sec_art, _ in group[1:]:
                sec_inner = sec_art.get("article", sec_art)
                sec_inner.setdefault("surfaced_via", sec_art.get("channels", []))
                sec_entry = article_to_evidence_entry(
                    sec_inner, round_num=1,
                    default_tag=sec_d.get("tag", "EVENT") or "EVENT",
                )
                sec_entry["claim"] = sec_d.get("claim") or sec_entry.get("text", "")
                # Park this secondary article (no firing). It gets logged with
                # impact NONE — flagged for indicator review — that's fine; its
                # purpose here is providing an evidence_id for the bundle.
                sec_result = process_evidence(
                    slug=slug, entry=sec_entry,
                    fired_indicator_id=None,
                    reason=f"Secondary article in bundled OBSERVE for {ind_id}",
                )
                log_activity(sec_result, platform="news-scan")
                summary["park"] += 1
                if sec_result.get("evidence_id"):
                    secondary_evidence_ids.append(sec_result["evidence_id"])
        except Exception as e:
            summary["engine_rejections"] += 1
            summary["rejection_msgs"].append(
                f"BUNDLE-PARK({len(group)}) {ind_id}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            for idx, _, _, _ in group:
                bundled_idxs.add(idx)
            continue

        # Step 2: apply_observation on canonical first article, passing the
        # secondary evidence_ids as refs (they resolve in evidenceLog now).
        first_idx, first_d, first_art, _ = group[0]
        inner = first_art.get("article", first_art)
        inner.setdefault("surfaced_via", first_art.get("channels", []))
        entry = article_to_evidence_entry(
            inner, round_num=1,
            default_tag=first_d.get("tag", "EVENT") or "EVENT",
        )
        combined_claim = " | ".join(d.get("claim", "") for _, d, _, _ in group if d.get("claim"))
        entry["claim"] = combined_claim or entry.get("text", "")
        entry["evidence_refs"] = list(secondary_evidence_ids)  # real evidence_ids
        entry["bundled_articles"] = [f"A{idx}" for idx, _, _, _ in group]

        try:
            result = apply_observation(
                slug=slug, entry=entry,
                indicator_id=ind_id,
                observed_value=value,
            )
            log_activity(result, platform="news-scan")
            summary["observe"] += 1
            summary["bundled_groups"].append({
                "indicator_id": ind_id,
                "value": value,
                "n_articles": len(group),
                "articles": [f"A{idx}" for idx, _, _, _ in group],
                "secondary_refs": secondary_evidence_ids,
                "before": result.get("posteriors_before"),
                "after": result.get("posteriors_after"),
            })
            for idx, _, _, _ in group:
                bundled_idxs.add(idx)
        except Exception as e:
            summary["engine_rejections"] += 1
            summary["rejection_msgs"].append(
                f"BUNDLED({len(group)}) {ind_id}={value}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            for idx, _, _, _ in group:
                bundled_idxs.add(idx)

    for i, art in enumerate(articles, start=1):
        if i in bundled_idxs:
            continue  # already handled in bundle loop above
        d = by_idx.get(i)
        if d is None:
            summary["missing"] += 1
            continue

        action = d["action"]
        # If jury voted MOVE_TO on this idx, override the original PARK
        if i in jury_overrides and action["kind"] == "PARK":
            action = jury_overrides[i]

        kind = action["kind"]
        if kind == "IGNORE":
            summary["ignore"] += 1
            continue
        if kind == "SCHEMA_GAP":
            # Article reports topic-relevant evidence in a direction the schema
            # has no observable for. Log to topic.governance.flagged_schema_gaps
            # for operator review. Do NOT add to evidenceLog — the article
            # doesn't have a place to land in the current schema, by the
            # matcher's own admission.
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
                "article": f"A{i}",
                "headline": inner.get("headline", ""),
                "missing_direction": action.get("description", ""),
            })
            continue

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
            summary["results"].append({
                "article": f"A{i}", "kind": kind,
                "indicator_id": action.get("indicator_id"),
                "value": action.get("value"),
                "before": result.get("posteriors_before"),
                "after": result.get("posteriors_after"),
            })
        except Exception as e:
            summary["engine_rejections"] += 1
            summary["rejection_msgs"].append(
                f"A{i} {kind} {action.get('indicator_id', '?')}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )

    try:
        stamp_last_scanned(slug)
    except Exception:
        pass

    # Closing-loop signal: should the news-scan caller dispatch the
    # schema_gap_resolver? Decided by current accumulated gaps + whether
    # proposals already pending review. Exposed in summary so the skill
    # workflow can branch.
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


def get_strict_reasons_map(matcher_decisions: list) -> dict:
    """{idx: {claim, reason}} for rebut/jury context."""
    return {
        d["idx"]: {"claim": d["claim"], "reason": d["reason"]}
        for d in matcher_decisions
    }


def filter_advocate_moves(advocate_blocks: list) -> list:
    """Just the ARGUE_MOVE entries from advocate output."""
    return [a for a in advocate_blocks if a["verdict"] == "ARGUE_MOVE"]
