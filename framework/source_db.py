#!/usr/bin/env python3
"""
NRL-Alpha Omega — Source Database
=================================

Cross-topic, domain-aware source performance tracking.

The key finding from hypothesis testing: domain matters more than source
identity for reliability (r=0.159 source vs domain). A source's ECON claims
may be 98% accurate while its RHETORIC claims are 33%. This module tracks
that distinction.

Schema (stored as JSON at sources/source_db.json):

    {
      "sources": {
        "<source_name>": {
            "baseTrust": float,          # initial prior (from calibrate.py)
            "category": str,             # "wire", "government", "state_media", ...
            "domains": {
                "<tag>": {
                    "claims": int,       # total claims observed
                    "confirmed": int,    # confirmed by higher-trust or outcome
                    "refuted": int,      # refuted by outcome
                    "hitRate": float,    # confirmed / (confirmed + refuted)
                    "domainTrust": float # Bayesian posterior for this domain
                }
            },
            "topicHistory": {
                "<topic_slug>": {
                    "claims": int,
                    "confirmed": int,
                    "refuted": int,
                    "hitRate": float,
                    "lastSeen": str      # ISO timestamp
                }
            },
            "effectiveTrust": float,     # overall Bayesian posterior
            "totalClaims": int,
            "totalConfirmed": int,
            "totalRefuted": int,
            "lastUpdated": str
        }
    },
    "meta": {
        "version": 1,
        "lastFullScan": str,
        "topicsScanned": [str]
    }
}

Functions:
    load_db / save_db          — persistence
    ingest_from_topic          — pull resolution data from a topic's sourceCalibration
    get_domain_trust           — domain-specific trust for a source
    get_source_profile         — full profile for a source
    find_domain_patterns       — cross-source analysis (which domains are reliable?)
    export_trust_overrides     — generate SOURCE_TRUST-compatible dict with domain awareness

No external dependencies — Python stdlib only.
"""

import json
import math
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from framework.calibrate import SOURCE_TRUST
from framework.source_ledger import extract_sources


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DB_DIR = Path(__file__).parent.parent / "sources"
_DB_FILE = _DB_DIR / "source_db.json"


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def _empty_db() -> dict:
    """Return an empty source database."""
    return {
        "sources": {},
        "meta": {
            "version": 1,
            "lastFullScan": None,
            "topicsScanned": [],
        },
    }


def _empty_source(name: str) -> dict:
    """Return an empty source record with base trust from calibrate.py."""
    base = SOURCE_TRUST.get(name, 0.50)
    # Infer category from name heuristics
    cat = _infer_category(name)
    return {
        "baseTrust": base,
        "category": cat,
        "domains": {},
        "topicHistory": {},
        "effectiveTrust": base,
        "totalClaims": 0,
        "totalConfirmed": 0,
        "totalRefuted": 0,
        "lastUpdated": None,
    }


def _empty_domain() -> dict:
    return {
        "claims": 0,
        "confirmed": 0,
        "refuted": 0,
        "hitRate": 0.0,
        "domainTrust": None,  # filled on first update
    }


def _empty_topic_record() -> dict:
    return {
        "claims": 0,
        "confirmed": 0,
        "refuted": 0,
        "hitRate": 0.0,
        "lastSeen": None,
    }


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------

_CATEGORY_PATTERNS = {
    "government": {"CENTCOM", "Pentagon", "DoD", "WhiteHouse", "StateDept"},
    "wire": {"Reuters", "AP", "AFP"},
    "broadsheet": {"WashingtonPost", "NewYorkTimes", "WallStreetJournal", "WSJ",
                   "Guardian", "Bloomberg"},
    "broadcast": {"CNN", "BBC", "Fox", "CNBC", "Al Jazeera"},
    "state_media": {"IRNA", "ISNA", "Mehr News", "TASS", "IranianEmbassy",
                    "Xinhua", "RT"},
    "tabloid": {"DailyMail", "Sun", "Mirror"},
    "trade": {"Fortune", "MarineTraffic", "Lloyd's List", "Platts"},
}


