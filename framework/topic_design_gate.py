#!/usr/bin/env python3
"""
NRL-Alpha Omega — Topic Design Gate
====================================

Pre-governor structural review for new topic designs. This module runs
BEFORE a topic enters the governor's jurisdiction — it validates that
the hypothesis space, indicators, resolution criteria, and actor model
are structurally sound.

Two modes:
  1. Mechanical checks (run_mechanical_checks) — pure code, no LLM needed.
     Catches structural issues: missing fields, empty indicator tiers,
     hypotheses without anti-indicators, undated resolution criteria, etc.

  2. Adversarial review prompt (generate_review_prompt) — produces a fixed
     prompt for an LLM subagent to review the topic design. The subagent
     acts as an adversarial examiner, not a collaborator. It must PASS or
     FAIL the topic with specific objections.

The gate is a pre-commit hook for topic design. It fires once per topic,
not per update. The governor gates every UPDATE; this gates the TOPIC ITSELF.

Usage:
    from framework.topic_design_gate import run_mechanical_checks, generate_review_prompt

    # Step 1: mechanical checks (instant, no LLM)
    issues = run_mechanical_checks(topic)
    if issues["blockers"]:
        print("BLOCKED:", issues["blockers"])
        sys.exit(1)

    # Step 2: generate prompt for LLM adversarial review
    prompt = generate_review_prompt(topic)
    # Feed `prompt` to a subagent. The subagent returns PASS/FAIL + objections.
    # Operator must address all objections before topic enters the system.

No external dependencies — Python stdlib only.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# 1. Mechanical Checks (no LLM required)
# ============================================================================

def run_mechanical_checks(topic: dict) -> dict:
    """
    Structural validation of topic design. Returns dict with:
        blockers: list[str]  — must fix before proceeding
        warnings: list[str]  — should fix, but not blocking
        info: list[str]      — informational notes

    Blockers prevent the topic from entering the system.
    Warnings are logged but don't block.
    """
    blockers = []
    warnings = []
    info = []

    meta = topic.get("meta", {})
    model = topic.get("model", {})
    hypotheses = model.get("hypotheses", {})
    indicators = topic.get("indicators", {})
    tiers = indicators.get("tiers", {})
    actor_model = topic.get("actorModel", {})

    # --- Meta ---
    if not meta.get("question"):
        blockers.append("meta.question is empty — the topic has no question to answer")
    if not meta.get("resolution"):
        blockers.append("meta.resolution is empty — no observable criterion for resolution")
    if not meta.get("slug"):
        blockers.append("meta.slug is empty — topic has no identifier")

    # Resolution should mention observability
    resolution = meta.get("resolution", "")
    if resolution and not any(w in resolution.lower() for w in
            ["observ", "confirm", "official", "announce", "publish", "report",
             "measur", "record", "data", "signed", "declared", "AP call"]):
        warnings.append(
            "meta.resolution may not be objectively observable — "
            "does it describe something a third party could verify?"
        )

    # --- Hypotheses ---
    if len(hypotheses) < 3:
        blockers.append(
            f"Only {len(hypotheses)} hypotheses — need at least 3 for "
            "meaningful posterior discrimination"
        )

    if len(hypotheses) >= 2:
        posteriors = [h.get("posterior", 0) for h in hypotheses.values()]
        total = sum(posteriors)
        if abs(total - 1.0) > 0.01:
            blockers.append(
                f"Posteriors sum to {total:.3f}, not 1.0"
            )

    # Check for degenerate hypotheses (labels too similar)
    labels = [h.get("label", "").lower() for h in hypotheses.values()]
    for i, l1 in enumerate(labels):
        for j, l2 in enumerate(labels):
            if i < j:
                # Simple word overlap check
                words1 = set(l1.split())
                words2 = set(l2.split())
                if len(words1) > 3 and len(words2) > 3:
                    overlap = len(words1 & words2) / min(len(words1), len(words2))
                    if overlap > 0.7:
                        h_keys = list(hypotheses.keys())
                        warnings.append(
                            f"{h_keys[i]} and {h_keys[j]} labels overlap "
                            f"by {overlap:.0%} — are these actually distinct outcomes?"
                        )

    # --- Indicators ---
    all_indicators = []
    for tier_name in ["tier1_critical", "tier2_strong", "tier3_suggestive"]:
        tier_indicators = tiers.get(tier_name, [])
        all_indicators.extend(tier_indicators)

    anti_indicators = tiers.get("anti_indicators", [])

    if not all_indicators:
        blockers.append(
            "No indicators defined — the topic has no observable signals "
            "that would trigger posterior updates"
        )

    if len(tiers.get("tier1_critical", [])) == 0:
        warnings.append(
            "No tier-1 (critical) indicators — what would it take to "
            "near-certainly resolve this question?"
        )

    if not anti_indicators:
        warnings.append(
            "No anti-indicators defined — every hypothesis needs at least "
            "one indicator that would REDUCE its probability"
        )

    # Check which hypotheses have anti-indicators
    h_keys = set(hypotheses.keys())
    covered_by_anti = set()
    for ai in anti_indicators:
        effect = ai.get("posteriorEffect", "")
        for hk in h_keys:
            if hk in effect:
                covered_by_anti.add(hk)
    uncovered = h_keys - covered_by_anti
    if uncovered and anti_indicators:  # only warn if they tried but missed some
        warnings.append(
            f"Hypotheses {uncovered} have no anti-indicator — "
            "what evidence would make these LESS likely?"
        )

    # Check indicator observability
    for ind in all_indicators + anti_indicators:
        desc = ind.get("desc", "").lower()
        if any(w in desc for w in ["believe", "think", "feel", "seem", "likely"]):
            warnings.append(
                f"Indicator '{ind.get('id', '?')}' may not be objectively "
                f"observable — description uses subjective language: "
                f"'{ind.get('desc', '')[:60]}...'"
            )

    # --- Coverage Matrix (hypothesis × indicator) ---
    # For each indicator, parse which hypotheses it references in posteriorEffect.
    # Build a matrix: which hypotheses can be POSITIVELY updated and which can be
    # NEGATIVELY updated by the indicator set? Gaps are structural flaws.
    coverage = _build_coverage_matrix(hypotheses, all_indicators, anti_indicators)
    info.append(f"COVERAGE_MATRIX: {json.dumps(coverage['matrix'])}")

    for hk in h_keys:
        pos = coverage["positive"].get(hk, [])
        neg = coverage["negative"].get(hk, [])
        if not pos and not neg:
            blockers.append(
                f"COVERAGE GAP: {hk} has no indicators for or against it — "
                "this hypothesis is permanently underdetermined"
            )
        elif not pos:
            warnings.append(
                f"COVERAGE ASYMMETRY: {hk} has {len(neg)} negative indicator(s) "
                f"but no positive ones — it can only lose probability, never gain"
            )
        elif not neg:
            warnings.append(
                f"COVERAGE ASYMMETRY: {hk} has {len(pos)} positive indicator(s) "
                f"but no negative ones — it can only gain probability, never lose"
            )

    # --- Distinguishability (hypothesis pairs sharing all indicators) ---
    # If two hypotheses are affected by EXACTLY the same set of indicators,
    # no evidence can discriminate between them. This is mechanically detectable.
    distinguishability = _check_distinguishability(coverage)
    for pair in distinguishability["indistinguishable"]:
        warnings.append(
            f"INDISTINGUISHABLE: {pair['h1']} and {pair['h2']} share "
            f"{pair['shared']}/{pair['total']} indicators "
            f"(overlap {pair['overlap']:.0%}) — consider merging or adding "
            f"a discriminating indicator"
        )

    # --- Prior Justification ---
    posteriors = {hk: h.get("posterior", 0) for hk, h in hypotheses.items()}
    is_uniform = all(abs(p - 1.0/len(posteriors)) < 0.01 for p in posteriors.values())
    history = model.get("posteriorHistory", [])
    has_prior_note = bool(history and history[0].get("note"))

    if is_uniform and len(hypotheses) >= 3:
        warnings.append(
            f"UNIFORM PRIORS ({1.0/len(posteriors):.2f} each) — is this "
            "honest ignorance or lazy defaulting? If you have domain knowledge, "
            "use it. If genuinely ignorant, document why."
        )
    elif not is_uniform and not has_prior_note:
        blockers.append(
            f"NON-UNIFORM PRIORS without justification — posteriors are "
            f"{posteriors} but posteriorHistory has no initial note explaining "
            "the asymmetry. Add a posteriorHistory entry documenting why."
        )

    # --- Actor Model ---
    actors = actor_model.get("actors", {})
    if not actors:
        warnings.append(
            "No actor model — who makes the decisions that drive this outcome? "
            "Even for science topics, institutional actors matter."
        )

    # --- Data Feeds ---
    feeds = topic.get("dataFeeds", {})
    if not feeds:
        info.append(
            "No data feeds defined — consider adding quantitative "
            "metrics that can be tracked over time"
        )

    # --- Search Queries ---
    queries = topic.get("searchQueries", [])
    if not queries:
        info.append(
            "No search queries defined — how will the operator "
            "find new evidence for this topic?"
        )

    return {
        "blockers": blockers,
        "warnings": warnings,
        "info": info,
        "passed": len(blockers) == 0,
        "coverage": coverage,
        "distinguishability": distinguishability,
    }


def _build_coverage_matrix(hypotheses: dict, indicators: list,
                           anti_indicators: list) -> dict:
    """
    Build a hypothesis × indicator coverage matrix.

    For each indicator, parse posteriorEffect to determine which hypotheses
    it affects and in which direction (positive = increases probability,
    negative = decreases probability).

    Returns dict with:
        matrix: {H1: {pos: [ind_ids], neg: [ind_ids]}, ...}
        positive: {H1: [ind_ids that can increase H1], ...}
        negative: {H1: [ind_ids that can decrease H1], ...}
        indicator_reach: {ind_id: [hypothesis_keys it affects]}
    """
    import re as _re

    h_keys = list(hypotheses.keys())
    matrix = {hk: {"pos": [], "neg": []} for hk in h_keys}
    indicator_reach = {}

    for ind in indicators + anti_indicators:
        ind_id = ind.get("id", "?")
        effect = ind.get("posteriorEffect", "")
        reached = []

        for hk in h_keys:
            if hk in effect:
                reached.append(hk)
                # Determine direction from effect string
                # Patterns: "H1 +15pp", "H3 surge", "H2 -5pp", "H1 collapse"
                # Look for the hypothesis mention and its nearby context
                pattern = _re.compile(
                    rf'{hk}\s*([+-])?\s*(\d+)?\s*(?:pp|%)?|'
                    rf'{hk}.*?(surge|increase|up|gain|rise|confirm)|'
                    rf'{hk}.*?(collapse|decrease|down|drop|reduce|decline)',
                    _re.IGNORECASE
                )
                match = pattern.search(effect)
                if match:
                    if match.group(1) == '+' or match.group(3):  # +Npp or surge/increase
                        matrix[hk]["pos"].append(ind_id)
                    elif match.group(1) == '-' or match.group(4):  # -Npp or collapse/decrease
                        matrix[hk]["neg"].append(ind_id)
                    else:
                        # Ambiguous — count as both
                        matrix[hk]["pos"].append(ind_id)
                        matrix[hk]["neg"].append(ind_id)
                else:
                    # H mentioned but no clear direction — count as affecting
                    matrix[hk]["pos"].append(ind_id)

        indicator_reach[ind_id] = reached

    positive = {hk: matrix[hk]["pos"] for hk in h_keys}
    negative = {hk: matrix[hk]["neg"] for hk in h_keys}

    return {
        "matrix": {hk: {"pos": len(v["pos"]), "neg": len(v["neg"])}
                   for hk, v in matrix.items()},
        "positive": positive,
        "negative": negative,
        "indicator_reach": indicator_reach,
    }


def _check_distinguishability(coverage: dict) -> dict:
    """
    Check if any pair of hypotheses are indistinguishable — meaning
    they are affected by exactly the same set of indicators.

    Two hypotheses that share all indicators cannot be discriminated
    by evidence. This is a structural flaw, not a judgment call.

    Returns dict with:
        indistinguishable: list of {h1, h2, shared, total, overlap}
        distinguishable: list of {h1, h2, shared, total, overlap}
    """
    positive = coverage["positive"]
    negative = coverage["negative"]
    h_keys = list(positive.keys())

    indistinguishable = []
    distinguishable = []

    for i, h1 in enumerate(h_keys):
        for j, h2 in enumerate(h_keys):
            if i >= j:
                continue

            # All indicators affecting either hypothesis
            all_h1 = set(positive.get(h1, []) + negative.get(h1, []))
            all_h2 = set(positive.get(h2, []) + negative.get(h2, []))
            union = all_h1 | all_h2
            intersection = all_h1 & all_h2

            if not union:
                # Neither hypothesis has any indicators — already caught
                continue

            overlap = len(intersection) / len(union) if union else 0

            entry = {
                "h1": h1, "h2": h2,
                "shared": len(intersection),
                "total": len(union),
                "overlap": round(overlap, 2),
                "unique_to_h1": list(all_h1 - all_h2),
                "unique_to_h2": list(all_h2 - all_h1),
            }

            # >80% overlap with no unique discriminators = indistinguishable
            if overlap > 0.8 and not (all_h1 - all_h2) and not (all_h2 - all_h1):
                indistinguishable.append(entry)
            else:
                distinguishable.append(entry)

    return {
        "indistinguishable": indistinguishable,
        "distinguishable": distinguishable,
    }


# ============================================================================
# 2. Adversarial Review Prompt (for LLM subagent)
# ============================================================================

_REVIEW_PROMPT_TEMPLATE = """You are a topic design examiner for a Bayesian estimation engine. Your job is to find structural flaws in a proposed forecasting topic BEFORE it enters the system. You are adversarial, not collaborative. Your goal is to prevent bad topics from wasting months of tracking.

