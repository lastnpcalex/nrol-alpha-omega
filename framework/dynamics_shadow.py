"""Shadow dynamics model: posteriors as first-passage probabilities of a
regime-switching process, with conjugate-Bayesian intensity inference.

SHADOW MODE: this module has ZERO authority. It never writes topic state.
It derives an independent posterior from a pre-committed dynamics spec so
operators can compare it against the committed (indicator-driven) posterior
before any promotion to a typed transition.

Why this exists: the indicator engine quantizes sub-threshold evidence to
LR=1.0 exactly and has only a deadline cliff for time. For date-banded
hypotheses both are miscalibrated — the passage of time at an unchanged
crisis floor IS evidence about hitting times. Here that statement is a
theorem, not a vibe: holding times in a continuous-time Markov chain are
exponential, the Gamma family is conjugate for their rates, and "no regime
exit for dt days" performs the exact update Gamma(a, b) -> Gamma(a, b + dt).
Hypothesis probabilities are first-passage-time integrals, estimated by
Monte Carlo with parameter uncertainty integrated out (sampling rates from
their Gamma posteriors), fixed seed for reproducibility.

Grounding: conjugate inference for fully-observed CTMC paths is classical
(see e.g. Wiley StatsRef "Bayesian Inference of Markov Processes"; for the
discretely-observed hard case, arXiv:2507.16756). The LLM's role upstream
is confined to bounded evidence_nudges (pseudo-count updates on specific
intensities) — mirroring the sequential-Bayesian direction of current LLM
forecasting systems (Halawi et al. 2024, arXiv:2402.18563, and successors).

Model (deliberately minimal, three regimes):
    E (entrenched closure) --lam_exit-->   R (ramping/normalization)
    R --lam_ramp--> O (reopen: observable crosses the resolution threshold)
    R --lam_relapse--> E
    After reaching O, the reopen only RESOLVES if it survives the sustain
    window (hazard lam_relapse * sustain_hazard_factor); a relapse during
    sustain returns the path to E. The reopen date is the date O is reached.

Spec file (pre-committed; the lint gate refuses to run without it):
    loom/topics/dynamics/<slug>.dynamics.json
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

REQUIRED_PRIOR_FIELDS = {"alpha", "beta_days", "rationale"}
REQUIRED_PRIORS = {"lam_exit", "lam_ramp", "lam_relapse"}


class DynamicsSpecError(ValueError):
    """Spec missing or under-committed — refuse to run rather than guess."""


def _spec_path(repo_root: Path, slug: str) -> Path:
    return repo_root / "loom" / "topics" / "dynamics" / f"{slug}.dynamics.json"


def load_spec(repo_root: Path, slug: str) -> dict:
    path = _spec_path(repo_root, slug)
    if not path.exists():
        raise DynamicsSpecError(
            f"No dynamics spec at {path}. Shadow posteriors require pre-committed "
            "priors — write the spec (with rationales) before running."
        )
    spec = json.loads(path.read_text(encoding="utf-8"))
    lint_spec(spec)
    return spec


def lint_spec(spec: dict) -> None:
    """Hard gate: every prior must be pre-committed WITH a written rationale.

    A prior without a rationale is a vibe with extra steps — exactly what
    this system exists to prevent. Same posture as indicator LR linting.
    """
    problems = []
    priors = spec.get("priors") or {}
    for name in REQUIRED_PRIORS:
        p = priors.get(name)
        if not isinstance(p, dict):
            problems.append(f"missing prior: {name}")
            continue
        missing = REQUIRED_PRIOR_FIELDS - set(p)
        if missing:
            problems.append(f"prior {name} missing fields: {sorted(missing)}")
        elif not str(p.get("rationale", "")).strip():
            problems.append(f"prior {name} has an empty rationale")
        elif p["alpha"] <= 0 or p["beta_days"] <= 0:
            problems.append(f"prior {name} must have alpha>0, beta_days>0")
    for field in ("entrenched_since", "sustain_days", "hypothesis_windows"):
        if field not in spec:
            problems.append(f"missing spec field: {field}")
    for nudge in spec.get("evidence_nudges", []) or []:
        if not str(nudge.get("rationale", "")).strip():
            problems.append(f"evidence_nudge on {nudge.get('parameter')} lacks rationale")
        if nudge.get("parameter") not in REQUIRED_PRIORS:
            problems.append(f"evidence_nudge targets unknown parameter {nudge.get('parameter')}")
        if abs(float(nudge.get("pseudo_events", 0))) > float(nudge.get("max_pseudo_events", 1.0)):
            problems.append("evidence_nudge exceeds its own pseudo-event cap")
    if problems:
        raise DynamicsSpecError("dynamics spec failed lint: " + "; ".join(problems))


def _parse_date(s: str) -> date:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()


def _posterior_rate_params(spec: dict, asof: date) -> dict:
    """Conjugate updates: priors + observed exposure + bounded nudges.

    lam_exit gets the exact-conjugacy time update: the regime has been
    observed in E from entrenched_since to asof with zero exits, so
    beta += elapsed days. Evidence nudges (LLM/jury-proposed, human-gated,
    capped in the spec) add pseudo-events to alpha / pseudo-days to beta.
    """
    priors = spec["priors"]
    params = {}
    for name in REQUIRED_PRIORS:
        params[name] = {
            "alpha": float(priors[name]["alpha"]),
            "beta_days": float(priors[name]["beta_days"]),
        }
    entrenched_since = _parse_date(spec["entrenched_since"])
    elapsed = max(0, (asof - entrenched_since).days)
    params["lam_exit"]["beta_days"] += elapsed  # the month IS evidence — exactly
    for nudge in spec.get("evidence_nudges", []) or []:
        if _parse_date(nudge["date"]) > asof:
            continue
        p = params[nudge["parameter"]]
        p["alpha"] += float(nudge.get("pseudo_events", 0))
        p["beta_days"] += float(nudge.get("pseudo_days", 0))
    params["_elapsed_in_E_days"] = elapsed
    return params


def shadow_posteriors(
    spec: dict,
    asof: date | None = None,
    n_paths: int = 40000,
    seed: int = 20260610,
) -> dict:
    """Monte Carlo first-passage over the regime chain.

    Parameter uncertainty is integrated out by sampling each rate from its
    Gamma posterior per path (the Bayesian predictive, not a plug-in mean).
    Deterministic for a given (spec, asof, n_paths, seed).
    """
    lint_spec(spec)
    asof = asof or date.today()
    params = _posterior_rate_params(spec, asof)
    sustain = float(spec["sustain_days"])
    sustain_factor = float(spec.get("sustain_hazard_factor", 0.5))
    windows = [(hk, _parse_date(d)) for hk, d in spec["hypothesis_windows"].items()]
    windows.sort(key=lambda kv: kv[1])
    residual_key = spec.get("residual_hypothesis", "H_never")
    horizon_days = (windows[-1][1] - asof).days + int(sustain) + 1

    rng = random.Random(seed)
    counts = {hk: 0 for hk, _ in windows}
    counts[residual_key] = 0

    def sample_rate(p):
        return rng.gammavariate(p["alpha"], 1.0 / p["beta_days"])

    for _ in range(n_paths):
        lam_exit = sample_rate(params["lam_exit"])
        lam_ramp = sample_rate(params["lam_ramp"])
        lam_relapse = sample_rate(params["lam_relapse"])
        t = 0.0
        state = "E"
        reopen_day = None
        while t < horizon_days:
            if state == "E":
                t += rng.expovariate(lam_exit) if lam_exit > 0 else horizon_days
                state = "R"
            elif state == "R":
                t_ramp = rng.expovariate(lam_ramp) if lam_ramp > 0 else horizon_days
                t_rel = rng.expovariate(lam_relapse) if lam_relapse > 0 else horizon_days
                if t_ramp <= t_rel:
                    t += t_ramp
                    # candidate reopen at t; must survive the sustain window
                    lam_s = lam_relapse * sustain_factor
                    t_fail = rng.expovariate(lam_s) if lam_s > 0 else sustain + 1
                    if t_fail > sustain:
                        reopen_day = t
                        break
                    t += t_fail
                    state = "E"
                else:
                    t += t_rel
                    state = "E"
        if reopen_day is None:
            counts[residual_key] += 1
            continue
        reopen_date = asof + timedelta(days=reopen_day)
        for hk, deadline in windows:
            if reopen_date <= deadline:
                counts[hk] += 1
                break
        else:
            counts[residual_key] += 1

    post = {hk: round(c / n_paths, 4) for hk, c in counts.items()}
    return {
        "shadow_posteriors": post,
        "asof": asof.isoformat(),
        "n_paths": n_paths,
        "seed": seed,
        "rate_posteriors": {
            k: {"alpha": round(v["alpha"], 3), "beta_days": round(v["beta_days"], 1),
                "mean_rate_per_day": round(v["alpha"] / v["beta_days"], 6),
                "mean_residence_days": round(v["beta_days"] / v["alpha"], 1)}
            for k, v in params.items() if isinstance(v, dict)
        },
        "elapsed_in_entrenched_days": params["_elapsed_in_E_days"],
        "mode": "SHADOW — no authority, derived from pre-committed dynamics spec",
    }


def write_spec(repo_root: str | Path, slug: str, spec: dict) -> str:
    """Lint-then-write a dynamics spec. The only sanctioned write path —
    a spec that fails lint never reaches disk."""
    spec = dict(spec)
    spec.setdefault("slug", slug)
    lint_spec(spec)
    path = _spec_path(Path(repo_root), slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return str(path)


def run(repo_root: str | Path, slug: str, asof: str = "", **kw) -> dict:
    spec = load_spec(Path(repo_root), slug)
    asof_d = _parse_date(asof) if asof else None
    out = shadow_posteriors(spec, asof=asof_d, **kw)
    out["slug"] = slug
    return out


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parent.parent
    slug = sys.argv[1] if len(sys.argv) > 1 else "calibration-hormuz-reopen-2027"
    asof = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(run(root, slug, asof), indent=2))
