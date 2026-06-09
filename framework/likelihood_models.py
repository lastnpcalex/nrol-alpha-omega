"""
Mechanical evaluation of pre-committed likelihood models.

Each indicator that supports continuous evaluation declares an `observable`
block on the topic JSON:

    observable:
      metric: str                  # controlled-vocabulary metric ID
      family: "logistic" | "count_event" | "binary_event"
      threshold_value: float       # value at which committed LR applies
      baseline: float              # empirically-uninformative reference value
      direction: "higher_strengthens" | "lower_strengthens"

Given an observation extracted by the matcher, `evaluate(observable,
committed_lr, observed_value)` returns a per-H LR vector derived by
geometric interpolation in log-LR space between the baseline (LR=1.0)
and the threshold (LR=committed_lr).

Post-conditions:
- At baseline: LR = 1.0 for all H (uninformative)
- At threshold: LR = committed_lr (full strength)
- Beyond threshold (in strengthening direction): caps at committed_lr
- Beyond baseline (in weakening direction): caps at LR = 1.0
- Engine's [0.01, 0.99] clamp still applies upstream

The pre-commitment surface is unchanged from today: existing
`indicator.likelihoods` is still the authored-and-audited LR. The new
`observable` block is structured restatement of facts already implicit
in `indicator.desc` plus one new datum (`baseline`).

Usage:

    from framework.likelihood_models import evaluate, lint_observable
    derived_lrs = evaluate(
        observable=indicator["observable"],
        committed_lr=indicator["likelihoods"],
        observed_value=389000,
    )
    # derived_lrs is a per-H dict; route through engine.bayesian_update
    # with indicator_id and these as the likelihoods.
"""

VALID_FAMILIES = {"logistic", "count_event", "binary_event"}
VALID_DIRECTIONS = {"higher_strengthens", "lower_strengthens"}


def _alpha_continuous(value: float, baseline: float, threshold: float,
                     direction: str) -> float:
    """
    Compute observation strength alpha in [0, 1].

    alpha = 0 at baseline (uninformative)
    alpha = 1 at threshold (full strength)
    Linear interpolation between (piecewise; saturates outside).

    Direction:
      higher_strengthens: alpha grows as value rises from baseline to threshold
      lower_strengthens: alpha grows as value falls from baseline to threshold
    """
    if baseline == threshold:
        return 0.0
    if direction == "higher_strengthens":
        if value <= baseline:
            return 0.0
        if value >= threshold:
            return 1.0
        return (value - baseline) / (threshold - baseline)
    elif direction == "lower_strengthens":
        if value >= baseline:
            return 0.0
        if value <= threshold:
            return 1.0
        return (baseline - value) / (baseline - threshold)
    else:
        raise ValueError(f"unknown direction: {direction!r}; "
                         f"valid: {sorted(VALID_DIRECTIONS)}")


def evaluate(observable: dict, committed_lr: dict, observed_value) -> dict:
    """
    Derive a per-H LR vector from the indicator's observable block, its
    pre-committed LR at threshold, and the matcher-extracted observed value.

    Args:
        observable: dict with keys family, threshold_value, baseline,
                    direction, metric (see module docstring).
        committed_lr: dict {H1: float, H2: float, ...} — the indicator's
                      pre-committed P(E|H) at threshold (existing schema field).
        observed_value: numeric value extracted from the article by the
                        matcher subagent.

    Returns:
        dict {H1: applied_lr, H2: applied_lr, ...} per hypothesis. The
        applied LR is geometrically interpolated between 1.0 (baseline) and
        committed_lr[H] (threshold) by the observation strength alpha.

    Raises:
        ValueError on malformed observable, unknown family, or invalid
        direction.
    """
    family = observable.get("family")
    if family not in VALID_FAMILIES:
        raise ValueError(f"unknown family: {family!r}; "
                         f"valid: {sorted(VALID_FAMILIES)}")

    if family == "binary_event":
        # Continuous evaluation does not apply; caller should use the
        # existing FIRE/PARK indicator path. Returning committed_lr
        # unchanged lets engine apply it iff the caller chooses to fire.
        return dict(committed_lr)

    threshold = observable.get("threshold_value")
    baseline = observable.get("baseline")
    direction = observable.get("direction")

    if threshold is None:
        raise ValueError("observable missing threshold_value")
    if baseline is None:
        raise ValueError("observable missing baseline "
                         "(required for continuous families)")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"observable direction must be one of "
                         f"{sorted(VALID_DIRECTIONS)}; got {direction!r}")

    threshold_f = float(threshold)
    baseline_f = float(baseline)
    value_f = float(observed_value)

    alpha = _alpha_continuous(value_f, baseline_f, threshold_f, direction)

    if alpha <= 0.0:
        return {h: 1.0 for h in committed_lr}
    if alpha >= 1.0:
        return dict(committed_lr)

    # Geometric interpolation in log-LR space:
    #   applied = 1.0^(1-alpha) * committed^alpha = committed^alpha
    return {h: float(committed_lr[h]) ** alpha for h in committed_lr}


