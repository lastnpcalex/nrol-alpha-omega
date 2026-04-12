"""
NRL-Alpha Omega — Cross-Topic Dependency Graph

Manages conditional relationships between topics so that a posterior
shift on topic A can flag downstream topics whose assumptions are now
stale.  Draws on Bayesian forecast reconciliation (Hyndman et al.) for
the coherence concept, but implements an alert-based system rather than
automatic propagation — the operator decides whether to act.

Design principle: dependencies are DECLARED at design time (like
indicators), checked MECHANICALLY at save time (like the governor),
and surfaced as GOVERNANCE ISSUES (not auto-updates).

Key concepts:
  - upstream: topics whose posteriors this topic's assumptions depend on
  - assumptions: what this topic assumes about upstream posteriors
  - staleness: when upstream posteriors drift beyond assumed values
  - reconciliation alert: governance issue flagging stale assumptions
"""

import json
from pathlib import Path
from typing import Optional

TOPICS_DIR = Path(__file__).parent.parent / "topics"


def get_dependencies(topic: dict) -> dict:
    """
    Get this topic's dependency declarations.

    Returns {
        "upstream": [
            {
                "slug": "us-recession-2026",
                "assumptions": {"H1": 0.20, "H3": 0.35},
                "tolerance": 0.08,
            },
            ...
        ],
        "downstream": []  # populated by scan_downstream()
    }
    """
    return topic.get("dependencies", {"upstream": [], "downstream": []})


def check_stale_dependencies(topic: dict, topic_loader=None) -> list[dict]:
    """
    Check whether upstream topic posteriors have drifted beyond this
    topic's declared assumptions.

    Parameters
    ----------
    topic : dict
        The topic to check.
    topic_loader : callable, optional
        Function(slug) -> dict that loads a topic by slug.
        Defaults to loading from TOPICS_DIR.

    Returns
    -------
    list of dicts, each:
        {
            "upstream_slug": str,
            "hypothesis": str,
            "assumed": float,
            "actual": float,
            "drift": float,       # absolute difference
            "tolerance": float,
            "stale": bool,
        }
    """
    if topic_loader is None:
        topic_loader = _default_loader

    deps = get_dependencies(topic)
    results = []

    for dep in deps.get("upstream", []):
        slug = dep["slug"]
        assumptions = dep.get("assumptions", {})
        tolerance = dep.get("tolerance", 0.08)

        try:
            upstream = topic_loader(slug)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            results.append({
                "upstream_slug": slug,
                "hypothesis": "*",
                "assumed": None,
                "actual": None,
                "drift": None,
                "tolerance": tolerance,
                "stale": True,
                "error": f"Could not load upstream topic: {slug}",
            })
            continue

        upstream_hypotheses = upstream.get("model", {}).get("hypotheses", {})

        for h_key, assumed_value in assumptions.items():
            actual = upstream_hypotheses.get(h_key, {}).get("posterior")
            if actual is None:
                results.append({
                    "upstream_slug": slug,
                    "hypothesis": h_key,
                    "assumed": assumed_value,
                    "actual": None,
                    "drift": None,
                    "tolerance": tolerance,
                    "stale": True,
                    "error": f"Hypothesis {h_key} not found in {slug}",
                })
                continue

            drift = abs(actual - assumed_value)
            results.append({
                "upstream_slug": slug,
                "hypothesis": h_key,
                "assumed": round(assumed_value, 4),
                "actual": round(actual, 4),
                "drift": round(drift, 4),
                "tolerance": tolerance,
                "stale": drift > tolerance,
            })

    return results


def scan_downstream(slug: str, topic_loader=None) -> list[dict]:
    """
    Find all topics that declare this slug as an upstream dependency.

    Returns list of {"slug": str, "assumptions": dict, "tolerance": float}
    """
    if topic_loader is None:
        topic_loader = _default_loader

    downstream = []
    if not TOPICS_DIR.exists():
        return downstream

    for path in TOPICS_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        deps = t.get("dependencies", {})
        for dep in deps.get("upstream", []):
            if dep.get("slug") == slug:
                downstream.append({
                    "slug": t.get("meta", {}).get("slug", path.stem),
                    "title": t.get("meta", {}).get("title", path.stem),
                    "assumptions": dep.get("assumptions", {}),
                    "tolerance": dep.get("tolerance", 0.08),
                })

    return downstream


