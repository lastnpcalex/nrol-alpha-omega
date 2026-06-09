#!/usr/bin/env python
"""Parse matcher output and apply decisions through engine."""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import load_topic
from framework.news_observation_pipeline import parse_matcher_output, apply_decisions

# Full article dicts matching A1-A20
articles = [
    {
        "title": "Three US Navy destroyers come under Iranian missile and drone fire in Hormuz",
        "url": "https://www.cnbc.com/2026/05/07/iran-war-hormuz-strait-ceasefire-trump.html",
        "source": "CNBC + CENTCOM statement",
        "date": "2026-05-07",
        "text": "CENTCOM confirmed three US destroyers (Truxtun DDG-103, Rafael Peralta DDG-115, Mason DDG-87) came under coordinated Iranian missile, drone and small-boat attack while transiting Strait of Hormuz. US intercepted all threats, no US assets struck. US responded with self-defense strikes on Iranian missile/drone launch sites, C2 facilities, and ISR nodes. Iran claimed significant damage to US vessels; CENTCOM denied any hits.",
    },
    {
        "title": "US and Iran trade fire in Strait of Hormuz",
        "url": "https://www.reuters.com/world/asia-pacific/trump-says-us-help-ships-stranded-strait-hormuz-tanker-hit-by-projectiles-2026-05-04/",
        "source": "Reuters",
        "date": "2026-05-04",
        "text": "US and Iran launched new attacks in the Gulf as they wrestled for control over Strait of Hormuz with duelling maritime blockades. Project Freedom operations: US destroyed six Iranian small boats targeting civilian ships, intercepted cruise missiles and drones. Two US merchant ships safely transited.",
    },
    {
        "title": "CMA CGM containership San Antonio struck in Hormuz, 8 crew wounded",
        "url": "https://gcaptain.com/u-s-confirms-iranian-attack-on-u-s-navy-destroyers-in-strait-of-hormuz/",
        "source": "GCaptain + IMO",
        "date": "2026-05-07",
        "text": "French shipping giant CMA CGM confirmed its Malta-flagged containership San Antonio was struck while transiting the Strait of Hormuz, injuring multiple crew members. IMO reported 8 seafarers wounded, marking the 32nd reported shipping incident since conflict began.",
    },
    {
        "title": "US destroys six Iranian boats while opening Hormuz to trade",
        "url": "https://apnews.com/article/iran-us-war-ceasefire-negotiations-strait-a4857f28d9b47e0170b65ced19451a25",
        "source": "Associated Press",
        "date": "2026-05-05",
        "text": "US military launched Project Freedom to force a lane through the strait. US sank six Iranian small boats targeting civilian ships; two US-flagged merchant ships successfully transited. UAE was attacked by Iran for first time since ceasefire.",
    },
    {
        "title": "Two tankers slip through Hormuz dark with transponders off",
        "url": "https://investinglive.com/commodities/two-tankers-slip-through-hormuz-dark-as-gulf-oil-crisis-grinds-on-yeah-two-yippee-20260510/",
        "source": "Reuters + Kpler data",
        "date": "2026-05-10",
        "text": "Two VLCCs carrying combined 4 million barrels of Gulf crude exited Strait of Hormuz with transponders switched off to avoid Iranian attack.",
    },
    {
        "title": "Qatari LNG tanker Al Kharaitiyat crosses Hormuz, first transit since war",
        "url": "https://www.france24.com/en/middle-east/20260511-middle-east-war-live-uk-and-france-to-host-defence-talks-on-hormuz-shipping-mission",
        "source": "France 24 (Reuters/LSEG)",
        "date": "2026-05-11",
        "text": "Qatari LNG tanker Al Kharaitiyat crossed Strait of Hormuz for first time since war began, reportedly approved by Iran to build confidence with Qatar and Pakistan.",
    },
    {
        "title": "Iran redefines Strait of Hormuz to far larger zone from Jask to Siri Island",
        "url": "https://www.reuters.com/world/middle-east/iran-now-defines-strait-hormuz-far-larger-zone-irgc-officer-says-2026-05-12/",
        "source": "Reuters",
        "date": "2026-05-12",
        "text": "Senior IRGC officer stated Iran redefined Strait of Hormuz from narrow area to strategic zone stretching from Jask to Siri Island. Iranian lawmakers drafting legislation to formalize management with clauses forbidding passage to hostile-state vessels.",
    },
    {
        "title": "UK and France convene 40-nation defense talks for Hormuz security mission",
        "url": "https://www.rfi.fr/en/international/20260512-france-and-uk-convene-40-nation-hormuz-talks-as-iran-stand-off-continues",
        "source": "RFI",
        "date": "2026-05-12",
        "text": "UK and France co-hosted virtual meeting of 40+ defense ministers to plan multinational security mission for Strait of Hormuz. France deployed aircraft carrier Charles de Gaulle, UK sent HMS Dragon to pre-position.",
    },
    {
        "title": "China urges Pakistan to step up mediation on Hormuz",
        "url": "https://english.mathrubhumi.com/news/world/china-pakistan-iran-us-strait-of-hormuz-mediation-m74a3a96",
        "source": "Chinese state media / AFP",
        "date": "2026-05-12",
        "text": "Chinese FM Wang Yi called Pakistani Deputy PM Ishaq Dar, urging Pakistan to intensify mediation between Iran and US for Hormuz stability. Iran FM Araghchi told Wang Yi in Beijing that Hormuz opening can be resolved as soon as possible.",
    },
    {
        "title": "Trump rejects Iran peace proposal, ceasefire on life support",
        "url": "https://apnews.com/article/iran-us-war-attack-may-10-2026-f8812db41837336d816efaea7bc1c44a",
        "source": "AP News + Guardian",
        "date": "2026-05-11",
        "text": "Trump rejected Iran counterproposal calling it totally unacceptable. Iran demanded war reparations, full sovereignty over Hormuz, end of sanctions, release of seized assets.",
    },
    {
        "title": "HMM NAMU South Korean vessel struck in Hormuz",
        "url": "https://www.koreatimes.co.kr/world/20260511/confirmed-vessel-strike-may-shift-seoul-stance-on-us-led-hormuz-mission-experts",
        "source": "Korea Times",
        "date": "2026-05-11",
        "text": "Two unidentified objects struck the South Korean-operated vessel HMM NAMU while anchored in the strait about one minute apart, causing explosion and fire.",
    },
    {
        "title": "Aramco: 100M barrel weekly loss, transit at 9 tankers vs 100+ baseline",
        "url": "https://www.bloomberg.com/news/articles/2026-05-11/aramco-sees-100-million-barrel-oil-loss-each-week-hormuz-is-shut",
        "source": "Bloomberg + Windward",
        "date": "2026-05-11",
        "text": "Windward identified only 9 commercial tanker transits through Hormuz on May 11 vs 100+ daily baseline before war. IMO estimates 2000 vessels trapped.",
    },
    {
        "title": "Maersk halts all Hormuz transits after escalating tensions",
        "url": "https://www.roic.ai/news/maersk-halts-strait-of-hormuz-transits-amid-rising-gulf-tensions-05-12-2026",
        "source": "ROIC.AI",
        "date": "2026-05-12",
        "text": "Maersk confirmed it has halted all Strait of Hormuz transit operations amid escalating tensions following US-Iran exchange of fire.",
    },
    {
        "title": "IRGC tightens grip on Hormuz, shipping under dark/EMCON conditions",
        "url": "https://www.hstoday.us/subject-matter-areas/maritime-security/iran-tightens-grip-on-strait-of-hormuz-as-shipping-forced-into-controlled-routes/",
        "source": "Defense and Security (HSToday)",
        "date": "2026-05-11",
        "text": "Commercial shipping through Hormuz operating under dark or EMCON conditions. IRGC fast craft activity expanded across both Hormuz corridors deploying swarm-style formations near commercial traffic.",
    },
    {
        "title": "UK parliamentary briefing: almost no shipping, strait at 5% volume",
        "url": "https://commonslibrary.parliament.uk/research-briefings/cbp-10636/",
        "source": "UK House of Commons Library",
        "date": "2026-05-10",
        "text": "Almost no shipping has used strait and it remains effectively closed. Pre-conflict monthly volume was ~3000 vessels; current numbers stand at around 5 percent of that level.",
    },
    {
        "title": "Iran warns US blockade threatens ceasefire, Project Freedom launched",
        "url": "https://www.al-monitor.com/originals/2026/05/iran-warns-ceasefire-violation-us-plans-escort-hormuz-ships",
        "source": "Al-Monitor",
        "date": "2026-05-05",
        "text": "Iran warned it would consider any US attempt to interfere in Hormuz a breach of Mideast ceasefire.",
    },
    {
        "title": "US strikes two Iranian-flagged ships amid ceasefire tensions",
        "url": "https://www.washingtonpost.com/national-security/2026/05/08/us-iran-ceasefire-hormuz-attacks/",
        "source": "Washington Post",
        "date": "2026-05-08",
        "text": "US struck two Iranian-flagged ships as tensions rose amid the ceasefire.",
    },
    {
        "title": "US disables Iranian tanker Hasna violating blockade with cannon fire",
        "url": "https://gcaptain.com/u-s-navy-jet-fires-upon-disables-iranian-tanker-accused-of-violating-washingtons-blockade/",
        "source": "GCaptain + CENTCOM",
        "date": "2026-05-07",
        "text": "US Navy F/A-18 Super Hornet fired 20mm cannon rounds at Iranian-flagged tanker M/T Hasna for attempting to transit toward Iranian port in violation of blockade.",
    },
    {
        "title": "Drone attacks on UAE, Qatar, Kuwait over weekend during ceasefire",
        "url": "https://apnews.com/article/iran-us-war-attack-may-10-2026-f8812db41837336d816efaea7bc1c44a",
        "source": "AP News",
        "date": "2026-05-10",
        "text": "Iranian drone attacks targeted Gulf Arab nations over the weekend. UAE shot down two drones, Qatar reported drone ignited fire on ship off its coast, Kuwait airspace entered by drones.",
    },
    {
        "title": "Somali piracy resurgence exploits naval diversion from Hormuz",
        "url": "https://www.dw.com/en/somalia-piracy-global-shipping-trade-routes-strait-of-hormuz-indian-ocean/a-77047750",
        "source": "Deutsche Welle",
        "date": "2026-05-11",
        "text": "Three ships hijacked in three weeks as piracy resurges off Somalia, exploiting stretched international naval patrols diverted by Hormuz and Red Sea crises.",
    },
]

