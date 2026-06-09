"""
News-flow simulation gate (Phase 3.5 operational validator).

Phase 3 lint validates schema *correctness* (LRs honest, no resolution
disguise, no compound projection). Phase 3.5 backtests *point-LR
calibration* against fixture data. Neither validates that the topic
actually responds to news flow — i.e., whether posteriors move toward
the correct hypothesis when realistic news under that hypothesis arrives.

This module fills that gap. Per hypothesis:
  1. Generate plausible synthetic news under H_i (subagent dispatch)
  2. Route the synthetic corpus through the matcher (subagent dispatch)
  3. Apply decisions in a sandbox (cloned topic, posteriors not persisted)
  4. Verify posterior(H_i) increases vs. baseline AND no article produces
     wrong-direction shift (e.g., recovery news raising no-recovery H)
  5. Return per-H verdict

A topic that fails the gate ships with `calibrationStatus` blocked until
the schema is extended to make news flow under each H actually move
posteriors.

Public:
  build_synthetic_news_prompt(topic, hypothesis_key, n_articles=5) -> str
  parse_synthetic_corpus(response_text) -> list[article_dict]
  simulate_per_hypothesis(topic, slug, hypothesis_key, articles) -> dict
  evaluate_news_flow_responsiveness(...) -> dict  # full gate

Cost (per topic): n_hypotheses x 2 subagent dispatches (synth + match)
plus a third for matcher-on-each-corpus. Roughly ~30 dispatches per topic.
Run once at topic-design Phase 3.5; persisted to
governance.newsFlowSimulation. Re-run on schema changes.
"""

import copy
import json
import re
from typing import Optional

from engine import load_topic


def build_synthetic_news_prompt(topic: dict, hypothesis_key: str,
                                 n_articles: int = 5) -> str:
    """
    Build a prompt asking a fresh-context subagent to generate N
    plausible news articles consistent with `hypothesis_key` being the
    correct outcome for the topic. Articles should be:
      - Specific and dated (within the topic's tracking window)
      - Drawn from realistic news vocabulary (not academic / reference-class)
      - Diverse: cover different evidence dimensions implied by the
        hypothesis being true
    """
    meta = topic.get("meta", {})
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    h_obj = hypotheses.get(hypothesis_key) or {}
    h_label = h_obj.get("label") or h_obj.get("desc") or h_obj.get("description") or hypothesis_key

    indicators_block = topic.get("indicators") or {}
    all_inds = []
    for tier_list in (indicators_block.get("tiers") or {}).values():
        if isinstance(tier_list, list):
            all_inds.extend(tier_list)
    all_inds.extend(indicators_block.get("anti_indicators") or [])

    other_h_summary = "\n".join(
        f"  {hk}: {(hv.get('label') or hv.get('desc') or '?')[:100]}"
        for hk, hv in hypotheses.items() if hk != hypothesis_key
    )

    return f"""You are generating SYNTHETIC NEWS articles for a Phase 3.5 simulation
gate. Your output validates whether the topic's indicator schema can
correctly update posteriors when news under each hypothesis arrives.

==== TOPIC ====
Slug:     {meta.get('slug', '?')}
Question: {meta.get('question', '?')}

==== HYPOTHESIS BEING SIMULATED ====
{hypothesis_key}: {h_label}

==== OTHER HYPOTHESES (for differentiation) ====
{other_h_summary}

==== TASK ====
Generate {n_articles} plausible news articles that, IF the world were to
unfold under {hypothesis_key}, you would expect to see published over
the topic's tracking window. The articles should:

1. Be diverse across evidence dimensions (events, data prints, statements,
   operations, market reactions). Don't pile all articles on the same
   sub-event.
2. Use realistic news vocabulary — wire-service framing, named sources,
   numeric values where appropriate. Not academic, not reference-class.
3. Differentiate {hypothesis_key} from neighboring hypotheses. An article
   that's compatible with multiple H is weak signal; aim for articles
   that, conditional on accuracy, would clearly point to {hypothesis_key}.
4. Be specific: include quantitative values, named entities, dates within
   the tracking window where natural.
5. Avoid forecasts and analyst opinion (those would IGNORE in the matcher).

==== OUTPUT FORMAT (one block per article, no preamble) ====

ARTICLE
HEADLINE: <plausible news headline>
SOURCE: <wire / outlet / agency name>
DATE: <YYYY-MM-DD within tracking window>
BODY: <2-4 sentence summary in news prose>
EVIDENCE_DIMENSION: <which dimension this article covers — event, data
                    print, statement, operation, market reaction, etc.>
END
"""


