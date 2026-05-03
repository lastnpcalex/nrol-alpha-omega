"""
indicator_match — match parked evidence to existing indicators.

Used by the cleanup-indicator-sweep workflow. For each parked evidence
entry, suggests candidate matching indicators ranked by semantic
similarity. Operator confirms or rejects.

This is the function whose absence allowed the freeform-LR loophole:
operators kept doing freeform updates because they didn't realize
existing indicators covered the evidence. With this module, the
cleanup workflow can surface candidate matches automatically.

Public:
  match_evidence_to_indicators(evidence_text, evidence_likelihoods, indicators,
                               top_n=5, score_threshold=0.3) -> list of matches

  direction_agreement(evidence_likelihoods, indicator_likelihoods) -> dict
    Mechanical post-check on whether a match is direction-consistent.

The match function uses sentence-transformers cosine similarity
(deterministic, free, local). Stateless per-call: no context anchoring
across multiple matches.
"""

from typing import Optional
from framework.topic_search import _get_embed_model, _embed_score, _keyword_score


def match_evidence_to_indicators(
    evidence_text: str,
    indicators: list,
    evidence_likelihoods: Optional[dict] = None,
    top_n: int = 5,
    score_threshold: float = 0.3,
) -> list:
    """
    Suggest candidate indicators that match the given evidence.

    Args:
        evidence_text: text of the parked evidence (note + text combined)
        indicators: list of indicator dicts (each with id, desc, likelihoods, etc.)
                   Should include all the topic's indicators across tiers.
        evidence_likelihoods: optional dict of LRs the operator/AI claimed
                              for this evidence. If provided, the match
                              result includes direction_agreement check.
        top_n: max candidates to return
        score_threshold: only return matches with score >= this

    Returns: list of match dicts:
        {
            indicator_id: str,
            tier: str,  # tier1_critical / tier2_strong / tier3_suggestive / anti
            score: float,  # cosine similarity 0..1
            indicator_desc: str,
            indicator_likelihoods: dict | None,
            indicator_status: str,
            direction_agreement: dict | None,  # if evidence_likelihoods provided
        }
        Sorted by score descending. Empty list if nothing scores above threshold.
    """
    if not evidence_text or not indicators:
        return []

    # Build text representation per indicator (id + desc + posteriorEffect text)
    ind_texts = []
    for ind in indicators:
        if not isinstance(ind, dict):
            continue
        text_parts = [
            ind.get("id", ""),
            ind.get("desc", ""),
            ind.get("posteriorEffect", "") if isinstance(ind.get("posteriorEffect"), str) else "",
            ind.get("note", "") or "",
        ]
        ind_texts.append(" ".join(p for p in text_parts if p))

    # Score each
    scores = _embed_score(evidence_text, ind_texts)
    if scores is None:
        # Keyword fallback
        scores = [_keyword_score(evidence_text, t) for t in ind_texts]

    matches = []
    for ind, score in zip(indicators, scores):
        if not isinstance(ind, dict):
            continue
        if score < score_threshold:
            continue
        match = {
            "indicator_id": ind.get("id"),
            "tier": ind.get("_tier", "?"),  # caller can stamp this
            "score": float(score),
            "indicator_desc": ind.get("desc", ""),
            "indicator_likelihoods": ind.get("likelihoods"),
            "indicator_status": ind.get("status"),
            "indicator_causal_event_id": ind.get("causal_event_id"),
        }
        if evidence_likelihoods and ind.get("likelihoods"):
            match["direction_agreement"] = direction_agreement(
                evidence_likelihoods, ind["likelihoods"]
            )
        matches.append(match)

    matches.sort(key=lambda m: -m["score"])
    return matches[:top_n]


def direction_agreement(evidence_lrs: dict, indicator_lrs: dict) -> dict:
    """
    Mechanical check: does the proposed direction of the evidence's LRs
    agree with the indicator's pre-committed LRs?

    Direction = which hypothesis has the maximum LR (the one the evidence
    is claimed to most favor).

    Returns:
        {
            agreement: bool,  # True if both pick the same hypothesis as max
            evidence_max_h: str,
            indicator_max_h: str,
            evidence_lr_at_indicator_max: float,
            indicator_lr_at_evidence_max: float,
            cosine_similarity: float,  # vector similarity of the LR vectors
        }
    """
    import math
    common_keys = set(evidence_lrs.keys()) & set(indicator_lrs.keys())
    if not common_keys:
        return {"agreement": False, "reason": "no common hypothesis keys"}
    # Find max for each
    ev_max_h = max(common_keys, key=lambda k: evidence_lrs[k])
    ind_max_h = max(common_keys, key=lambda k: indicator_lrs[k])
    # Cosine sim of LR vectors
    keys = sorted(common_keys)
    ev_vec = [evidence_lrs[k] for k in keys]
    ind_vec = [indicator_lrs[k] for k in keys]
    dot = sum(a*b for a, b in zip(ev_vec, ind_vec))
    ev_norm = math.sqrt(sum(a*a for a in ev_vec)) + 1e-9
    ind_norm = math.sqrt(sum(b*b for b in ind_vec)) + 1e-9
    cos_sim = dot / (ev_norm * ind_norm)
    return {
        "agreement": ev_max_h == ind_max_h,
        "evidence_max_h": ev_max_h,
        "indicator_max_h": ind_max_h,
        "evidence_lr_at_indicator_max": evidence_lrs.get(ind_max_h),
        "indicator_lr_at_evidence_max": indicator_lrs.get(ev_max_h),
        "cosine_similarity": float(cos_sim),
    }


def collect_topic_indicators(topic: dict) -> list:
    """
    Helper: flatten a topic's indicators into a list of dicts, each tagged
    with its tier. Convenience for callers of match_evidence_to_indicators.
    """
    out = []
    inds = topic.get("indicators", {}) or {}
    for tier_key in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in inds.get("tiers", {}).get(tier_key, []) or []:
            if isinstance(ind, dict):
                ind_copy = dict(ind)
                ind_copy["_tier"] = tier_key
                out.append(ind_copy)
    for ind in inds.get("anti_indicators", []) or []:
        if isinstance(ind, dict):
            ind_copy = dict(ind)
            ind_copy["_tier"] = "anti_indicators"
            out.append(ind_copy)
    return out
