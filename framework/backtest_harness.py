"""
backtest_harness — empirical LR calibration for indicators with historical
analogs. Reads fixtures from framework/backtest_data/<slug>.json. Fixtures
are assembled at design time from authoritative public sources (NBER, FRED,
BLS, BEA, etc.) — see fixture _provenance fields. NEVER hand-encoded from
LLM memory.

Runs after Phase 3 design lock. Produces topic.governance.calibrationReport
and recommends meta.calibrationStatus value. Engine refuses bayesian_update
on a topic without a valid calibrationStatus.

Status outcomes:
  - VALIDATED: enough analogs, all indicators within deviation tolerance
  - VALIDATED_WITH_FLAGS: scored, ≥1 indicator deviation > 30%
  - UN_BACKTESTABLE: novel event, no historical analog (Hormuz 2027,
    AI market structure 2028, etc.) — operator-judgment-only by construction
  - PENDING_DATA_INGESTION: backtestable in principle, but the historical
    data layer for the indicator's source isn't wired yet

NOTE on current implementation:
  This is a SKELETON. The data-ingestion layer (FRED client, BLS history,
  polling archives, etc.) is a separate engineering project. This module
  classifies topics into the four status buckets based on declared dataFeeds
  metadata. Topics that mark themselves UN_BACKTESTABLE proceed; topics
  flagged PENDING_DATA_INGESTION cannot reach VALIDATED until the data
  layer is wired and individual indicators are scored against history.

  When the data layer is implemented, _empirical_lr_for_indicator() needs
  to be replaced with real history-querying code per indicator type.
"""
from typing import Optional


# Topics with no historical analog by design — declared up-front
UN_BACKTESTABLE_TOPICS = {
    # Novel geopolitical events
    "calibration-hormuz-reopen-2027",
    # Forward-looking market structure with no comparable prior episode
    "calibration-ai-market-structure-2028",
    "calibration-ai-local-share-2028",
    # 2030 endpoint: the endpoint itself has no analog (consumer GPU pricing
    # under sustained AI capex regime is a novel context)
    "calibration-gpu-vram-prices-2030",
}


# Source-tag inference: data feeds whose history can be queried in principle
# (gates UN_BACKTESTABLE vs PENDING_DATA_INGESTION classification)
KNOWN_DATA_SOURCES = {
    "Bureau of Labor Statistics", "BLS",
    "Bureau of Economic Analysis", "BEA",
    "Department of Labor",
    "Federal Reserve", "FRED", "FOMC", "SEP",
    "Institute for Supply Management", "ISM",
    "ICE Data Services",
    "FDIC",
    "Conference Board",
    "NBER",
    "RealClearPolitics", "RCP",
    "Cook Political Report",
    "FEC",
    "NRCS USDA", "SNOTEL",
    "USBR", "Bureau of Reclamation", "CBRFC",
    "NOAA", "CPC",
    "DOI", "Department of Interior",
    "AP", "Associated Press", "Reuters",
    "SEC",
}


def _all_indicators(topic: dict) -> list[dict]:
    out = []
    inds = topic.get("indicators", {})
    for tk in ("tier1_critical", "tier2_strong", "tier3_suggestive"):
        out.extend(inds.get("tiers", {}).get(tk, []))
    out.extend(inds.get("anti_indicators", []))
    return out


def _has_known_source(text: str) -> bool:
    """Check whether any KNOWN_DATA_SOURCES token appears in source string."""
    if not text:
        return False
    text_lower = text.lower()
    return any(src.lower() in text_lower for src in KNOWN_DATA_SOURCES)


def _classify_indicator(ind: dict) -> str:
    """Return: BACKTESTABLE | UN_BACKTESTABLE_NOVEL | PENDING_DATA"""
    desc = ind.get("desc", "")
    # Crude heuristic: does desc reference a recognized historical-data publisher?
    if _has_known_source(desc):
        return "BACKTESTABLE"
    return "PENDING_DATA"


import json
from pathlib import Path


_FIXTURE_DIR = Path(__file__).parent / "backtest_data"