_ARTICLE_BLOCK = re.compile(
    r"ARTICLE\s*\n"
    r"HEADLINE:\s*([^\n]+)\n"
    r"SOURCE:\s*([^\n]+)\n"
    r"DATE:\s*([^\n]+)\n"
    r"BODY:\s*((?:.|\n)+?)\n"
    r"EVIDENCE_DIMENSION:\s*([^\n]+)\n"
    r".*?END",
    re.MULTILINE | re.DOTALL,
)


def parse_synthetic_corpus(response_text: str) -> list:
    """Parse subagent's synthetic article output into a list of dicts."""
    out = []
    for m in _ARTICLE_BLOCK.finditer(response_text or ""):
        out.append({
            "headline": m.group(1).strip(),
            "source": m.group(2).strip(),
            "date": m.group(3).strip(),
            "body": m.group(4).strip(),
            "evidence_dimension": m.group(5).strip(),
        })
    return out


def to_matcher_article_format(synthetic_article: dict, channel: str = "synthetic") -> dict:
    """Adapt synthetic articles to the format the matcher expects."""
    return {
        "article": {
            "headline": synthetic_article.get("headline", ""),
            "url": "",  # synthetic — no URL
            "source": synthetic_article.get("source", "synthetic"),
            "date": synthetic_article.get("date", ""),
            "relevance": synthetic_article.get("body", ""),
        },
        "channels": [channel],
    }