# The matcher output
matcher_output = r"""
DECISION
ARTICLE: A1
ACTION: OBSERVE t1_kinetic_escalation_event AT 1
TAG: EVENT
CLAIM: Iranian forces conducted a coordinated missile, drone, and small-boat attack on three US Navy destroyers in the Strait of Hormuz on 2026-05-07, per CENTCOM confirmation.
REASON: One kinetic confrontation event in 30-day window; metric counts naval confrontation events with direction=higher_strengthens, directionally consistent.
END

DECISION
ARTICLE: A2
ACTION: FIRE t2_named_recovery_action_initiated
TAG: EVENT
CLAIM: The US launched Project Freedom operations destroying six Iranian small boats targeting civilian ships and enabling two merchant ship transits through Hormuz.
REASON: Literal threshold met - named US-led military operation specifically aimed at reopening Hormuz transit.
END

DECISION
ARTICLE: A3
ACTION: OBSERVE t1_kinetic_escalation_event AT 1
TAG: EVENT
CLAIM: The CMA CGM containership San Antonio was struck while transiting the Strait of Hormuz, wounding 8 crew members, per IMO reporting.
REASON: One commercial vessel struck in Hormuz counts toward the 30-day kinetic event metric.
END

DECISION
ARTICLE: A4
ACTION: PARK
TAG: EVENT
CLAIM: US military sank six Iranian boats targeting civilian ships under Project Freedom while UAE was attacked by Iranian drones/missiles.
REASON: Overlapping coverage of A2. Park to avoid double-counting.
END

DECISION
ARTICLE: A5
ACTION: PARK
TAG: DATA
CLAIM: Two VLCCs exited Hormuz with transponders off carrying 4M barrels of Gulf crude to avoid Iranian attack.
REASON: Covert transponder-off activity does not cleanly map to recovery or restriction metrics.
END

DECISION
ARTICLE: A6
ACTION: SCHEMA_GAP Government-to-government confidence-building transit approval (Iran authorizing Qatari LNG passage via northern route) supports de-escalation/faster reopen but no indicator captures bilateral facilitation or government-approved selective transits
TAG: POLICY
CLAIM: Qatari LNG tanker Al Kharaitiyat crossed Hormuz for first time since war began after Iran approved passage via Iranian-controlled northern route to build confidence with Qatar and Pakistan.
REASON: De-escalation signal but no indicator captures government-to-government facilitation or selective transit approval.
END

DECISION
ARTICLE: A7
ACTION: FIRE t2_preparatory_kinetic_or_blockade
TAG: POLICY
CLAIM: Senior IRGC officer stated Iran redefined the Strait of Hormuz to a far larger zone from Jask to Siri Island, and lawmakers are drafting legislation to formalize management with clauses forbidding hostile-state vessel passage.
REASON: Literal threshold met - expansion of declared/de-facto control zones explicitly listed in indicator desc.
END

DECISION
ARTICLE: A8
ACTION: FIRE t2_named_recovery_action_initiated
TAG: EVENT
CLAIM: UK and France co-hosted a virtual meeting of 40+ defense ministers to plan a multinational security mission for Strait of Hormuz, with France deploying Charles de Gaulle and UK sending HMS Dragon to pre-position.
REASON: Literal threshold met - named multilateral coalition announcement for restoring shipping in Hormuz.
END

DECISION
ARTICLE: A9
ACTION: OBSERVE t2_third_party_mediation_active AT 1
TAG: EVENT
CLAIM: Chinese FM Wang Yi urged Pakistan to intensify Iran-US mediation on Hormuz ahead of Trump-Xi summit, and Iran FM Araghchi told Wang Yi in Beijing that Hormuz opening can be resolved as soon as possible.
REASON: One named mediation effort by China with Iran FM making an on-record concession statement.
END

DECISION
ARTICLE: A10
ACTION: IGNORE
TAG: RHETORIC
CLAIM: Trump rejected Iran's peace counterproposal, describing the ceasefire as on life support with a 1% chance of survival, while Iran demanded reparations, sovereignty over Hormuz, and sanctions removal.
REASON: Pure rhetoric - political statements, forecasts, and negotiation posture. No concrete observable event.
END

DECISION
ARTICLE: A11
ACTION: OBSERVE t1_kinetic_escalation_event AT 1
TAG: EVENT
CLAIM: The South Korean-operated vessel HMM NAMU was struck by two unidentified objects while anchored in the Strait of Hormuz, causing explosion and fire.
REASON: One commercial vessel struck in the strait; counts toward kinetic events 30-day metric.
END

DECISION
ARTICLE: A12
ACTION: OBSERVE t1_transit_below_25pct_3mo AT 9
TAG: DATA
CLAIM: Windward identified only 9 commercial tanker transits through Hormuz on May 11 versus 100+ daily baseline before war.
REASON: Transit at ~9% of baseline extracted from stated ratio.
END

DECISION
ARTICLE: A13
ACTION: PARK
TAG: EVENT
CLAIM: Maersk confirmed it has halted all Strait of Hormuz transit operations amid escalating tensions following US-Iran exchange of fire.
REASON: Topic-relevant operational withdrawal but no indicator captures shipping company risk decisions.
END

DECISION
ARTICLE: A14
ACTION: FIRE t2_preparatory_kinetic_or_blockade
TAG: EVENT
CLAIM: IRGC fast craft activity expanded across both Hormuz corridors using swarm-style formations and escort-like behavior near commercial traffic, tightening operational control over the strait.
REASON: Literal threshold met - IRGC fast-boat sortie surge and de-facto blockade reinforcement explicitly listed in indicator desc.
END

DECISION
ARTICLE: A15
ACTION: OBSERVE t1_transit_below_25pct_3mo AT 5
TAG: DATA
CLAIM: UK House of Commons Library reports current Hormuz transit volume at approximately 5% of the pre-conflict monthly baseline.
REASON: Transit at 5% of baseline directly stated.
END

DECISION
ARTICLE: A16
ACTION: PARK
TAG: RHETORIC
CLAIM: Iran warned it would consider any US attempt to interfere in Hormuz a breach of the ceasefire.
REASON: Rhetorical posture; overlaps with A2.
END

DECISION
ARTICLE: A17
ACTION: OBSERVE t1_kinetic_escalation_event AT 1
TAG: EVENT
CLAIM: US struck two Iranian-flagged ships as tensions rose amid the ceasefire.
REASON: One kinetic engagement event; counts toward kinetic events 30-day metric.
END

DECISION
ARTICLE: A18
ACTION: OBSERVE t1_kinetic_escalation_event AT 1
TAG: EVENT
CLAIM: US Navy F/A-18 Super Hornet fired 20mm cannon rounds at and disabled the Iranian-flagged tanker M/T Hasna for violating the US naval blockade.
REASON: One kinetic event; counts toward kinetic events 30-day metric.
END

DECISION
ARTICLE: A19
ACTION: FIRE t2_preparatory_kinetic_or_blockade
TAG: EVENT
CLAIM: Iranian drone attacks targeted UAE, Qatar, and Kuwait over the weekend during the ceasefire period.
REASON: Literal threshold met - Iran cross-border strikes on Gulf states straining the ceasefire.
END

DECISION
ARTICLE: A20
ACTION: IGNORE
TAG: RHETORIC
CLAIM: Somali piracy has resurged with three ships hijacked in three weeks, exploiting naval patrols diverted by Hormuz and Red Sea crises.
REASON: Secondary downstream consequence rather than evidence about Hormuz reopening timeline itself.
END
"""

