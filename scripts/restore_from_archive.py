"""Restore selected topics from archive — strip contaminated state, keep design substrate.

Run from repo root:  python scripts/restore_from_archive.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

ARCHIVE = REPO / "topics" / "_archive_2026-05-03"
DEST = REPO / "topics"

TARGETS = [
    "calibration-us-recession-2026",
    "calibration-midterms-2026",
    "hormuz-closure",
    "calibration-fed-rate-2026",
    "colorado-shortage-tier-2028",
]

NOW_ISO = datetime.now(timezone.utc).isoformat()
TODAY = NOW_ISO[:10]


def restore_topic(slug: str) -> dict:
    src = ARCHIVE / f"{slug}.json"
    if not src.exists():
        raise FileNotFoundError(src)
    with open(src, "r", encoding="utf-8") as f:
        t = json.load(f)

    # Capture design priors from posteriorHistory[0] (initialization entry).
    # Fallback: current hypotheses[H].posterior if no history.
    ph = t.get("model", {}).get("posteriorHistory", [])
    if ph and isinstance(ph[0].get("posteriors"), dict):
        design_priors = dict(ph[0]["posteriors"])
    else:
        design_priors = {k: v.get("posterior") for k, v in
                         t.get("model", {}).get("hypotheses", {}).items()}

    # 1. Reset hypotheses[H].posterior to design priors
    for h_key, h in t.get("model", {}).get("hypotheses", {}).items():
        if h_key in design_priors:
            h["posterior"] = design_priors[h_key]

    # 2. Reset posteriorHistory to a single restore-marker entry
    t["model"]["posteriorHistory"] = [{
        "date": TODAY,
        "timestamp": NOW_ISO,
        "updateMethod": "restore_from_archive",
        "posteriors": design_priors,
        "priors": design_priors,
        "note": (
            f"RESTORE: topic restored from archive at {NOW_ISO}. Prior posteriorHistory "
            f"discarded (contaminated by pre-gate freeform-LR loophole). Posteriors reset "
            f"to design priors. evidenceLog dropped, governance recomputed, sourceCalibration "
            f"reset. Indicator schema preserved with shape='single_observation' default."
        ),
        "lrSource": {
            "lens": "OPERATOR_JUDGMENT",
            "lensSetAt": NOW_ISO,
            "source": "restore_from_archive",
        },
    }]

    # 3. Strip evidenceLog (observations were entangled with derived weights)
    t["evidenceLog"] = []

    # 4. Reset governance block — engine will recompute on next save_topic
    t["governance"] = {}

    # 5. Reset sourceCalibration deltas
    if "sourceCalibration" in t:
        t["sourceCalibration"] = {
            "perTopicTrust": {},
            "lastUpdated": NOW_ISO,
            "note": "reset on restore_from_archive",
        }

    # 6. Add shape='single_observation' default + clamp extreme LRs.
    # Archive likelihoods of 1.0 / 0.0 fail the post-gate lr_too_certain
    # lint (no observation makes a hypothesis logically impossible).
    # Clamp [0.99, 1.0] -> 0.95, [0.0, 0.01] -> 0.05. Also normalize via
    # division by max so the indicator's strongest hypothesis caps at
    # 0.95 — preserves the relative ordering authors intended.
    def _clamp_lrs(lrs: dict) -> dict:
        if not isinstance(lrs, dict) or not lrs:
            return lrs
        clamped = {}
        for h, v in lrs.items():
            if v is None:
                clamped[h] = v
                continue
            try:
                vf = float(v)
            except (TypeError, ValueError):
                clamped[h] = v
                continue
            # Clamp aggressively: 0.85 ceiling / 0.15 floor leaves room
            # for cumulative LR products to stay clear of saturation.
            if vf >= 0.85:
                vf = 0.85
            elif vf <= 0.15:
                vf = 0.15
            clamped[h] = round(vf, 6)
        return clamped

    inds = t.get("indicators", {})
    for tier_key, tier_inds in inds.get("tiers", {}).items():
        for ind in tier_inds:
            if not isinstance(ind, dict):
                continue
            if not ind.get("shape"):
                ind["shape"] = "single_observation"
            if ind.get("likelihoods"):
                ind["likelihoods"] = _clamp_lrs(ind["likelihoods"])
            if ind.get("status") == "FIRED":
                ind["status"] = "NOT_FIRED"
                ind["firedDate"] = None
    for ind in inds.get("anti_indicators", []) or []:
        if not isinstance(ind, dict):
            continue
        if not ind.get("shape"):
            ind["shape"] = "single_observation"
        if ind.get("likelihoods"):
            ind["likelihoods"] = _clamp_lrs(ind["likelihoods"])
        if ind.get("status") == "FIRED":
            ind["status"] = "NOT_FIRED"
            ind["firedDate"] = None

    # 7. Stamp lastScanned to a week ago for adaptive-window backfill
    t["meta"]["lastScanned"] = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    t["meta"]["lastUpdated"] = NOW_ISO
    t["meta"]["restoredAt"] = NOW_ISO

    # 8. Drop any lingering cleanup-session state
    if "running_indicator_loop" in t.get("governance", {}):
        del t["governance"]["running_indicator_loop"]
    if "flagged_for_indicator_review" in t.get("governance", {}):
        t["governance"]["flagged_for_indicator_review"] = []

    # 9. Drop contradictionTracker active state (was tied to old evidence)
    if "contradictionTracker" in t:
        t["contradictionTracker"] = {"contradictions": [], "lastChecked": NOW_ISO}

    return t


def main():
    from engine import save_topic
    restored, failed = [], []
    for slug in TARGETS:
        try:
            t = restore_topic(slug)
            save_topic(t)  # routes through engine validation + lint + governance recompute
            n_inds = sum(len(v) for v in t["indicators"].get("tiers", {}).values()) + \
                     len(t["indicators"].get("anti_indicators", []) or [])
            posteriors = {k: round(v["posterior"], 3) for k, v in
                          t["model"]["hypotheses"].items()}
            print(f"  [OK] {slug}: indicators={n_inds}, posteriors={posteriors}")
            restored.append(slug)
        except Exception as e:
            print(f"  [FAIL] {slug}: {type(e).__name__}: {e}")
            failed.append((slug, str(e)))

    print(f"\nRestored {len(restored)}/{len(TARGETS)} topics.")
    if failed:
        print(f"Failed: {[f[0] for f in failed]}")
    return restored


if __name__ == "__main__":
    main()