def simulate_per_hypothesis(
    topic: dict,
    hypothesis_key: str,
    matcher_decisions: list,
    articles: list,
) -> dict:
    """
    Apply matcher decisions to a CLONE of the topic, return per-H
    posterior shifts and per-article direction-correctness.

    Args:
        topic: original topic dict (won't be mutated)
        hypothesis_key: the H this corpus was generated under
        matcher_decisions: parsed matcher output for the synthetic corpus
        articles: synthetic articles in matcher format

    Returns:
        {
            "hypothesis": hypothesis_key,
            "posterior_baseline": {H: float, ...},
            "posterior_simulated": {H: float, ...},
            "shift_toward_h": float,        # signed pp shift on hypothesis_key
            "wrong_direction_count": int,    # decisions that pushed posterior
                                              # opposite to article content
            "schema_gap_count": int,         # SCHEMA_GAP outcomes
            "applied_count": int,
            "verdict": "PASS" | "WEAK" | "FAIL",
            "verdict_reason": str,
        }

    PASS:  shift_toward_h > 0.02 (≥2pp), wrong_direction_count == 0
    WEAK:  shift_toward_h > 0  but < 0.02, OR shift_toward_h > 0.02 but
           1-2 wrong-direction outcomes (recoverable but flag)
    FAIL:  shift_toward_h <= 0 OR > 2 wrong-direction outcomes
    """
    hyps = topic.get("model", {}).get("hypotheses", {})
    baseline = {hk: hv.get("posterior", 0.0) for hk, hv in hyps.items()}

    # Walk the topic to compute simulated posteriors WITHOUT persisting.
    # We can't safely call apply_decisions here because it would mutate
    # topic state. Instead, replay LR multiplications manually using the
    # likelihood_models.evaluate function (no I/O, pure arithmetic).
    from framework.likelihood_models import evaluate

    def _walk_inds(o):
        out = []
        if isinstance(o, dict):
            if "id" in o and "likelihoods" in o:
                out.append(o)
            else:
                for v in o.values():
                    out += _walk_inds(v)
        elif isinstance(o, list):
            for v in o:
                out += _walk_inds(v)
        return out

    inds_by_id = {i["id"]: i for i in _walk_inds(topic.get("indicators") or {})}

    posteriors = dict(baseline)
    applied = 0
    wrong_dir = 0
    schema_gap = 0

    for d in matcher_decisions:
        action = d.get("action", {})
        kind = action.get("kind")
        if kind == "SCHEMA_GAP":
            schema_gap += 1
            continue
        if kind not in ("OBSERVE", "FIRE"):
            continue
        ind_id = action.get("indicator_id")
        ind = inds_by_id.get(ind_id)
        if not ind:
            continue
        committed_lr = ind.get("likelihoods") or {}
        if kind == "OBSERVE":
            ob = ind.get("observable") or {}
            try:
                derived = evaluate(ob, committed_lr, action.get("value"))
            except Exception:
                continue
        else:  # FIRE
            derived = dict(committed_lr)

        # Direction check: is the hypothesis_key the favored H of derived?
        # Or at least is hypothesis_key NOT the most-disfavored H?
        try:
            favored = max(derived.items(), key=lambda kv: float(kv[1]))[0]
        except (TypeError, ValueError):
            continue
        if favored != hypothesis_key:
            # Possibly wrong-direction. Tolerate if hypothesis_key is the
            # 2nd or 3rd most favored — only flag if it's the LEAST favored.
            sorted_h = sorted(derived.items(), key=lambda kv: -float(kv[1]))
            ranks = [hk for hk, _ in sorted_h]
            if ranks[-1] == hypothesis_key:
                wrong_dir += 1

        # Apply LR via Bayes (manual, in-memory)
        unnorm = {h: posteriors[h] * float(derived.get(h, 1.0)) for h in posteriors}
        total = sum(unnorm.values())
        if total > 0:
            posteriors = {h: v / total for h, v in unnorm.items()}
        applied += 1

    shift = posteriors.get(hypothesis_key, 0.0) - baseline.get(hypothesis_key, 0.0)

    if shift <= 0 or wrong_dir > 2:
        verdict = "FAIL"
        reason = (
            f"shift_toward_{hypothesis_key}={shift:+.4f}, "
            f"wrong_direction_count={wrong_dir}, applied={applied}"
        )
    elif shift < 0.02 or wrong_dir > 0:
        verdict = "WEAK"
        reason = (
            f"shift_toward_{hypothesis_key}={shift:+.4f} "
            f"(below 2pp threshold for PASS), wrong_direction_count={wrong_dir}"
        )
    else:
        verdict = "PASS"
        reason = (
            f"shift_toward_{hypothesis_key}={shift:+.4f}, "
            f"wrong_direction_count=0, applied={applied}"
        )

    return {
        "hypothesis": hypothesis_key,
        "posterior_baseline": baseline,
        "posterior_simulated": posteriors,
        "shift_toward_h": shift,
        "wrong_direction_count": wrong_dir,
        "schema_gap_count": schema_gap,
        "applied_count": applied,
        "verdict": verdict,
        "verdict_reason": reason,
    }


def evaluate_news_flow_responsiveness(per_h_results: list) -> dict:
    """
    Aggregate per-H simulation results into the topic-level gate verdict.

    Topic-level rules:
      PASS         — every H has PASS verdict
      VALIDATED_WITH_FLAGS — at most 1 H has WEAK; rest PASS
      FAIL         — any H has FAIL OR > 1 H has WEAK

    Returns:
        {
            "verdict": "PASS" | "VALIDATED_WITH_FLAGS" | "FAIL",
            "n_h_pass": int,
            "n_h_weak": int,
            "n_h_fail": int,
            "details": [...],   # per-H results
            "summary": str,
        }
    """
    counts = {"PASS": 0, "WEAK": 0, "FAIL": 0}
    for r in per_h_results:
        counts[r.get("verdict", "FAIL")] += 1

    if counts["FAIL"] > 0:
        verdict = "FAIL"
    elif counts["WEAK"] > 1:
        verdict = "FAIL"
    elif counts["WEAK"] == 1:
        verdict = "VALIDATED_WITH_FLAGS"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "n_h_pass": counts["PASS"],
        "n_h_weak": counts["WEAK"],
        "n_h_fail": counts["FAIL"],
        "details": per_h_results,
        "summary": (
            f"news-flow simulation: {verdict} "
            f"({counts['PASS']} PASS / {counts['WEAK']} WEAK / "
            f"{counts['FAIL']} FAIL)"
        ),
    }