You will receive a topic JSON. You must check each item below and give a verdict: PASS or FAIL with a specific objection. Do not be nice. Do not suggest improvements — just identify what's broken.

## Checklist

### 1. HYPOTHESIS SPACE COMPLETENESS
- Are there plausible real-world outcomes not covered by H1-H4?
- If the actual outcome falls between two hypotheses, which one captures it?
- Is there an implicit "none of the above" that should be explicit?
- Would a domain expert look at this hypothesis space and say "you forgot X"?

Verdict: PASS / FAIL + what's missing

### 2. HYPOTHESIS DISTINGUISHABILITY
- Can each hypothesis be distinguished from the others by observable evidence?
- If H2 and H3 would produce the same evidence, they should be merged.
- Are the hypotheses defined by outcomes (good) or by mechanisms (risky — mechanisms can overlap)?

Verdict: PASS / FAIL + which hypotheses blur together

### 3. RESOLUTION CRITERIA
- Is the resolution criterion observable by a disinterested third party?
- Is there a specific date or trigger, or is it open-ended?
- What happens if the topic reaches its horizon and nothing has definitively resolved?
- Could reasonable people disagree about whether resolution occurred?

Verdict: PASS / FAIL + what's ambiguous

### 4. INDICATOR FALSIFIABILITY
- For each indicator: is the firing threshold objectively measurable?
- Could two observers disagree about whether an indicator fired?
- Are there indicators for BOTH directions (escalation and de-escalation)?
- Does every hypothesis have at least one indicator that would make it MORE likely and one that would make it LESS likely?

