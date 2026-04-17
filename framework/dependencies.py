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


def validate_conditionals(
    conditionals: dict,
    upstream_h_keys: list[str],
    downstream_h_keys: list[str],
) -> dict:
    """
    Validate a conditional probability table (CPT).

    Each row key must be an upstream hypothesis label.
    Each row value must be a dict mapping downstream hypothesis labels to probabilities.
    Each row must sum to 1.00 (±0.005).
    No probability may be exactly 0.0 (epistemic humility — use 0.01 minimum).
    The matrix must not be uniform (every row identical = no information).

    Returns {"valid": bool, "errors": [...], "warnings": [...]}
    """
    errors = []
    warnings = []

    if not conditionals:
        return {"valid": False, "errors": ["Empty conditionals matrix"], "warnings": []}

    # Check row keys match upstream hypotheses
    row_keys = set(conditionals.keys())
    expected_rows = set(upstream_h_keys)
    missing_rows = expected_rows - row_keys
    extra_rows = row_keys - expected_rows
    if missing_rows:
        errors.append(f"Missing upstream hypothesis rows: {sorted(missing_rows)}")
    if extra_rows:
        # Allow rows that aren't hypothesis keys (e.g. 'narrative', 'derivation_method')
        pass

    # Check each row
    rows_for_uniformity = []
    for row_key in upstream_h_keys:
        row = conditionals.get(row_key)
        if row is None:
            continue
        if not isinstance(row, dict):
            errors.append(f"Row {row_key} is not a dict")
            continue

        # Extract probability values (skip non-numeric fields like 'narrative')
        prob_keys = {k for k, v in row.items() if isinstance(v, (int, float))}
        expected_cols = set(downstream_h_keys)
        missing_cols = expected_cols - prob_keys
        if missing_cols:
            errors.append(f"Row {row_key} missing downstream hypotheses: {sorted(missing_cols)}")

        # Sum check
        probs = [row[k] for k in downstream_h_keys if k in row and isinstance(row[k], (int, float))]
        total = sum(probs)
        if abs(total - 1.0) > 0.005:
            errors.append(f"Row {row_key} sums to {total:.4f}, not 1.00")

        # No zeros
        for k in downstream_h_keys:
            v = row.get(k)
            if isinstance(v, (int, float)) and v == 0.0:
                errors.append(f"Row {row_key}, col {k} is exactly 0.0 — use 0.01 minimum")
            if isinstance(v, (int, float)) and v < 0:
                errors.append(f"Row {row_key}, col {k} is negative: {v}")

        rows_for_uniformity.append(tuple(row.get(k, 0) for k in downstream_h_keys))

    # Uniformity check
    if len(rows_for_uniformity) > 1:
        if all(r == rows_for_uniformity[0] for r in rows_for_uniformity):
            warnings.append("All rows are identical — CPT contains no conditional information")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def compute_implied_posteriors(
    downstream_topic: dict,
    upstream_slug: str,
    topic_loader=None,
) -> dict:
    """
    Compute implied downstream posteriors by marginalizing over upstream hypotheses.

    P(downstream_H_j) = Σ_i P(downstream_H_j | upstream_H_i) × P(upstream_H_i)

    Returns ADVISORY output only — never auto-applies.

    Returns {
        "implied_posteriors": {H1: float, ...},
        "actual_posteriors": {H1: float, ...},
        "max_drift": float,
        "upstream_posteriors": {H1: float, ...},
        "independence_warning": bool,  # True if topic has multiple upstream deps
        "upstream_slug": str,
    }
    """
    if topic_loader is None:
        topic_loader = _default_loader

    deps = get_dependencies(downstream_topic)
    upstream_deps = deps.get("upstream", [])

    # Find the dependency for this upstream
    dep = None
    for d in upstream_deps:
        if d.get("slug") == upstream_slug:
            dep = d
            break

    if dep is None:
        raise ValueError(f"No upstream dependency on {upstream_slug}")

    conditionals = dep.get("conditionals")
    if not conditionals:
        raise ValueError(f"No conditionals defined for upstream {upstream_slug}")

    # Load upstream posteriors
    upstream = topic_loader(upstream_slug)
    upstream_h = upstream.get("model", {}).get("hypotheses", {})
    upstream_posteriors = {k: v["posterior"] for k, v in upstream_h.items()}

    # Downstream hypothesis keys
    downstream_h = downstream_topic.get("model", {}).get("hypotheses", {})
    downstream_keys = list(downstream_h.keys())

    # Marginalize: P(D_j) = sum_i P(D_j | U_i) * P(U_i)
    implied = {k: 0.0 for k in downstream_keys}
    for u_key, u_prob in upstream_posteriors.items():
        row = conditionals.get(u_key, {})
        for d_key in downstream_keys:
            cond_prob = row.get(d_key, 0.0)
            if isinstance(cond_prob, (int, float)):
                implied[d_key] += cond_prob * u_prob

    # Normalize (should already sum to ~1.0 if CPT is valid)
    total = sum(implied.values())
    if total > 0:
        implied = {k: round(v / total, 4) for k, v in implied.items()}

    actual = {k: v["posterior"] for k, v in downstream_h.items()}
    max_drift = max(abs(implied[k] - actual[k]) for k in downstream_keys)

    # Independence warning if multiple upstream deps
    independence_warning = len(upstream_deps) > 1

    return {
        "implied_posteriors": implied,
        "actual_posteriors": actual,
        "max_drift": round(max_drift, 4),
        "upstream_posteriors": upstream_posteriors,
        "upstream_slug": upstream_slug,
        "independence_warning": independence_warning,
        "independence_note": (
            f"This topic has {len(upstream_deps)} upstream dependencies. "
            "Implied posteriors assume independence between upstream topics. "
            "If upstream topics are correlated, treat these numbers with caution."
        ) if independence_warning else None,
    }


def check_cpt_staleness(dep: dict, upstream_topic: dict, downstream_topic: dict) -> dict:
    """
    Check whether a CPT's derivation context has changed since it was created.

    Compares stored cptHash against current upstream/downstream schema.
    Returns {"stale": bool, "reasons": [...]}
    """
    cpt_hash = dep.get("cptHash")
    if not cpt_hash:
        return {"stale": False, "reasons": ["No cptHash stored — cannot check staleness"]}

    reasons = []

    # Check upstream hypothesis keys
    stored_h = set(cpt_hash.get("upstreamHypotheses", []))
    actual_h = set(upstream_topic.get("model", {}).get("hypotheses", {}).keys())
    if stored_h != actual_h:
        reasons.append(f"Upstream hypotheses changed: was {sorted(stored_h)}, now {sorted(actual_h)}")

    # Check downstream indicator count
    stored_count = cpt_hash.get("downstreamIndicatorCount")
    if stored_count is not None:
        actual_count = 0
        tiers = downstream_topic.get("indicators", {}).get("tiers", {})
        for tier_inds in tiers.values():
            if isinstance(tier_inds, list):
                actual_count += len(tier_inds)
        if actual_count != stored_count:
            reasons.append(f"Downstream indicator count changed: was {stored_count}, now {actual_count}")

    return {"stale": len(reasons) > 0, "reasons": reasons}


def _default_loader(slug: str) -> dict:
    """Load a topic from disk by slug."""
    path = TOPICS_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"Topic not found: {slug}")
    return json.loads(path.read_text(encoding="utf-8"))
