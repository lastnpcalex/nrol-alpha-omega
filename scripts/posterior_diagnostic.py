#!/usr/bin/env python3
"""
posterior_diagnostic.py — instruments to understand what drove a posterior
trajectory. NOT a fix. NOT a proposal. Just code that prints numbers.

Three views:
  1. Per-entry decomposition: each posteriorHistory entry's actual delta
     on each hypothesis, plus a leave-one-out counterfactual.
  2. Indicator analysis: LR ratios per fired indicator, compound effect,
     pairwise temporal proximity (potential correlated fires).
  3. De-correlated baseline: replay the same evidence with attenuated LRs
     and no clamp, to see how much of the saturation is from compounding
     correlated evidence + clamp redistribution vs from real Bayesian update.

Usage:
    python scripts/posterior_diagnostic.py <slug> --view per-entry
    python scripts/posterior_diagnostic.py <slug> --view indicators
    python scripts/posterior_diagnostic.py <slug> --view counterfactual
    python scripts/posterior_diagnostic.py <slug> --view all
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import load_topic


def _apply_bayes_no_clamp(prior, likelihoods, h_keys):
    """Bayesian update without clamp/floor."""
    unnorm = {k: prior[k] * likelihoods.get(k, 1.0) for k in h_keys}
    total = sum(unnorm.values())
    if total <= 0:
        return dict(prior)
    return {k: v / total for k, v in unnorm.items()}


def _apply_clamp(post, h_keys, floor=0.005, ceiling=0.98):
    """Clamp + redistribute."""
    pinned = {}
    free = {}
    for k in h_keys:
        v = post[k]
        if v < floor:
            pinned[k] = floor
        elif v > ceiling:
            pinned[k] = ceiling
        else:
            free[k] = v
    if not pinned:
        return post
    pinned_total = sum(pinned.values())
    free_target = max(0.0, 1.0 - pinned_total)
    free_sum = sum(free.values())
    out = {}
    for k in h_keys:
        if k in pinned:
            out[k] = pinned[k]
        elif free_sum > 0:
            out[k] = free[k] / free_sum * free_target
        else:
            out[k] = free_target / max(1, len(free)) if k in free else pinned.get(k, post[k])
    total = sum(out.values())
    return {k: v / total for k, v in out.items()}


def _apply_deadline_elim(prior, eliminated, h_keys, floor=0.005):
    freed = sum(max(0, prior[k] - floor) for k in eliminated if k in prior)
    survivors = [k for k in h_keys if k not in eliminated]
    survivor_total = sum(prior[k] for k in survivors)
    new = {}
    for k in h_keys:
        if k in eliminated:
            new[k] = floor
        else:
            if survivor_total > 0:
                share = prior[k] / survivor_total
                new[k] = prior[k] + freed * share
            else:
                new[k] = prior[k]
    total = sum(new.values())
    return {k: v / total for k, v in new.items()}


def _walk(history, h_keys, lr_attenuation=1.0, skip_clamp=False, skip_indices=None):
    """
    Walk posteriorHistory; return final posteriors + per-step trace.

    lr_attenuation: scale LRs toward 1.0 by this factor.
        1.0 = original LRs (no change)
        0.5 = halfway to no-op (de-correlated by half)
        0.0 = LRs become 1.0 (no update)
    """
    skip_indices = skip_indices or set()
    if not history:
        return None, []
    first = history[0]
    # Schema split: nested posteriors OR top-level H1/H2/... fields
    current = dict(first.get("posteriors") or {})
    if not current or not all(k in current for k in h_keys):
        current = {k: first.get(k) for k in h_keys if first.get(k) is not None}
    if not current or not all(k in current for k in h_keys):
        return None, []
    trace = [{"i": 0, "method": "prior", "posteriors": dict(current),
              "delta": {k: 0 for k in h_keys}}]
    for i, entry in enumerate(history[1:], start=1):
        if i in skip_indices:
            continue
        method = entry.get("updateMethod", "")
        prior = dict(current)
        likelihoods = entry.get("likelihoods")
        if method.startswith("deadline_elimination"):
            eliminated = entry.get("eliminatedHypotheses", [])
            if not eliminated:
                continue
            current = _apply_deadline_elim(current, eliminated, h_keys)
        elif likelihoods:
            # Apply attenuation: LR' = 1 + (LR - 1) * attenuation
            attenuated = {k: 1.0 + (likelihoods.get(k, 1.0) - 1.0) * lr_attenuation for k in h_keys}
            current = _apply_bayes_no_clamp(current, attenuated, h_keys)
            if not skip_clamp:
                if any(v < 0.005 or v > 0.98 for v in current.values()):
                    current = _apply_clamp(current, h_keys)
        else:
            continue
        trace.append({
            "i": i,
            "method": method,
            "lens": (entry.get("lrSource") or {}).get("lens", "-"),
            "posteriors": dict(current),
            "delta": {k: round(current[k] - prior[k], 4) for k in h_keys},
            "note": (entry.get("note") or "")[:80],
        })
    return current, trace


def view_per_entry(topic):
    """View 1: per-entry decomposition with leave-one-out counterfactuals.
    Computes leave-one-out under both clamped and no-clamp replay so we can
    see what the clamp is hiding."""
    h_keys = list(topic["model"]["hypotheses"].keys())
    history = topic["model"]["posteriorHistory"]

    # Baseline replay (clamped) and no-clamp baseline
    final_baseline, trace = _walk(history, h_keys)
    final_noclamp, _ = _walk(history, h_keys, skip_clamp=True)

    print("=" * 100)
    print("VIEW 1: per-entry decomposition + leave-one-out (clamp on AND clamp off)")
    print("=" * 100)
    print(f"baseline final (clamp on): " + " ".join(f"{k}={final_baseline[k]:.4f}" for k in h_keys))
    print(f"baseline final (no clamp): " + " ".join(f"{k}={final_noclamp[k]:.4f}" for k in h_keys))
    print()
    print(f"{'idx':>3} {'method':<25} {'lens':<10} " +
          " ".join(f"{'d_'+k:>8}" for k in h_keys) +
          f"  {'lo_clamp':>9} {'lo_noclamp':>11} note")
    print("-" * 145)

    sorted_trace = sorted(trace[1:], key=lambda s: -abs(s["delta"].get("H3", 0)))
    for s in sorted_trace[:25]:
        loo_clamp, _ = _walk(history, h_keys, skip_indices={s["i"]})
        loo_noclamp, _ = _walk(history, h_keys, skip_indices={s["i"]}, skip_clamp=True)
        loo_d_clamp = (loo_clamp["H3"] - final_baseline["H3"]) if loo_clamp else 0
        loo_d_noclamp = (loo_noclamp["H3"] - final_noclamp["H3"]) if loo_noclamp else 0
        deltas_s = " ".join(f"{s['delta'][k]:>+8.4f}" for k in h_keys)
        print(f"  {s['i']:>3} {s['method'][:25]:<25} {s['lens'][:10]:<10} {deltas_s}  {loo_d_clamp:>+9.4f} {loo_d_noclamp:>+11.4f} {s['note'][:50]}")
    print()
    print("Reading:")
    print("  'd_X' = immediate delta on hypothesis X from this entry's update.")
    print("  'lo_clamp'   = change in final H3 if this entry alone were removed (with clamp).")
    print("  'lo_noclamp' = same, but with clamp DISABLED across the whole replay.")
    print("  When lo_clamp ~= 0 but lo_noclamp != 0, the clamp is HIDING the contribution")
    print("  (saturation absorbs individual entry effects).")


def view_indicators(topic):
    """View 2: per-indicator LR ratios + compound effect + temporal correlation."""
    h_keys = list(topic["model"]["hypotheses"].keys())
    inds = []
    for tier in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in topic["indicators"]["tiers"].get(tier, []):
            if ind.get("status") == "FIRED" and ind.get("likelihoods"):
                inds.append((tier, ind))
    for ind in topic["indicators"].get("anti_indicators", []):
        if ind.get("status") == "FIRED" and ind.get("likelihoods"):
            inds.append(("anti", ind))

    print("=" * 100)
    print("VIEW 2: indicator analysis")
    print("=" * 100)
    print(f"{'tier':<18} {'id':<35} {'fired_date':<12} " +
          " ".join(f"{'L_'+k:<7}" for k in h_keys) + " max_ratio")
    print("-" * 130)

    compound = {k: 1.0 for k in h_keys}
    fired_dates = []
    for tier, ind in inds:
        L = ind["likelihoods"]
        ratios_str = " ".join(f"{L.get(k,1.0):<7.3f}" for k in h_keys)
        L_min = min(L.get(k, 1.0) for k in h_keys)
        L_max = max(L.get(k, 1.0) for k in h_keys)
        max_ratio = L_max / L_min if L_min > 0 else float('inf')
        fired = (ind.get("firedDate") or "?")[:10]
        fired_dates.append((fired, ind["id"]))
        print(f"  {tier[:18]:<18} {ind['id'][:35]:<35} {fired:<12} {ratios_str} {max_ratio:>5.2f}x")
        for k in h_keys:
            compound[k] *= L.get(k, 1.0)

    print()
    print("Compound LR effect (product across all fired indicators):")
    base = compound.get("H1", 1.0)
    if base > 0:
        for k in h_keys:
            ratio = compound[k] / base
            print(f"  {k}: {compound[k]:.4f} (ratio vs H1: {ratio:.2f}x)")

    # Pairwise temporal proximity
    print("\nIndicator firings ordered by date (potential correlated fires within 7 days):")
    fired_dates = sorted(fired_dates)
    for i, (date, ind_id) in enumerate(fired_dates):
        nearby = []
        if date != "?":
            try:
                d = datetime.fromisoformat(date)
                for j, (d2_str, id2) in enumerate(fired_dates):
                    if i == j or d2_str == "?":
                        continue
                    try:
                        d2 = datetime.fromisoformat(d2_str)
                        gap_days = abs((d2 - d).days)
                        if gap_days <= 7:
                            nearby.append(f"{id2}({gap_days}d)")
                    except Exception:
                        pass
            except Exception:
                pass
        nearby_s = ", ".join(nearby) if nearby else "(no fires within 7 days)"
        print(f"  {date}  {ind_id[:30]:<30}  -> near: {nearby_s}")


def _cluster_indicator_fires(topic, window_days=5):
    """
    Group fired indicators by temporal proximity. Returns list of clusters,
    each cluster a list of (tier, indicator) tuples that fired within
    window_days of each other.
    """
    fires = []
    for tier in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        for ind in topic["indicators"]["tiers"].get(tier, []):
            if ind.get("status") == "FIRED" and ind.get("likelihoods") and ind.get("firedDate"):
                fires.append((tier, ind))
    for ind in topic["indicators"].get("anti_indicators", []):
        if ind.get("status") == "FIRED" and ind.get("likelihoods") and ind.get("firedDate"):
            fires.append(("anti", ind))

    fires.sort(key=lambda x: x[1]["firedDate"][:10])

    clusters = []
    for tier, ind in fires:
        d = datetime.fromisoformat(ind["firedDate"][:10])
        placed = False
        for cluster in clusters:
            cluster_dates = [datetime.fromisoformat(c[1]["firedDate"][:10]) for c in cluster]
            if any(abs((d - cd).days) <= window_days for cd in cluster_dates):
                cluster.append((tier, ind))
                placed = True
                break
        if not placed:
            clusters.append([(tier, ind)])
    return clusters


def view_decorrelated(topic):
    """
    View 4: replay with causal de-correlation. Cluster indicator fires by
    temporal proximity (same event window); each cluster contributes ONE
    update whose LRs are the geometric mean of the cluster's individual LRs.
    Compare to baseline.
    """
    import math
    h_keys = list(topic["model"]["hypotheses"].keys())
    history = topic["model"]["posteriorHistory"]
    clusters = _cluster_indicator_fires(topic, window_days=5)

    print("=" * 100)
    print("VIEW 4: de-correlated replay (geometric mean LRs per event-cluster)")
    print("=" * 100)
    print(f"Window: 5 days. {sum(len(c) for c in clusters)} fires -> {len(clusters)} clusters\n")
    for i, cluster in enumerate(clusters):
        ids = [ind["id"] for _, ind in cluster]
        date = cluster[0][1]["firedDate"][:10]
        print(f"  cluster {i+1}: {date}  ({len(cluster)} fires)  {', '.join(ids)}")

        # Compute geometric mean LRs for the cluster
        gm_LRs = {}
        for k in h_keys:
            log_sum = sum(math.log(max(0.0001, ind["likelihoods"].get(k, 1.0))) for _, ind in cluster)
            gm_LRs[k] = math.exp(log_sum / len(cluster))
        gm_str = " ".join(f"{k}={gm_LRs[k]:.3f}" for k in h_keys)
        print(f"     geometric-mean LRs: {gm_str}")
    print()

    # Build a synthetic posteriorHistory: prior + one entry per cluster + non-indicator entries
    # (deadline_elim and freeform are kept as-is, but indicator-fire entries are replaced)
    if not history:
        print("  (no history)")
        return

    first = history[0]
    current = dict(first.get("posteriors") or {})
    if not current or not all(k in current for k in h_keys):
        print("  (cannot replay: no posteriors at history[0])")
        return
    if "Prior" in first.get("note", ""):
        print(f"prior: " + " ".join(f"{k}={current[k]:.4f}" for k in h_keys))
    else:
        print(f"start (history[0], not labeled 'Prior' — using as starting state):")
        print(f"  " + " ".join(f"{k}={current[k]:.4f}" for k in h_keys))

    # Map: indicator_id → cluster_index
    ind_to_cluster = {}
    for i, cluster in enumerate(clusters):
        for _, ind in cluster:
            ind_to_cluster[ind["id"]] = i
    cluster_applied = set()

    # Two parallel walks:
    #   LENIENT: de-correlate clusters + apply ALL non-cluster entries
    #            (freeform, OCHRE, removed-indicator) at full strength
    #   STRICT:  de-correlate clusters + new-gate behavior (skip freeform/OCHRE
    #            because the new gate would reject ungated updates)
    state_strict = dict(current)
    state_lenient = dict(current)
    cluster_applied_strict = set()
    cluster_applied_lenient = set()

    def is_indicator_referenced_in_topic(entry):
        ind_id = entry.get("indicatorId")
        if ind_id and ind_id in ind_to_cluster:
            return ind_id
        note_l = (entry.get("note") or "").lower()
        for cand in ind_to_cluster:
            if f"indicator {cand} fired" in note_l or f"indicator {cand} fires" in note_l:
                return cand
        return None

    for entry in history[1:]:
        method = entry.get("updateMethod", "")
        likelihoods = entry.get("likelihoods")

        if method.startswith("deadline_elimination"):
            eliminated = entry.get("eliminatedHypotheses", [])
            if eliminated:
                state_strict = _apply_deadline_elim(state_strict, eliminated, h_keys)
                state_lenient = _apply_deadline_elim(state_lenient, eliminated, h_keys)
            continue

        ind_id = is_indicator_referenced_in_topic(entry)

        if ind_id:
            ci = ind_to_cluster[ind_id]
            cluster = clusters[ci]
            gm_LRs = {}
            for k in h_keys:
                log_sum = sum(math.log(max(0.0001, ind["likelihoods"].get(k, 1.0))) for _, ind in cluster)
                gm_LRs[k] = math.exp(log_sum / len(cluster))
            if ci not in cluster_applied_strict:
                state_strict = _apply_bayes_no_clamp(state_strict, gm_LRs, h_keys)
                if any(v < 0.005 or v > 0.98 for v in state_strict.values()):
                    state_strict = _apply_clamp(state_strict, h_keys)
                cluster_applied_strict.add(ci)
            if ci not in cluster_applied_lenient:
                state_lenient = _apply_bayes_no_clamp(state_lenient, gm_LRs, h_keys)
                if any(v < 0.005 or v > 0.98 for v in state_lenient.values()):
                    state_lenient = _apply_clamp(state_lenient, h_keys)
                cluster_applied_lenient.add(ci)
            continue

        # Non-indicator entry. STRICT skips it (new gate would reject).
        # LENIENT applies it at full strength using stored likelihoods.
        if likelihoods:
            state_lenient = _apply_bayes_no_clamp(state_lenient, likelihoods, h_keys)
            if any(v < 0.005 or v > 0.98 for v in state_lenient.values()):
                state_lenient = _apply_clamp(state_lenient, h_keys)
            # state_strict: skip
    current = state_lenient  # for backward-compat with the print below

    full = {k: v["posterior"] for k, v in topic["model"]["hypotheses"].items()}

    print(f"\nde-corr LENIENT (apply all freeform): " + " ".join(f"{k}={state_lenient[k]:.4f}" for k in h_keys))
    print(f"de-corr STRICT (new-gate skip freeform): " + " ".join(f"{k}={state_strict[k]:.4f}" for k in h_keys))
    print(f"current full state (saved topic):       " + " ".join(f"{k}={full[k]:.4f}" for k in h_keys))
    print()
    print("Reading:")
    print("  LENIENT = de-correlation alone, freeform updates kept at full strength")
    print("  STRICT  = de-correlation + new gate would reject freeform/OCHRE/anonymous")
    print("  STRICT is the closest counterfactual for 'system as fixed by parts 1-4 + de-correlation'")


def view_counterfactual(topic):
    """View 3: alternative replays with attenuation + no-clamp to bound the saturation."""
    h_keys = list(topic["model"]["hypotheses"].keys())
    history = topic["model"]["posteriorHistory"]

    print("=" * 100)
    print("VIEW 3: counterfactual replays")
    print("=" * 100)

    scenarios = [
        ("baseline (LR=1.0, clamp ON)",  1.0, False),
        ("LR=0.75 (mild de-correlation)", 0.75, False),
        ("LR=0.50 (half de-correlation)", 0.5, False),
        ("LR=0.25 (heavy de-correlation)", 0.25, False),
        ("baseline NO clamp",             1.0, True),
        ("LR=0.5 NO clamp",               0.5, True),
        ("LR=0.25 NO clamp",              0.25, True),
    ]

    print(f"{'scenario':<35} " + " ".join(f"{k:<8}" for k in h_keys))
    print("-" * 90)
    for label, atten, skip_clamp in scenarios:
        final, _ = _walk(history, h_keys, lr_attenuation=atten, skip_clamp=skip_clamp)
        if final:
            posts_s = " ".join(f"{final[k]:.4f}" for k in h_keys)
            print(f"  {label:<33} {posts_s}")

    print()
    print("Reading: each scenario re-walks the SAME evidence with different mechanics.")
    print("  Attenuation < 1.0 pulls every LR toward 1.0 (simulates de-correlation).")
    print("  NO clamp lets posteriors go past 0.98 ceiling and below 0.005 floor.")
    print("  If saturation persists across many scenarios, evidence is genuinely strong.")
    print("  If it collapses under mild attenuation, the 0.95 is correlation+clamp artifact.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug")
    parser.add_argument("--view", choices=["per-entry", "indicators", "counterfactual", "decorrelated", "all"],
                        default="all")
    args = parser.parse_args()

    topic = load_topic(args.slug)

    if args.view in ("per-entry", "all"):
        view_per_entry(topic)
        print()
    if args.view in ("indicators", "all"):
        view_indicators(topic)
        print()
    if args.view in ("counterfactual", "all"):
        view_counterfactual(topic)
        print()
    if args.view in ("decorrelated", "all"):
        view_decorrelated(topic)


if __name__ == "__main__":
    main()