def _load_fixture(slug: str) -> Optional[dict]:
    """Load fixture from framework/backtest_data/<slug>.json. Returns None
    if no fixture exists. Fixtures are real-data tables, never LLM-recall."""
    path = _FIXTURE_DIR / f"{slug}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _empirical_lr_for_indicator(ind: dict, topic: dict) -> Optional[dict]:
    """Query historical data fixture for empirical P(E|H) per hypothesis.

    Supports two fixture entry schemas:

    SCHEMA A (legacy, trigger_events list):
      indicator_firings[ind_id] = {
        "trigger_events": [
          {"trigger_yyyy_mm": "...", "outcome": "RECESSION"|"FALSE_POSITIVE",
           "outcome_class": "H2"|"H3"|"H4"|"H1_TECHNICAL_ONLY"}, ...
        ]
      }

    SCHEMA B (preferred, per-recession boolean):
      indicator_firings[ind_id] = {
        "_per_recession_fired_at_least_once": {
          "<peak>_<class>": true|false|"PRE_DATA"|other_string,
          ...
        },
        "_false_positives_in_h1_years": [{...}],
        "_h1_year_firing_rate": optional explicit override of H1 rate,
      }

    Beta(1,1) Laplace smoothing applied to all rates. Returns None if no
    fixture entry; caller treats as PENDING_DATA_INGESTION.
    """
    slug = topic.get("meta", {}).get("slug")
    if not slug:
        return None
    fx = _load_fixture(slug)
    if not fx:
        return None
    firings_section = fx.get("indicator_firings", {})
    ind_data = firings_section.get(ind["id"])
    if not ind_data:
        return None

    # Total class population from fixture
    counts = fx.get("_class_counts", {})
    h1_total = (counts.get("H1_calendar_years_no_recession_1948_2025", 0)
                + counts.get("H1_technical_only_2001", 0))
    h_totals = {
        "H1": h1_total,
        "H2": counts.get("H2_recessions", 0),
        "H3": counts.get("H3_recessions", 0),
        "H4": counts.get("H4_recessions", 0),
    }

    fire_count_by_class = {"H1": 0, "H2": 0, "H3": 0, "H4": 0}
    sample_size_caveats = []

    # SCHEMA A: trigger_events list
    if "trigger_events" in ind_data:
        for tev in ind_data["trigger_events"]:
            outcome = tev.get("outcome")
            if outcome == "FALSE_POSITIVE":
                fire_count_by_class["H1"] += 1
            elif outcome == "RECESSION":
                cls = tev.get("outcome_class", "")
                if cls == "H1_TECHNICAL_ONLY":
                    fire_count_by_class["H1"] += 1
                elif cls in fire_count_by_class:
                    fire_count_by_class[cls] += 1

    # SCHEMA B: per-recession boolean dict
    elif "_per_recession_fired_at_least_once" in ind_data:
        per_rec = ind_data["_per_recession_fired_at_least_once"]
        usable_h_totals = {"H1": h1_total, "H2": 0, "H3": 0, "H4": 0}
        for key, fired in per_rec.items():
            # key is like "1948_11_H2" or "2001_03_H1_TECHNICAL_ONLY"
            cls = None
            if key.endswith("_H2"):
                cls = "H2"
            elif key.endswith("_H3"):
                cls = "H3"
            elif key.endswith("_H4"):
                cls = "H4"
            elif "H1_TECHNICAL" in key:
                cls = "H1"  # Technical-only counts as H1 per topic def
            if cls is None:
                continue
            # If marked unusable (pre-data window, missing data), skip
            if isinstance(fired, str):
                sample_size_caveats.append(f"{key}: {fired}")
                continue
            if cls != "H1":
                usable_h_totals[cls] += 1
                if fired is True:
                    fire_count_by_class[cls] += 1
            else:
                # H1_TECHNICAL counts toward H1 total + firings
                if fired is True:
                    fire_count_by_class["H1"] += 1
        # H1 false positives in non-recession years
        fp_list = ind_data.get("_false_positives_in_h1_years", [])
        fire_count_by_class["H1"] += len(fp_list)
        # Use usable H2/H3/H4 totals (excluding sample-size-caveat entries)
        for h in ("H2", "H3", "H4"):
            h_totals[h] = max(usable_h_totals[h], 0)

        # Optional explicit H1 rate override
        if "_h1_year_firing_rate" in ind_data:
            sample_size_caveats.append(
                f"H1 rate explicitly set in fixture: {ind_data['_h1_year_firing_rate']}"
            )

    else:
        # No recognized firing-data schema
        return None

    # Apply Beta(1,1) smoothing
    empirical_lr = {}
    for h in ("H1", "H2", "H3", "H4"):
        n_e = fire_count_by_class[h]
        n_h = h_totals[h]
        if n_h == 0:
            # No usable observations for this class
            empirical_lr[h] = None
            continue
        empirical_lr[h] = round((n_e + 1) / (n_h + 2), 4)

    return {
        "p_e_given_h": empirical_lr,
        "raw_counts": {h: {"fires": fire_count_by_class[h], "total": h_totals[h]}
                       for h in ("H1","H2","H3","H4")},
        "smoothing": "Beta(1,1)",
        "sample_size_caveats": sample_size_caveats,
        "n_total_class_periods": sum(h_totals.values()),
        "n_total_firings": sum(fire_count_by_class.values()),
    }