decisions = parse_matcher_output(matcher_output)
print(f"Parsed {len(decisions)} decisions:")
action_counts = {}
for d in decisions:
    action = d.get('kind', 'UNKNOWN')
    action_counts[action] = action_counts.get(action, 0) + 1
print(f"  Counts: {action_counts}")

slug = 'calibration-hormuz-reopen-2027'
summary = apply_decisions(slug, articles, decisions)
print(f"\nApply complete: {summary}")

# Show final posteriors
topic = load_topic(slug)
print(f"\n=== FINAL POSTERIORS ===")
for k, v in topic['model']['hypotheses'].items():
    print(f"  {k}: {v['posterior']:.4f}")

# Governance
from governor import governance_report
gov = governance_report(topic)
print(f"\n=== GOVERNANCE ===")
print(f"  Health: {gov['health']}")
print(f"  R_t regime: {gov['rt']['regime']} (value: {gov['rt']['rt']:.4f})")
print(f"  Entropy: {gov['entropy']:.4f}")
print(f"  Issues: {len(gov['issues'])}")
for issue in gov['issues'][:5]:
    print(f"    - {issue}")
print(f"  Total evidenceLog: {len(topic['evidenceLog'])}")
print(f"  Flagged for review: {len(topic.get('governance', {}).get('flagged_for_indicator_review', []))}")