def lint_observable(observable: dict) -> list:
    """
    Sanity-check an observable block. Returns list of error strings; empty
    list = clean.
    """
    errors = []
    family = observable.get("family")
    if family not in VALID_FAMILIES:
        errors.append(
            f"family must be one of {sorted(VALID_FAMILIES)}; "
            f"got {family!r}")
        return errors

    metric = observable.get("metric")
    if not metric or not str(metric).strip():
        errors.append("metric (non-empty string) required")

    if family == "binary_event":
        return errors

    threshold = observable.get("threshold_value")
    baseline = observable.get("baseline")
    direction = observable.get("direction")

    if threshold is None:
        errors.append("threshold_value required for continuous families")
    if baseline is None:
        errors.append("baseline required for continuous families")
    if direction not in VALID_DIRECTIONS:
        errors.append(
            f"direction must be one of {sorted(VALID_DIRECTIONS)}; "
            f"got {direction!r}")

    if errors:
        return errors

    try:
        bl = float(baseline)
        th = float(threshold)
    except (TypeError, ValueError):
        errors.append("baseline and threshold_value must be numeric")
        return errors

    if bl == th:
        errors.append("baseline equals threshold_value (degenerate; "
                      "no LR shift possible)")

    if direction == "higher_strengthens" and th <= bl:
        errors.append(f"direction=higher_strengthens but threshold ({th}) "
                      f"<= baseline ({bl}); should be strictly greater")
    if direction == "lower_strengthens" and th >= bl:
        errors.append(f"direction=lower_strengthens but threshold ({th}) "
                      f">= baseline ({bl}); should be strictly less")

    return errors


# Self-test — run this file directly to verify behavior.
if __name__ == "__main__":
    # Test fixture: hormuz transit %, lower_strengthens, threshold 25%, baseline 90% (pre-2025)
    ob = {
        "family": "logistic",
        "metric": "hormuz:transit_pct_of_2024_baseline",
        "threshold_value": 25,
        "baseline": 90,
        "direction": "lower_strengthens",
    }
    committed = {"H1": 0.10, "H2": 0.30, "H3": 0.85, "H4": 0.95}

    print("Lint:", lint_observable(ob))
    assert lint_observable(ob) == []

    # At baseline: uninformative
    r = evaluate(ob, committed, 90)
    assert r == {"H1": 1.0, "H2": 1.0, "H3": 1.0, "H4": 1.0}, r
    print("baseline=90 -> uninformative:", r)

    # At threshold: full LR
    r = evaluate(ob, committed, 25)
    assert r == committed, r
    print("threshold=25 -> full LR:", r)

    # Halfway (transit at 57.5%): partial LR
    r = evaluate(ob, committed, 57.5)
    print("halfway=57.5 -> partial LR:", r)
    for h in committed:
        assert committed[h] < r[h] < 1.0, (h, r[h])

    # Strengthens further past threshold (transit at 10%): caps at full LR
    r = evaluate(ob, committed, 10)
    assert r == committed, r
    print("past-threshold=10 -> capped at full LR:", r)

    # Weaker than baseline (transit at 110%): uninformative
    r = evaluate(ob, committed, 110)
    assert r == {"H1": 1.0, "H2": 1.0, "H3": 1.0, "H4": 1.0}, r
    print("past-baseline=110 -> uninformative:", r)

    # Test higher_strengthens direction (e.g., kinetic event count)
    ob2 = {
        "family": "count_event",
        "metric": "hormuz:vessels_struck_30d",
        "threshold_value": 2,
        "baseline": 0,
        "direction": "higher_strengthens",
    }
    assert lint_observable(ob2) == []
    committed2 = {"H1": 0.20, "H2": 0.50, "H3": 0.85, "H4": 0.92}

    # At baseline (no events): uninformative
    r = evaluate(ob2, committed2, 0)
    assert r == {"H1": 1.0, "H2": 1.0, "H3": 1.0, "H4": 1.0}, r
    print("count=0 -> uninformative:", r)

    # 1 event (halfway between 0 and 2): partial LR
    r = evaluate(ob2, committed2, 1)
    print("count=1 -> partial LR:", r)
    for h in committed2:
        assert committed2[h] < r[h] < 1.0, (h, r[h])

    # Threshold (2 events): full LR
    r = evaluate(ob2, committed2, 2)
    assert r == committed2, r
    print("count=2 -> full LR:", r)

    # Lint failure cases
    bad = [
        ({"family": "unknown"}, "unknown family"),
        ({"family": "logistic", "metric": "x", "threshold_value": 5,
          "baseline": 5, "direction": "higher_strengthens"},
         "baseline equals threshold"),
        ({"family": "logistic", "metric": "x", "threshold_value": 5,
          "baseline": 10, "direction": "higher_strengthens"},
         "wrong direction sign"),
        ({"family": "logistic", "metric": "", "threshold_value": 5,
          "baseline": 1, "direction": "higher_strengthens"},
         "empty metric"),
    ]
    for ob_bad, label in bad:
        errs = lint_observable(ob_bad)
        assert errs, f"expected lint errors for {label}, got none"
        print(f"lint catches '{label}':", errs[0])

    print("\nAll likelihood_models self-tests passed.")