Verdict: PASS / FAIL + which indicators are subjective or missing

### 5. INDICATOR COVERAGE
- Which hypotheses have no tier-1 indicator? Why not?
- Are there observable events that would near-certainly resolve this but aren't listed?
- Is the indicator set front-loaded (things that happen early) or back-loaded (only at resolution)?

Verdict: PASS / FAIL + coverage gaps

### 6. ACTOR MODEL REALISM
- Are the listed actors actually the decision-makers who drive this outcome?
- Are decision styles plausible and based on observed behavior, not stereotypes?
- Are there actors missing who could change the outcome?

Verdict: PASS / FAIL + who's missing or mischaracterized

### 7. EVIDENCE FEED FEASIBILITY
- Can the operator actually obtain the evidence needed to update this topic?
- Are there data feeds that exist in principle but are practically inaccessible?
- Is this topic trackable with web search, or does it require classified/paywalled sources?

Verdict: PASS / FAIL + what's inaccessible

### 8. PRIOR JUSTIFICATION
- If priors are uniform (0.25 each): is that honest ignorance or lazy defaulting?
- If priors are non-uniform: what justifies the asymmetry?
- Are the priors consistent with publicly available base rates?

Verdict: PASS / FAIL + what's unjustified

## Output format

For each check, output:

```
CHECK N: [name]
VERDICT: PASS / FAIL
OBJECTION: [specific issue, if FAIL]
```