def _classify_indicator_with_fixture(ind: dict, topic: dict) -> str:
    """If fixture has firing data for this indicator, classify as
    BACKTESTABLE_FIXTURE_AVAILABLE. Otherwise fall back to source-recognition."""
    slug = topic.get("meta", {}).get("slug", "")
    fx = _load_fixture(slug)
    if fx and ind["id"] in fx.get("indicator_firings", {}):
        return "BACKTESTABLE_FIXTURE_AVAILABLE"
    return _classify_indicator(ind)


def run_backtest(topic: dict) -> dict:
    """Run backtest harness on topic. Returns:
      {status: VALIDATED | VALIDATED_WITH_FLAGS | UN_BACKTESTABLE | PENDING_DATA_INGESTION,
       reason: str,
       per_indicator: {ind_id: {classification, empirical_lr, declared_lr, deviation}},
       flags: [{ind_id, reason}],
       recommended_calibrationStatus: str}
    """
    slug = topic.get("meta", {}).get("slug", "<unknown>")
    inds = _all_indicators(topic)
    per_ind = {}

    if slug in UN_BACKTESTABLE_TOPICS:
        for ind in inds:
            per_ind[ind["id"]] = {
                "classification": "UN_BACKTESTABLE_NOVEL",
                "empirical_lr": None,
                "declared_lr": ind.get("likelihoods"),
                "deviation": None,
            }
        return {
            "status": "UN_BACKTESTABLE",
            "reason": f"Topic {slug} concerns a novel event with no historical analog (declared in UN_BACKTESTABLE_TOPICS).",
            "per_indicator": per_ind,
            "flags": [],
            "recommended_calibrationStatus": "UN_BACKTESTABLE",
        }

    # Classify each indicator
    n_validated = 0
    n_pending = 0
    flags = []
    n_indicators_with_high_deviation = 0
    DEVIATION_THRESHOLD = 0.30  # 30% relative deviation flags an indicator
    # Absolute floor: very small absolute differences don't flag even if
    # relative deviation is high. Prevents phantom_precision-constrained
    # H1 LRs (which must stay above 0.05 to keep max/min ratio < 20) from
    # spuriously flagging when empirical H1 is near zero.
    ABSOLUTE_FLOOR = 0.05

    for ind in inds:
        classification = _classify_indicator_with_fixture(ind, topic)
        emp = _empirical_lr_for_indicator(ind, topic)
        declared = ind.get("likelihoods", {})

        # Compute per-hypothesis deviation if we have empirical numbers
        deviation_per_h = None
        max_deviation = None
        if emp is not None:
            p_e_h = emp.get("p_e_given_h", {})
            deviation_per_h = {}
            for h in ("H1", "H2", "H3", "H4"):
                if h in p_e_h and h in declared:
                    d_val = declared[h]
                    e_val = p_e_h[h]
                    # Skip H values where empirical is None (no data for class)
                    if e_val is None:
                        deviation_per_h[h] = None
                        continue
                    # Deviation metric: relative to declared, but with a 0.1
                    # absolute floor on the denominator. This prevents tiny
                    # absolute differences (e.g., 0.05 vs 0.015) from blowing
                    # up to >200% relative deviation when both values are near
                    # zero. Effectively: |d-e| / max(d, 0.1)
                    rel = abs(d_val - e_val) / max(d_val, 0.10)
                    deviation_per_h[h] = round(rel, 3)
            if deviation_per_h:
                _devs = [v for v in deviation_per_h.values() if v is not None]
                max_deviation = max(_devs) if _devs else None
                # Compute max absolute difference too — flag only if BOTH
                # relative > threshold AND absolute > floor
                _abs_diffs = []
                for h in ("H1", "H2", "H3", "H4"):
                    if h in p_e_h and p_e_h[h] is not None and h in declared:
                        _abs_diffs.append(abs(declared[h] - p_e_h[h]))
                max_abs_diff = max(_abs_diffs) if _abs_diffs else 0.0
                if (max_deviation is not None
                        and max_deviation > DEVIATION_THRESHOLD
                        and max_abs_diff > ABSOLUTE_FLOOR):
                    n_indicators_with_high_deviation += 1
                    flags.append({
                        "ind_id": ind["id"],
                        "reason": f"Empirical LR diverges >{int(DEVIATION_THRESHOLD*100)}% from declared AND absolute difference >{ABSOLUTE_FLOOR} on at least one hypothesis (max_deviation={max_deviation:.2f}, max_abs={max_abs_diff:.3f}). Per-H: {deviation_per_h}",
                        "declared": declared,
                        "empirical": p_e_h,
                    })

        per_ind[ind["id"]] = {
            "classification": classification,
            "empirical_lr": emp,
            "declared_lr": declared,
            "deviation_per_h": deviation_per_h,
            "max_deviation": max_deviation,
        }

        if classification == "BACKTESTABLE_FIXTURE_AVAILABLE":
            n_validated += 1
        else:
            n_pending += 1
            flags.append({
                "ind_id": ind["id"],
                "reason": f"No fixture entry — indicator is PENDING_DATA_INGESTION.",
            })

    # Status decision
    if n_validated == 0:
        return {
            "status": "PENDING_DATA_INGESTION",
            "reason": (f"0/{len(inds)} indicators have fixture data. Build fixtures at "
                       f"framework/backtest_data/{slug}.json from authoritative public "
                       f"sources before topic can reach VALIDATED."),
            "per_indicator": per_ind,
            "flags": flags,
            "n_validated": n_validated,
            "n_pending": n_pending,
            "n_high_deviation": n_indicators_with_high_deviation,
            "recommended_calibrationStatus": "PENDING_DATA_INGESTION",
        }

    if n_pending > 0:
        return {
            "status": "VALIDATED_WITH_FLAGS",
            "reason": (f"{n_validated}/{len(inds)} indicators backtested empirically; "
                       f"{n_pending} pending fixture data; "
                       f"{n_indicators_with_high_deviation} backtested indicators have "
                       f">{int(DEVIATION_THRESHOLD*100)}% LR deviation requiring revision."),
            "per_indicator": per_ind,
            "flags": flags,
            "n_validated": n_validated,
            "n_pending": n_pending,
            "n_high_deviation": n_indicators_with_high_deviation,
            "recommended_calibrationStatus": "VALIDATED_WITH_FLAGS",
        }

    # All indicators have fixture data
    if n_indicators_with_high_deviation > 0:
        return {
            "status": "VALIDATED_WITH_FLAGS",
            "reason": (f"All {n_validated} indicators backtested empirically; "
                       f"{n_indicators_with_high_deviation} have >{int(DEVIATION_THRESHOLD*100)}% "
                       f"LR deviation requiring revision before VALIDATED."),
            "per_indicator": per_ind,
            "flags": flags,
            "n_validated": n_validated,
            "n_pending": 0,
            "n_high_deviation": n_indicators_with_high_deviation,
            "recommended_calibrationStatus": "VALIDATED_WITH_FLAGS",
        }

    return {
        "status": "VALIDATED",
        "reason": f"All {n_validated} indicators backtested with empirical-vs-declared LR deviation under {int(DEVIATION_THRESHOLD*100)}%.",
        "per_indicator": per_ind,
        "flags": flags,
        "n_validated": n_validated,
        "n_pending": 0,
        "n_high_deviation": 0,
        "recommended_calibrationStatus": "VALIDATED",
    }
