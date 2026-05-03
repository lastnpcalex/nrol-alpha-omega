"""
topic_search — search the project's accumulated knowledge.

Provides helpers to search across:
  - any topic's evidenceLog (or all topics' evidenceLogs)
  - canvas/evidence-cold.json (cold storage)
  - sources/source-trust.json (source calibration)

Used by the red/blue team agents during indicator-cleanup sweeps to
research counterevidence and base rates without needing external web
search. The project's own evaluated evidence is higher signal than
generic web results for project-specific questions.

Public functions:
  search_evidence(query, topic_slug=None, limit=20) -> list of matched entries
  search_cold_storage(query, limit=20) -> list of matched cold entries
  search_sources(query, limit=10) -> list of matched source records
  search_all(query, limit=30) -> aggregated results across all three

The matching uses sentence-transformers cosine similarity if available;
falls back to keyword overlap otherwise. Both are deterministic.
"""

import json
import re
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent
TOPICS_DIR = REPO_ROOT / "topics"
CANVAS_COLD = REPO_ROOT.parent / "canvas" / "evidence-cold.json"
SOURCES_FILE = REPO_ROOT / "sources" / "source-trust.json"

# Lazy-loaded embedding model
_EMBED_MODEL = None
_EMBED_AVAILABLE = None


def _get_embed_model():
    """Lazy-load sentence-transformers model. Returns None if unavailable."""
    global _EMBED_MODEL, _EMBED_AVAILABLE
    if _EMBED_AVAILABLE is False:
        return None
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _EMBED_AVAILABLE = True
        return _EMBED_MODEL
    except Exception:
        _EMBED_AVAILABLE = False
        return None


def _keyword_score(query: str, text: str) -> float:
    """Fallback similarity: shared significant tokens / total query tokens."""
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "into", "than",
        "then", "when", "also", "does", "more", "past", "about", "have", "been",
    }
    q_tokens = set(re.findall(r"\b[a-z]{4,}\b", (query or "").lower())) - stop
    t_tokens = set(re.findall(r"\b[a-z]{4,}\b", (text or "").lower())) - stop
    if not q_tokens:
        return 0.0
    return len(q_tokens & t_tokens) / len(q_tokens)


def _embed_score(query: str, texts: list) -> list:
    """Embed query and texts; return cosine similarities."""
    model = _get_embed_model()
    if model is None:
        return None
    try:
        import numpy as np
        embs = model.encode([query] + list(texts), convert_to_numpy=True, show_progress_bar=False)
        q_emb = embs[0]
        text_embs = embs[1:]
        # Cosine similarity
        q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)
        sims = []
        for emb in text_embs:
            t_norm = emb / (np.linalg.norm(emb) + 1e-9)
            sims.append(float(q_norm @ t_norm))
        return sims
    except Exception:
        return None


def _rank(query: str, items: list, text_extractor) -> list:
    """Rank items by similarity to query. Returns list with 'score' added."""
    if not items:
        return []
    texts = [text_extractor(item) for item in items]
    scores = _embed_score(query, texts)
    if scores is None:
        # Fallback to keyword
        scored = [(_keyword_score(query, t), item) for t, item in zip(texts, items)]
    else:
        scored = list(zip(scores, items))
    scored.sort(key=lambda x: -x[0])
    return [{**item, "_score": float(score)} for score, item in scored]


# --- Public API ---


def search_evidence(query: str, topic_slug: Optional[str] = None, limit: int = 20) -> list:
    """
    Search evidenceLog of one or all topics by semantic similarity to query.

    Args:
        query: natural-language search query
        topic_slug: restrict to one topic; if None, searches all topics
        limit: max results

    Returns: list of {topic_slug, evidence_id, time, source, text, score}
    """
    items = []
    if topic_slug:
        topics_to_search = [topic_slug]
    else:
        topics_to_search = [
            p.stem for p in TOPICS_DIR.glob("*.json")
            if not p.stem.startswith("_") and p.stem != "manifest"
        ]
    for slug in topics_to_search:
        path = TOPICS_DIR / f"{slug}.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                topic = json.load(f)
        except Exception:
            continue
        ev_log = topic.get("evidenceLog", []) or []
        for entry in ev_log:
            text = entry.get("text") or entry.get("note") or ""
            if not text:
                continue
            items.append({
                "topic_slug": slug,
                "evidence_id": entry.get("id"),
                "time": entry.get("time"),
                "source": entry.get("source"),
                "text": text[:300],
                "_full_text": text,
            })
    ranked = _rank(query, items, lambda x: x["_full_text"])
    # Strip _full_text from output
    out = [{k: v for k, v in item.items() if not k.startswith("_") or k == "_score"}
           for item in ranked[:limit]]
    return out


def search_cold_storage(query: str, limit: int = 20) -> list:
    """Search canvas/evidence-cold.json for matching IGNORE'd entries."""
    if not CANVAS_COLD.exists():
        return []
    try:
        with open(CANVAS_COLD, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    entries = data.get("entries", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    items = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        # Cold storage entries vary in shape — extract any text fields we can
        text_parts = []
        for k in ("text", "summary", "headline", "note"):
            v = e.get(k)
            if isinstance(v, str):
                text_parts.append(v)
        for k in ("claims", "keywords", "actors"):
            v = e.get(k)
            if isinstance(v, list):
                text_parts.extend(str(x) for x in v if x)
        text = " | ".join(text_parts)
        if not text:
            continue
        items.append({
            "source": e.get("source"),
            "time": e.get("time") or e.get("timestamp"),
            "text": text[:300],
            "_full_text": text,
        })
    ranked = _rank(query, items, lambda x: x["_full_text"])
    return [{k: v for k, v in item.items() if not k.startswith("_") or k == "_score"}
            for item in ranked[:limit]]


def search_sources(query: str, limit: int = 10) -> list:
    """Search source-trust.json for sources matching the query."""
    if not SOURCES_FILE.exists():
        return []
    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    if not isinstance(sources, dict):
        return []
    items = []
    for src_name, src_data in sources.items():
        if not isinstance(src_data, dict):
            continue
        text_parts = [src_name]
        for k in ("description", "domain", "category"):
            v = src_data.get(k)
            if isinstance(v, str):
                text_parts.append(v)
        text = " | ".join(text_parts)
        items.append({
            "source": src_name,
            "trust": src_data.get("trust") or src_data.get("baseline_trust"),
            "domain": src_data.get("domain"),
            "text": text[:200],
            "_full_text": text,
        })
    ranked = _rank(query, items, lambda x: x["_full_text"])
    return [{k: v for k, v in item.items() if not k.startswith("_") or k == "_score"}
            for item in ranked[:limit]]


def search_all(query: str, limit: int = 30) -> dict:
    """Aggregate search across evidence, cold storage, and sources."""
    return {
        "evidence": search_evidence(query, limit=limit),
        "cold_storage": search_cold_storage(query, limit=min(limit, 10)),
        "sources": search_sources(query, limit=min(limit, 10)),
    }