def _infer_category(name: str) -> str:
    for cat, names in _CATEGORY_PATTERNS.items():
        if name in names:
            return cat
    return "unknown"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_db() -> dict:
    """Load source database from disk, creating if absent."""
    if _DB_FILE.exists():
        with open(_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return _empty_db()


def save_db(db: dict) -> Path:
    """Persist source database to disk."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    with open(_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    return _DB_FILE


# ---------------------------------------------------------------------------
# Bayesian trust update
# ---------------------------------------------------------------------------

def _bayesian_update(prior: float, hits: int, misses: int,
                     lr_hit: float = 3.0, lr_miss: float = 0.33) -> float:
    """
    Update trust via repeated Bayesian updates.

    For each hit:   posterior = (prior * lr_hit) / (prior * lr_hit + (1 - prior))
    For each miss:  posterior = (prior * lr_miss) / (prior * lr_miss + (1 - prior))

    lr_hit=3.0 means a confirmed claim triples the odds.
    lr_miss=0.33 means a refuted claim cuts odds to 1/3.
    """
    p = max(0.01, min(0.99, prior))

    for _ in range(hits):
        p = (p * lr_hit) / (p * lr_hit + (1.0 - p))

    for _ in range(misses):
        p = (p * lr_miss) / (p * lr_miss + (1.0 - p))

    return round(max(0.01, min(0.99, p)), 4)


# ---------------------------------------------------------------------------
# Ingest from topic
# ---------------------------------------------------------------------------

def ingest_from_topic(db: dict, topic: dict) -> dict:
    """
    Pull resolution data from a topic's sourceCalibration ledger and
    evidence log into the source database.

    Each ledger entry has: source, result (CONFIRMED/REFUTED), evidence text,
    and we also extract the tag from the evidence log entry.

    Returns summary dict with counts.
    """
    slug = topic.get("meta", {}).get("slug", "unknown")
    cal = topic.get("sourceCalibration", {})
    ledger = cal.get("ledger", [])
    evidence_log = topic.get("evidenceLog", [])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build index: evidence text -> tag (for domain tracking)
    tag_index = {}
    for entry in evidence_log:
        text = entry.get("text", "")
        tag = entry.get("tag", "")
        if text and tag:
            tag_index[text[:80]] = tag  # use first 80 chars as key

    added = 0
    skipped = 0

    for rec in ledger:
        source_str = rec.get("source", "")
        # Ledger uses "resolution" (from source_ledger.py scan_for_resolutions)
        result = rec.get("resolution") or rec.get("result", "")
        evidence_text = rec.get("original_text_snippet") or rec.get("evidence_text", "")

        if not source_str or result not in ("CONFIRMED", "REFUTED"):
            skipped += 1
            continue

        # Determine domain tag from the evidence log entry
        tag = rec.get("tag", "")
        if not tag and evidence_text:
            tag = tag_index.get(evidence_text[:80], "")
        # Also try matching by evidence_index
        if not tag:
            ev_idx = rec.get("evidence_index")
            if ev_idx is not None and 0 <= ev_idx < len(evidence_log):
                tag = evidence_log[ev_idx].get("tag", "")
        if not tag:
            tag = "UNKNOWN"

        # Decompose compound sources
        sources = extract_sources(source_str)
        if not sources:
            sources = [source_str]

        is_hit = result == "CONFIRMED"

        for src in sources:
            # Ensure source exists in db
            if src not in db["sources"]:
                db["sources"][src] = _empty_source(src)

            s = db["sources"][src]

            # Update domain stats
            if tag not in s["domains"]:
                s["domains"][tag] = _empty_domain()
            d = s["domains"][tag]
            d["claims"] += 1
            if is_hit:
                d["confirmed"] += 1
            else:
                d["refuted"] += 1
            total_resolved = d["confirmed"] + d["refuted"]
            d["hitRate"] = round(d["confirmed"] / total_resolved, 4) if total_resolved > 0 else 0.0
            d["domainTrust"] = _bayesian_update(
                s["baseTrust"], d["confirmed"], d["refuted"]
            )

            # Update topic history
            if slug not in s["topicHistory"]:
                s["topicHistory"][slug] = _empty_topic_record()
            th = s["topicHistory"][slug]
            th["claims"] += 1
            if is_hit:
                th["confirmed"] += 1
            else:
                th["refuted"] += 1
            th_resolved = th["confirmed"] + th["refuted"]
            th["hitRate"] = round(th["confirmed"] / th_resolved, 4) if th_resolved > 0 else 0.0
            th["lastSeen"] = now

            # Update totals
            s["totalClaims"] += 1
            if is_hit:
                s["totalConfirmed"] += 1
            else:
                s["totalRefuted"] += 1

            # Recompute overall effective trust
            s["effectiveTrust"] = _bayesian_update(
                s["baseTrust"], s["totalConfirmed"], s["totalRefuted"]
            )
            s["lastUpdated"] = now

            added += 1

    # Update meta
    if slug not in db["meta"]["topicsScanned"]:
        db["meta"]["topicsScanned"].append(slug)
    db["meta"]["lastFullScan"] = now

    return {
        "topic": slug,
        "ledger_entries": len(ledger),
        "ingested": added,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def get_domain_trust(db: dict, source_name: str, tag: str) -> float:
    """
    Get domain-specific trust for a source.

    Falls back: domain trust -> overall effective trust -> base trust -> 0.5
    """
    src = db.get("sources", {}).get(source_name)
    if not src:
        return SOURCE_TRUST.get(source_name, 0.50)

    domain = src.get("domains", {}).get(tag)
    if domain and domain.get("domainTrust") is not None:
        return domain["domainTrust"]

    return src.get("effectiveTrust", src.get("baseTrust", 0.50))


def get_source_profile(db: dict, source_name: str) -> dict | None:
    """Return full profile for a source, or None if not tracked."""
    return db.get("sources", {}).get(source_name)


def find_domain_patterns(db: dict, min_claims: int = 3) -> dict:
    """
    Cross-source analysis: which domains are most/least reliable?

    Returns:
        {
            "domain_stats": {
                "<tag>": {
                    "sources": int,
                    "total_claims": int,
                    "avg_hit_rate": float,
                    "avg_domain_trust": float,
                    "best_source": str,
                    "worst_source": str,
                }
            },
            "source_variance": {
                "<source>": {
                    "best_domain": (tag, trust),
                    "worst_domain": (tag, trust),
                    "spread": float  # best - worst
                }
            }
        }
    """
    domain_agg = {}  # tag -> list of (source, hitRate, domainTrust)
    source_var = {}  # source -> list of (tag, domainTrust)

    for src_name, src_data in db.get("sources", {}).items():
        for tag, dom in src_data.get("domains", {}).items():
            if dom["claims"] < min_claims:
                continue

            if tag not in domain_agg:
                domain_agg[tag] = []
            domain_agg[tag].append((src_name, dom["hitRate"], dom.get("domainTrust", 0.5)))

            if src_name not in source_var:
                source_var[src_name] = []
            source_var[src_name].append((tag, dom.get("domainTrust", 0.5)))

    # Aggregate domain stats
    domain_stats = {}
    for tag, entries in domain_agg.items():
        hit_rates = [hr for _, hr, _ in entries]
        trusts = [dt for _, _, dt in entries]
        best = max(entries, key=lambda x: x[2])
        worst = min(entries, key=lambda x: x[2])
        domain_stats[tag] = {
            "sources": len(entries),
            "total_claims": sum(1 for _ in entries),
            "avg_hit_rate": round(sum(hit_rates) / len(hit_rates), 4),
            "avg_domain_trust": round(sum(trusts) / len(trusts), 4),
            "best_source": best[0],
            "worst_source": worst[0],
        }

    # Source variance
    source_variance = {}
    for src_name, domain_list in source_var.items():
        if len(domain_list) < 2:
            continue
        best = max(domain_list, key=lambda x: x[1])
        worst = min(domain_list, key=lambda x: x[1])
        source_variance[src_name] = {
            "best_domain": list(best),
            "worst_domain": list(worst),
            "spread": round(best[1] - worst[1], 4),
        }

    return {
        "domain_stats": domain_stats,
        "source_variance": source_variance,
    }


def export_trust_overrides(db: dict, tag: str | None = None) -> dict:
    """
    Generate a SOURCE_TRUST-compatible dict, optionally domain-specific.

    If tag is provided, returns domain-specific trust values.
    Otherwise, returns overall effective trust.
    """
    overrides = {}
    for src_name, src_data in db.get("sources", {}).items():
        if tag:
            dom = src_data.get("domains", {}).get(tag)
            if dom and dom.get("domainTrust") is not None:
                overrides[src_name] = dom["domainTrust"]
            else:
                overrides[src_name] = src_data.get("effectiveTrust", src_data["baseTrust"])
        else:
            overrides[src_name] = src_data.get("effectiveTrust", src_data["baseTrust"])

    return overrides


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NRL-AO Source Database")
    parser.add_argument("command", choices=["ingest", "profile", "domains", "export"],
                        help="Command to run")
    parser.add_argument("--topic", help="Topic slug for ingest")
    parser.add_argument("--source", help="Source name for profile")
    parser.add_argument("--tag", help="Domain tag for export")
    parser.add_argument("--min-claims", type=int, default=3,
                        help="Min claims for domain pattern analysis")
    args = parser.parse_args()

    from engine import load_topic

    db = load_db()

    if args.command == "ingest":
        if not args.topic:
            parser.error("--topic required for ingest")
        topic = load_topic(args.topic)
        result = ingest_from_topic(db, topic)
        save_db(db)
        print(f"Ingested: {result}")

    elif args.command == "profile":
        if not args.source:
            parser.error("--source required for profile")
        profile = get_source_profile(db, args.source)
        if profile:
            print(json.dumps(profile, indent=2))
        else:
            print(f"Source '{args.source}' not tracked yet.")

    elif args.command == "domains":
        patterns = find_domain_patterns(db, min_claims=args.min_claims)
        print(json.dumps(patterns, indent=2))

    elif args.command == "export":
        overrides = export_trust_overrides(db, tag=args.tag)
        print(json.dumps(overrides, indent=2))
