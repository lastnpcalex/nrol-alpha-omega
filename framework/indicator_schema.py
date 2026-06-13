"""Shared indicator-schema helpers.

Canonical schema: topic["indicators"]["anti_indicators"] is a sibling of
"tiers". Readers remain backward-compatible with legacy
"indicators.tiers.anti_indicators" while topic files are migrated.
"""

from __future__ import annotations

import re
from typing import Iterable

TIER_KEYS = ("tier1_critical", "tier2_strong", "tier3_suggestive")


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _dedup_indicators(items: Iterable[dict]) -> list[dict]:
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("id") or id(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def tier_indicators_for_topic(topic: dict) -> list[dict]:
    """Return ordinary tier indicators only, excluding anti-indicators."""
    inds = topic.get("indicators") or {}
    tiers = inds.get("tiers") or {}
    out = []
    for tier_key in TIER_KEYS:
        out.extend(i for i in _list(tiers.get(tier_key)) if isinstance(i, dict))
    return out


def anti_indicators_for_topic(topic: dict) -> list[dict]:
    """Return anti-indicators from the canonical location plus legacy fallback."""
    inds = topic.get("indicators") or {}
    tiers = inds.get("tiers") or {}
    return _dedup_indicators(_list(inds.get("anti_indicators")) + _list(tiers.get("anti_indicators")))


def iter_indicators_for_topic(topic: dict, include_anti: bool = True):
    """Yield (tier_key, indicator) pairs across the topic schema."""
    inds = topic.get("indicators") or {}
    tiers = inds.get("tiers") or {}
    for tier_key in TIER_KEYS:
        for ind in _list(tiers.get(tier_key)):
            if isinstance(ind, dict):
                yield tier_key, ind
    if include_anti:
        for ind in anti_indicators_for_topic(topic):
            yield "anti_indicators", ind


def normalize_anti_indicators_location(topic: dict) -> int:
    """Move legacy nested anti-indicators to canonical top-level in-place.

    Returns the number of legacy anti-indicator objects removed from tiers.
    """
    inds = topic.setdefault("indicators", {})
    tiers = inds.setdefault("tiers", {})
    legacy = _list(tiers.pop("anti_indicators", []))
    if legacy or "anti_indicators" not in inds:
        top = _list(inds.get("anti_indicators"))
        merged = _dedup_indicators(top + legacy)
        rebuilt = {}
        inserted = False
        for key, value in inds.items():
            rebuilt[key] = value
            if key == "tiers":
                rebuilt["anti_indicators"] = merged
                inserted = True
        if not inserted:
            rebuilt["anti_indicators"] = merged
        inds.clear()
        inds.update(rebuilt)
    return len(legacy)


def posterior_effect_direction(effect: str, hypothesis_key: str) -> int:
    """Return +1, -1, or 0 for an effect's signed impact on one hypothesis."""
    hk = re.escape(str(hypothesis_key))
    text = str(effect or "")
    direct = re.search(rf"\b{hk}\b\s*([+-])\s*\d+(?:\.\d+)?\s*(?:pp|%)?", text, re.IGNORECASE)
    if direct:
        return 1 if direct.group(1) == "+" else -1

    for part in re.split(r"[;\n.]", text):
        if not re.search(rf"\b{hk}\b", part, re.IGNORECASE):
            continue
        lower = part.lower()
        if re.search(r"\b(collapse|decrease|down|drop|reduce|reduced|decline|weaken|lower|less likely|against|negative)\b", lower):
            return -1
        if re.search(r"\b(surge|increase|increased|up|gain|rise|confirm|strengthen|higher|more likely|positive)\b", lower):
            return 1
    return 0


def build_effect_coverage_matrix(hypotheses: dict, indicators: list, anti_indicators: list) -> dict:
    """Build signed per-hypothesis coverage from posteriorEffect text."""
    h_keys = list(hypotheses.keys())
    matrix = {hk: {"pos": [], "neg": []} for hk in h_keys}
    indicator_reach = {}

    for ind in (indicators or []) + (anti_indicators or []):
        if not isinstance(ind, dict):
            continue
        ind_id = ind.get("id", "?")
        effect = ind.get("posteriorEffect", "")
        reached = []
        for hk in h_keys:
            if re.search(rf"\b{re.escape(str(hk))}\b", str(effect), re.IGNORECASE):
                reached.append(hk)
            direction = posterior_effect_direction(effect, hk)
            if direction > 0:
                matrix[hk]["pos"].append(ind_id)
            elif direction < 0:
                matrix[hk]["neg"].append(ind_id)
        indicator_reach[ind_id] = reached

    return {
        "matrix": {hk: {"pos": len(v["pos"]), "neg": len(v["neg"])} for hk, v in matrix.items()},
        "positive": {hk: matrix[hk]["pos"] for hk in h_keys},
        "negative": {hk: matrix[hk]["neg"] for hk in h_keys},
        "indicator_reach": indicator_reach,
    }
