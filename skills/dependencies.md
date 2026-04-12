# Skill: Cross-Topic Dependencies

Wire causal relationships between topics, detect stale assumptions, and
propagate drift alerts when upstream posteriors shift.

## When to use

- A topic's outcome depends on another topic's resolution
- An upstream topic's posteriors have changed and downstream assumptions may be stale
- You need to visualize the dependency graph
- After any posterior update, to check downstream impact

## Python calls

### Check stale dependencies for a topic

```python
from engine import load_topic
from framework.dependencies import check_stale_dependencies

topic = load_topic("calibration-midterms-2026")
stale = check_stale_dependencies(topic)
# Returns list of:
# {
#   "upstream_slug": str,
#   "hypothesis": str,
#   "assumed": float,
#   "actual": float,     # current upstream posterior
#   "drift": float,      # |actual - assumed|
#   "tolerance": float,
#   "stale": bool,       # drift > tolerance
# }
```

### Propagate alerts after an update

```python
from engine import load_topic
from framework.dependencies import propagate_alert

# After updating calibration-us-recession-2026 posteriors:
source_topic = load_topic("calibration-us-recession-2026")
alerts = propagate_alert(source_topic)
# Returns list of downstream topics with stale assumptions:
# {
#   "downstream_slug": str,
#   "downstream_title": str,
#   "stale_assumptions": [
#     {"hypothesis": "H1", "assumed": 0.55, "actual": 0.62, "drift": 0.07}
#   ]
# }
```

### Build full dependency graph

```python
from framework.dependencies import build_dependency_graph

graph = build_dependency_graph()
# Returns:
# {
#   "nodes": [{"slug": str, "title": str, "status": str}],
#   "edges": [{"from": str, "to": str, "assumptions": dict, "tolerance": float}],
#   "stale_edges": [{"from": str, "to": str, "drift": {"H1": {"assumed": .., "actual": .., "drift": ..}}}]
# }
```

## Dependency schema in topic JSON

```json
{
  "dependencies": {
    "_docs": "Cross-topic dependency declarations.",
    "upstream": [
      {
        "slug": "calibration-upstream-topic",
        "reason": "Why this topic depends on the upstream topic's outcome",
        "assumptions": {
          "H1": 0.55,
          "H2": 0.22,
          "H3": 0.14,
          "H4": 0.09
        },
        "tolerance": 0.10
      }
    ]
  }
}
```

### Field definitions

- **slug**: The upstream topic this depends on
- **reason**: Causal justification for the dependency (not just correlation)
- **assumptions**: What this topic assumes the upstream posteriors are. When
  the upstream's actual posteriors drift beyond tolerance, the edge is stale.
- **tolerance**: Maximum acceptable drift (in probability units, e.g., 0.10 = 10pp).
  Default is 0.08 in the Python code.

## Wiring guidelines

1. **Causal, not correlational** — wire dependencies for actual causal mechanisms,
   not just "these topics are related." Example: tariffs → recession (tariffs raise
   costs, reducing demand) is causal. tariffs → midterms is indirect (goes through
   recession).

2. **Set assumptions to current upstream posteriors** — when first wiring, copy
   the upstream's current posteriors as your assumptions. This establishes the
   baseline.

3. **Tolerance should match update sensitivity** — smaller tolerance (0.05) for
   topics that are highly sensitive to upstream shifts; larger (0.15) for loose
   coupling.

4. **Downstream topics don't auto-update** — stale edges generate alerts, not
   automatic posterior shifts. The operator decides whether and how to propagate.

5. **Check after every update** — run `propagate_alert()` after any posterior
   change to identify newly stale downstream edges.

## Mirror dashboard equivalent

The Loom mirror computes stale edges in `loadAll()` by comparing each
dependency's `assumptions` to the upstream topic's actual posteriors. Stale
edges are highlighted in the Dependency Graph panel with per-hypothesis
drift details. The overview panel shows `staleDependencies` count per topic
and escalates health to DEGRADED if any dependencies are stale.
