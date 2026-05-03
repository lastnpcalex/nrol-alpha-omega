"""
lint_indicator_shape — subagent-mediated semantic shape checks.

Enforces that indicators do not secretly contain resolution outcomes 
disguised as marginal evidence ("resolution-disguised indicators").
Provides the checks required by the engine's IndicatorShapeReviewRequired gate.
"""

import hashlib
import json
import re

def _hash_indicator(ind: dict) -> str:
    """Compute a stable hash of the indicator's core semantic fields."""
    core = {
        "desc": ind.get("desc", ""),
        "posteriorEffect": ind.get("posteriorEffect", ""),
        "likelihoods": ind.get("likelihoods", {}),
        "shape": ind.get("shape", ""),
    }
    j = json.dumps(core, sort_keys=True)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()[:16]

def build_shape_review_prompt(topic: dict, indicator: dict) -> str:
    """Build the prompt for the resolution-disguise detection subagent."""
    meta = topic.get("meta", {})
    
    prompt = f"""You are reviewing an indicator for a Bayesian forecasting topic.
Your job is to detect if this indicator is "resolution-disguised". 
An indicator is resolution-disguised if observing its threshold effectively ENDS the question (resolves the topic) rather than just providing marginal evidence.

==== TOPIC ====
Question: {meta.get("question", "")}
Resolution Criteria: {meta.get("resolution", "")}

==== INDICATOR ====
ID: {indicator.get("id", "")}
Shape: {indicator.get("shape", "")}
Description: {indicator.get("desc", "")}

If this indicator fires, is the topic essentially resolved? Or is there still meaningful uncertainty?

Output a structured one-line decision at the end:
RESOLUTION_DISGUISE: <reason why this resolves the topic>
NOT_RESOLUTION: <reason why this is just marginal evidence>
UNCLEAR: <reason why the description is ambiguous>

No other output format.
"""
    return prompt

def parse_shape_review_decision(response_text: str) -> dict:
    """Parse the subagent's shape review decision."""
    if not response_text:
        return {"action": "error", "reason": "empty response"}

    pattern = re.compile(r"\b(RESOLUTION_DISGUISE|NOT_RESOLUTION|UNCLEAR)\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
    matches = pattern.findall(response_text)
    
    if not matches:
        return {"action": "error", "reason": "no valid decision line found"}
        
    keyword, value = matches[-1]
    keyword = keyword.upper()
    return {"action": keyword, "reason": value.strip()}

def record_shape_review(topic: dict, indicator_id: str, decisions: list[dict], subagent_ids: list[str] = None) -> dict:
    """Record the shape review result in the topic's governance block."""
    gov = topic.setdefault("governance", {})
    reviews = gov.setdefault("indicator_shape_reviews", {})
    
    # Find the indicator
    ind = None
    for tk in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for i in topic.get("indicators", {}).get("tiers", {}).get(tk, []):
            if i.get("id") == indicator_id:
                ind = i
                break
        if ind:
            break
    if not ind:
        for i in topic.get("indicators", {}).get("anti_indicators", []):
            if i.get("id") == indicator_id:
                ind = i
                break
                
    if not ind:
        raise ValueError(f"Indicator {indicator_id} not found.")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    
    # Determine consensus
    disguise_count = sum(1 for d in decisions if d["action"] == "RESOLUTION_DISGUISE")
    not_res_count = sum(1 for d in decisions if d["action"] == "NOT_RESOLUTION")
    
    final_decision = "NOT_RESOLUTION"
    if disguise_count > 0 and disguise_count >= not_res_count:
        final_decision = "RESOLUTION_DISGUISE"
    elif not decisions:
        final_decision = "ERROR"

    reviews[indicator_id] = {
        "reviewed_at": now,
        "schema_hash": _hash_indicator(ind),
        "decision": final_decision,
        "details": decisions,
        "subagent_dispatch_ids": subagent_ids or [],
    }
    return topic

def verify_shape_review(topic: dict, indicator_id: str) -> tuple[bool, str]:
    """Verify that an indicator has a valid, non-stale shape review."""
    gov = topic.get("governance", {})
    reviews = gov.get("indicator_shape_reviews", {})
    
    if indicator_id not in reviews:
        return False, "no shape review found"
        
    review = reviews[indicator_id]
    
    # Find indicator to check hash
    ind = None
    for tk in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for i in topic.get("indicators", {}).get("tiers", {}).get(tk, []):
            if i.get("id") == indicator_id:
                ind = i
                break
        if ind:
            break
    if not ind:
        for i in topic.get("indicators", {}).get("anti_indicators", []):
            if i.get("id") == indicator_id:
                ind = i
                break

    if not ind:
        return False, "indicator not found"
        
    current_hash = _hash_indicator(ind)
    if review.get("schema_hash") != current_hash:
        return False, f"shape review is stale (schema hash mismatch: {review.get('schema_hash')} != {current_hash})"
        
    if review.get("decision") == "RESOLUTION_DISGUISE":
        return False, "indicator was flagged as a RESOLUTION_DISGUISE"
        
    return True, "review valid"