def propagate_alert(source_topic: dict, topic_loader=None) -> list[dict]:
    """
    After a posterior shift on source_topic, check all downstream topics
    for stale assumptions.

    Returns list of alerts:
        {
            "downstream_slug": str,
            "downstream_title": str,
            "stale_assumptions": [
                {"hypothesis": str, "assumed": float, "actual": float, "drift": float}
            ]
        }
    """
    if topic_loader is None:
        topic_loader = _default_loader

    source_slug = source_topic.get("meta", {}).get("slug", "")
    downstream_topics = scan_downstream(source_slug, topic_loader)
    alerts = []

    for dt in downstream_topics:
        try:
            downstream = topic_loader(dt["slug"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            continue

        stale = check_stale_dependencies(downstream, topic_loader)
        stale_from_source = [
            s for s in stale
            if s.get("upstream_slug") == source_slug and s.get("stale")
        ]

        if stale_from_source:
            alerts.append({
                "downstream_slug": dt["slug"],
                "downstream_title": dt.get("title", dt["slug"]),
                "stale_assumptions": [
                    {
                        "hypothesis": s["hypothesis"],
                        "assumed": s["assumed"],
                        "actual": s["actual"],
                        "drift": s["drift"],
                    }
                    for s in stale_from_source
                ],
            })

    return alerts


def build_dependency_graph(topic_loader=None) -> dict:
    """
    Build a full dependency graph across all topics.

    Returns {
        "nodes": [{"slug": str, "title": str, "status": str}],
        "edges": [{"from": str, "to": str, "assumptions": dict}],
        "stale_edges": [{"from": str, "to": str, "drift": dict}],
    }
    """
    if topic_loader is None:
        topic_loader = _default_loader

    nodes = []
    edges = []
    stale_edges = []
    topics = {}

    if not TOPICS_DIR.exists():
        return {"nodes": nodes, "edges": edges, "stale_edges": stale_edges}

    # Load all topics
    for path in TOPICS_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
            meta = t.get("meta", {})
            slug = meta.get("slug", path.stem)
            topics[slug] = t
            nodes.append({
                "slug": slug,
                "title": meta.get("title", slug),
                "status": meta.get("status", "UNKNOWN"),
                "classification": meta.get("classification", "ROUTINE"),
                "health": (t.get("governance") or {}).get("health"),
            })
        except (json.JSONDecodeError, OSError):
            continue

    # Build edges from dependency declarations
    for slug, t in topics.items():
        deps = t.get("dependencies", {})
        for dep in deps.get("upstream", []):
            upstream_slug = dep.get("slug", "")
            edges.append({
                "from": upstream_slug,
                "to": slug,
                "assumptions": dep.get("assumptions", {}),
                "tolerance": dep.get("tolerance", 0.08),
            })

            # Check staleness
            if upstream_slug in topics:
                upstream = topics[upstream_slug]
                upstream_h = upstream.get("model", {}).get("hypotheses", {})
                drift_map = {}
                is_stale = False
                for h_key, assumed in dep.get("assumptions", {}).items():
                    actual = upstream_h.get(h_key, {}).get("posterior")
                    if actual is not None:
                        d = abs(actual - assumed)
                        if d > dep.get("tolerance", 0.08):
                            is_stale = True
                            drift_map[h_key] = {
                                "assumed": assumed,
                                "actual": round(actual, 4),
                                "drift": round(d, 4),
                            }
                if is_stale:
                    stale_edges.append({
                        "from": upstream_slug,
                        "to": slug,
                        "drift": drift_map,
                    })

    return {"nodes": nodes, "edges": edges, "stale_edges": stale_edges}


def _default_loader(slug: str) -> dict:
    """Load a topic from disk by slug."""
    path = TOPICS_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"Topic not found: {slug}")
    return json.loads(path.read_text(encoding="utf-8"))
