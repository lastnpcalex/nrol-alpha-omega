#!/usr/bin/env python3
"""Create calibration topic stubs for prospective forecasting."""
import json

stubs = [
    {
        "slug": "calibration-us-recession-2026",
        "title": "US Recession by Q4 2026",
        "question": "Will the US economy enter a recession (2 consecutive quarters of negative real GDP) by Q4 2026?",
        "resolution": "BEA official GDP prints for Q2 and Q3 2026. Two consecutive negative quarters = recession.",
        "topicType": "economics",
        "hypotheses": {
            "H1": {"label": "No recession — GDP stays positive", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Technical recession — 2 negative quarters, shallow (<-1%)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Significant recession — unemployment >5.5%", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "Stagflation — negative growth + inflation >4%", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~6 months (Q3/Q4 GDP prints)",
    },
    {
        "slug": "calibration-ukraine-ceasefire",
        "title": "Ukraine Formal Ceasefire by End 2026",
        "question": "Will Russia and Ukraine reach a formal ceasefire agreement by December 31, 2026?",
        "resolution": "Formal ceasefire agreement signed by both parties (not just temporary truces like Easter ceasefire).",
        "topicType": "conflict",
        "hypotheses": {
            "H1": {"label": "Comprehensive peace deal (territorial settlement)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Frozen conflict — de facto ceasefire, no formal agreement", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Limited truces only (Easter-style, no lasting agreement)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "Escalation (new offensive or external actor entry)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~8 months",
    },
    {
        "slug": "calibration-scs-kinetic",
        "title": "South China Sea Kinetic Incident by End 2026",
        "question": "Will a kinetic military incident with casualties occur between PRC forces and any claimant state in the SCS by December 31, 2026?",
        "resolution": "Confirmed shots fired, ramming with casualties, or boarding of military vessel resulting in injury/death.",
        "topicType": "conflict",
        "hypotheses": {
            "H1": {"label": "Continued gray zone only — no casualties", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Incident with injuries but no deaths", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Lethal incident (deaths)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "MDT Article IV invocation by Philippines", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~8 months",
    },
    {
        "slug": "calibration-section122-tariffs",
        "title": "Section 122 Tariffs at 150-Day Expiry (July 2026)",
        "question": "What happens to the Section 122 10% universal tariff at its 150-day statutory limit in late July 2026?",
        "resolution": "Observable: tariff expires, renewed, expanded, or replaced. White House action or inaction by late July 2026.",
        "topicType": "economics",
        "hypotheses": {
            "H1": {"label": "Expires as scheduled — no replacement", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Renewed at same 10% rate", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Expanded — higher rate or broader scope", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "Replaced by bilateral deals (partial rollback)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~3.5 months (late July 2026)",
    },
    {
        "slug": "calibration-fed-rate-2026",
        "title": "Fed Funds Rate by End 2026",
        "question": "Where will the Federal Reserve target rate be on December 31, 2026?",
        "resolution": "FOMC official target range as of final 2026 meeting.",
        "topicType": "economics",
        "hypotheses": {
            "H1": {"label": "No further cuts — stays 3.50-3.75%", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "One cut — 3.25-3.50%", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Two+ cuts — 3.00-3.25% or below", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "Rate hike — inflation forces reversal", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~8 months",
    },
    {
        "slug": "calibration-midterms-2026",
        "title": "US Midterms 2026 — House Control",
        "question": "Which party will control the US House of Representatives after the November 2026 midterm elections?",
        "resolution": "AP calls House majority. 218+ seats for either party.",
        "topicType": "election",
        "hypotheses": {
            "H1": {"label": "Democrats flip House (>218 seats)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Democrats gain seats but fall short", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Republicans hold (status quo +/-5 seats)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "Republicans gain seats", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~7 months (Nov 2026)",
    },
    {
        "slug": "calibration-houthi-posture",
        "title": "Houthi Red Sea Posture by Mid-2026",
        "question": "What will Houthi military posture toward Red Sea commercial shipping look like by July 2026?",
        "resolution": "Observable: attack frequency, MARAD advisory status, shipping insurance rates, transit volumes.",
        "topicType": "conflict",
        "hypotheses": {
            "H1": {"label": "Attacks resume at pre-ceasefire intensity", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Selective targeting — political screening, not indiscriminate", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "De facto ceasefire holds — no commercial attacks", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "Escalation beyond Red Sea — Bab al-Mandeb full closure", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "~3 months",
    },
    {
        "slug": "calibration-rtsc-replication",
        "title": "Next RTSC Claim Survives Replication",
        "question": "Will any room-temperature superconductor claim published after 2024 be independently replicated by >=2 labs within 12 months?",
        "resolution": "Two independent labs publish replication of zero-resistance + diamagnetic levitation at RT and ambient pressure.",
        "topicType": "science",
        "hypotheses": {
            "H1": {"label": "Claim published and replicated (>=2 labs, 12 months)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H2": {"label": "Partial replication (1 lab, or only some properties)", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H3": {"label": "Claim published, fails replication", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
            "H4": {"label": "No credible claim published in window", "midpoint": 0, "unit": "weeks", "posterior": 0.25},
        },
        "horizon": "Rolling (12-month window from any new claim)",
    },
]

template_base = {
    "tagConfig": {
        "availableTags": ["EVENT", "DATA", "RHETORIC", "INTEL", "ANALYSIS"],
        "directionHints": {},
        "escalationTags": [],
        "deescalationTags": [],
    },
    "subModels": {},
    "indicators": {
        "_note": "STUB — indicators must be defined prospectively before first update",
        "tiers": {
            "tier1_critical": [],
            "tier2_strong": [],
            "tier3_suggestive": [],
            "anti_indicators": [],
        },
    },
    "actorModel": {
        "description": "STUB — actor model must be defined before first update",
        "actors": {},
        "methodology": [
            "ACTIONS OVER RHETORIC",
            "TAG EVERYTHING",
            "DON'T FRONT-RUN",
        ],
    },
    "evidenceLog": [],
    "dataFeeds": {},
    "watchpoints": [],
    "searchQueries": [],
    "governance": None,
    "predictionScoring": {"snapshots": [], "outcomes": [], "brierScores": []},
}

for s in stubs:
    topic = {}
    topic["_protocol"] = (
        "Calibration stub — prospective topic. "
        "Must be fully populated before first posterior update."
    )
    topic["meta"] = {
        "slug": s["slug"],
        "title": s["title"],
        "question": s["question"],
        "resolution": s["resolution"],
        "created": "2026-04-11T00:00:00Z",
        "lastUpdated": "2026-04-11T00:00:00Z",
        "status": "STUB",
        "dayCount": 0,
        "startDate": "",
        "classification": "ROUTINE",
        "topicType": s["topicType"],
        "horizon": s["horizon"],
    }
    topic["model"] = {
        "hypotheses": s["hypotheses"],
        "expectedValue": 0,
        "expectedUnit": "weeks",
        "posteriorHistory": [],
    }
    topic.update(template_base)

    fname = f"topics/{s['slug']}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(topic, f, indent=2)
    print(f"Created {fname}")

print(f"\nTotal: {len(stubs)} stubs created")