Then a final summary:

```
OVERALL: PASS / FAIL
BLOCKING OBJECTIONS: [list, if any]
NON-BLOCKING CONCERNS: [list, if any]
```

A topic FAILS overall if ANY check is FAIL. The operator must address every FAIL before the topic enters the system.

## The topic to review:

```json
{topic_json}
```

Now review this topic. Be thorough and adversarial. Remember: a bad topic that passes your review wastes months of tracking and corrupts the calibration corpus. A good topic that you fail just needs revisions. Err on the side of failing.
"""


def generate_review_prompt(topic: dict) -> str:
    """
    Generate the fixed adversarial review prompt for a topic design.

    Returns a string that can be fed to any LLM subagent. The prompt
    is fixed — the only variable is the topic JSON. The subagent's
    job is to return PASS/FAIL per check with specific objections.
    """
    # Strip large runtime data that the reviewer doesn't need
    review_copy = {}
    for key in ["meta", "model", "indicators", "actorModel", "dataFeeds",
                "tagConfig", "searchQueries", "watchpoints", "subModels"]:
        if key in topic:
            review_copy[key] = topic[key]

    # Strip evidence log and scoring (design review, not runtime review)
    # But keep posteriorHistory if it exists (shows prior trajectory)
    if "model" in review_copy:
        model_copy = dict(review_copy["model"])
        # Keep first and last posteriorHistory entry only (for prior context)
        history = model_copy.get("posteriorHistory", [])
        if len(history) > 2:
            model_copy["posteriorHistory"] = [
                history[0],
                {"_note": f"... {len(history) - 2} entries omitted ..."},
                history[-1],
            ]
        review_copy["model"] = model_copy

    topic_json = json.dumps(review_copy, indent=2, default=str)
    return _REVIEW_PROMPT_TEMPLATE.replace("{topic_json}", topic_json)


# ============================================================================
# 3. Parse Review Response
# ============================================================================

def parse_review_response(response_text: str) -> dict:
    """
    Parse an LLM subagent's review response into structured results.

    Returns dict with:
        checks: list[dict] — each with name, verdict, objection
        overall: "PASS" | "FAIL"
        blocking: list[str]
        non_blocking: list[str]
        raw: str — the original response
    """
    import re

    checks = []
    current_check = None

    for line in response_text.split("\n"):
        line = line.strip()

        # Match CHECK N: name
        m = re.match(r"CHECK\s+(\d+)\s*:\s*(.+)", line, re.IGNORECASE)
        if m:
            if current_check:
                checks.append(current_check)
            current_check = {
                "number": int(m.group(1)),
                "name": m.group(2).strip(),
                "verdict": None,
                "objection": None,
            }
            continue

        # Match VERDICT: PASS/FAIL
        m = re.match(r"VERDICT\s*:\s*(PASS|FAIL)", line, re.IGNORECASE)
        if m and current_check:
            current_check["verdict"] = m.group(1).upper()
            continue

        # Match OBJECTION: text
        m = re.match(r"OBJECTION\s*:\s*(.+)", line, re.IGNORECASE)
        if m and current_check:
            current_check["objection"] = m.group(1).strip()
            continue

    if current_check:
        checks.append(current_check)

    # Parse overall
    overall = "UNKNOWN"
    m = re.search(r"OVERALL\s*:\s*(PASS|FAIL)", response_text, re.IGNORECASE)
    if m:
        overall = m.group(1).upper()

    # Parse blocking objections
    blocking = []
    non_blocking = []
    for check in checks:
        if check["verdict"] == "FAIL" and check["objection"]:
            blocking.append(f"Check {check['number']} ({check['name']}): {check['objection']}")
        elif check["objection"]:
            non_blocking.append(f"Check {check['number']} ({check['name']}): {check['objection']}")

    return {
        "checks": checks,
        "overall": overall,
        "blocking": blocking,
        "non_blocking": non_blocking,
        "raw": response_text,
    }


# ============================================================================
# 4. Full Gate (mechanical + prompt generation)
# ============================================================================

def run_design_gate(topic: dict) -> dict:
    """
    Run the full design gate: mechanical checks + generate review prompt.

    Returns dict with:
        mechanical: dict from run_mechanical_checks()
        review_prompt: str (for feeding to subagent)
        ready_for_review: bool (True if mechanical checks passed)
    """
    mechanical = run_mechanical_checks(topic)

    if not mechanical["passed"]:
        return {
            "mechanical": mechanical,
            "review_prompt": None,
            "ready_for_review": False,
        }

    return {
        "mechanical": mechanical,
        "review_prompt": generate_review_prompt(topic),
        "ready_for_review": True,
    }


# ============================================================================
# CLI: run against a topic file
# ============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python topic_design_gate.py <topic_file.json>")
        print("       python topic_design_gate.py <topic_file.json> --prompt")
        sys.exit(1)

    topic_path = Path(sys.argv[1])
    if not topic_path.exists():
        # Try topics/ prefix
        topic_path = Path("topics") / sys.argv[1]
        if not topic_path.exists():
            topic_path = Path("topics") / (sys.argv[1] + ".json")

    if not topic_path.exists():
        print(f"Topic file not found: {sys.argv[1]}")
        sys.exit(1)

    with open(topic_path, encoding="utf-8") as f:
        topic = json.load(f)

    gate = run_design_gate(topic)
    mechanical = gate["mechanical"]

    print(f"=== Topic Design Gate: {topic.get('meta', {}).get('slug', '?')} ===\n")

    if mechanical["blockers"]:
        print("BLOCKERS (must fix):")
        for b in mechanical["blockers"]:
            print(f"  [X] {b}")
        print()

    if mechanical["warnings"]:
        print("WARNINGS (should fix):")
        for w in mechanical["warnings"]:
            print(f"  [!] {w}")
        print()

    if mechanical["info"]:
        print("INFO:")
        for i in mechanical["info"]:
            print(f"  [i] {i}")
        print()

    if gate["ready_for_review"]:
        print("MECHANICAL: PASSED")
        if "--prompt" in sys.argv:
            print("\n--- Adversarial Review Prompt ---\n")
            print(gate["review_prompt"])
        else:
            print("Run with --prompt to generate the adversarial review prompt")
    else:
        print("MECHANICAL: BLOCKED — fix blockers before adversarial review")
