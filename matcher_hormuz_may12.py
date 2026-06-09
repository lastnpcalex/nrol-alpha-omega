#!/usr/bin/env python
"""Matcher subagent dispatcher for calibration-hormuz-reopen-2027 — May 12, 2026"""
import sys, json
sys.path.insert(0, '/c/Claude-Code/NROL-AO/temp-repo')

from engine import load_topic

topic = load_topic('calibration-hormuz-reopen-2027')

# Compile all novel articles from subagent results + direct research
# Format: list[dict] with keys: title, url, source, date, text
articles = [
    {
        "title": "Three US Navy destroyers come under Iranian missile and drone fire in Hormuz",
        "url": "https://www.cnbc.com/2026/05/07/iran-war-hormuz-strait-ceasefire-trump.html",
        "source": "CNBC + CENTCOM statement",
        "date": "2026-05-07",
        "text": "CENTCOM confirmed three US destroyers (Truxtun DDG-103, Rafael Peralta DDG-115, Mason DDG-87) came under coordinated Iranian missile, drone and small-boat attack while transiting Strait of Hormuz. US intercepted all threats, no US assets struck. US responded with self-defense strikes on Iranian missile/drone launch sites, C2 facilities, and ISR nodes. Iran claimed significant damage to US vessels; CENTCOM denied any hits. Trump called it a love tap. Iranian forces launched multiple missiles, drones and small boats. CENTCOM eliminated inbound threats and targeted Iranian military facilities."
    },
    {
        "title": "US and Iran trade fire in Strait of Hormuz; each claims the other initiated",
        "url": "https://www.reuters.com/world/asia-pacific/trump-says-us-help-ships-stranded-strait-hormuz-tanker-hit-by-projectiles-2026-05-04/",
        "source": "Reuters",
        "date": "2026-05-04",
        "text": "US and Iran launched new attacks in the Gulf as they wrestled for control over Strait of Hormuz with duelling maritime blockades. Project Freedom operations: US destroyed six Iranian small boats targeting civilian ships, intercepted cruise missiles and drones. Two US merchant ships safely transited while Iranian forces attempted to challenge."
    },
    {
        "title": "CMA CGM containership San Antonio struck in Hormuz, 8 crew wounded",
        "url": "https://gcaptain.com/u-s-confirms-iranian-attack-on-u-s-navy-destroyers-in-strait-of-hormuz/",
        "source": "GCaptain + IMO",
        "date": "2026-05-07",
        "text": "French shipping giant CMA CGM confirmed its Malta-flagged containership San Antonio was struck while transiting the Strait of Hormuz, injuring multiple crew members. IMO reported 8 seafarers wounded, marking the 32nd reported shipping incident since conflict began. Another CMA CGM vessel (Saigon) successfully exited Gulf via Oman coastline route south of Muscat. BIMCO warned Project Freedom pause complicates risk calculus."
    },
    {
        "title": "US destroys six Iranian boats while opening Hormuz to trade",
        "url": "https://apnews.com/article/iran-us-war-ceasefire-negotiations-strait-a4857f28d9b47e0170b65ced19451a25",
        "source": "Associated Press",
        "date": "2026-05-05",
        "text": "US military launched Project Freedom to force a lane through the strait. US sank six Iranian small boats targeting civilian ships; two US-flagged merchant ships successfully transited. UAE was attacked by Iran for first time since ceasefire — 15 missiles and 4 drones, one drone hit oil facility in Fujairah wounding three. Iran condemned US effort as military adventurism and ceasefire violation. Iran's military warned any foreign military force approaching strait will be targeted."
    },
    {
        "title": "Two tankers slip through Hormuz dark with transponders off",
        "url": "https://investinglive.com/commodities/two-tankers-slip-through-hormuz-dark-as-gulf-oil-crisis-grinds-on-yeah-two-yippee-20260510/",
        "source": "Reuters + Kpler data",
        "date": "2026-05-10",
        "text": "Two VLCCs carrying combined 4 million barrels of Gulf crude exited Strait of Hormuz last week with transponders switched off to avoid Iranian attack. Basrah Energy loaded 2M barrels Upper Zakum crude at ADNOC Zirku terminal May 1, exited May 6, offloaded at Fujairah May 8. Second VLCC Kiara M carrying 2M barrels Iraqi crude also transited with transponder off. Under ordinary conditions strait handles ~20M barrels per day. Transponder-off tactic inherently limited in scale, impossible to conduct at volume needed to offset broader disruption."
    },
    {
        "title": "Qatari LNG tanker Al Kharaitiyat crosses Hormuz, first transit since war",
        "url": "https://www.france24.com/en/middle-east/20260511-middle-east-war-live-uk-and-france-to-host-defence-talks-on-hormuz-shipping-mission",
        "source": "France 24 (Reuters/LSEG)",
        "date": "2026-05-11",
        "text": "Qatari LNG tanker Al Kharaitiyat crossed Strait of Hormuz for first time since war began, reportedly approved by Iran to build confidence with Qatar and Pakistan. Second Qatari LNG tanker (Mihzem, 174K cubic meters) also transiting toward Pakistan. Shipments part of government-to-government deal between Qatar and Pakistan, with Iran approving passage via Iranian-controlled northern route. First structured mechanism for cargo to pass through Iranian-controlled waters since war began."
    },
    {
        "title": "Iran redefines Strait of Hormuz to far larger zone from Jask to Siri Island",
        "url": "https://www.reuters.com/world/middle-east/iran-now-defines-strait-hormuz-far-larger-zone-irgc-officer-says-2026-05-12/",
        "source": "Reuters",
        "date": "2026-05-12",
        "text": "Senior IRGC officer Akbarzadeh stated Iran redefined Strait of Hormuz from narrow area around Hormuz and Hengam islands to strategic zone stretching from Jask in the east to Siri Island in the west. Expansion significantly increases territory Iran claims authority over, encompassing far larger portion of northern Gulf of Oman. Iranian lawmakers drafting legislation to formalize management of Strait with clauses forbidding passage to hostile-state vessels. Parliament claims transit fees would generate 15B dollar annually."
    },
    {
        "title": "UK and France convene 40-nation defense talks for Hormuz security mission",
        "url": "https://www.rfi.fr/en/international/20260512-france-and-uk-convene-40-nation-hormuz-talks-as-iran-stand-off-continues",
        "source": "RFI",
        "date": "2026-05-12",
        "text": "UK and France co-hosted virtual meeting of 40+ defense ministers to plan multinational security mission for Strait of Hormuz. France deployed aircraft carrier Charles de Gaulle, UK sent HMS Dragon to pre-position in region. UK Defence Secretary Healey stated goal is turning diplomatic agreement into practical military plans to restore shipping confidence. Macron stressed mission would be coordinated with Iran and rejected vessel tolls. Iran deputy FM Gharibabadi warned that foreign warship deployment would meet decisive and immediate response."
    },
    {
        "title": "China urges Pakistan to step up mediation on Hormuz ahead of Trump-Xi summit",
        "url": "https://english.mathrubhumi.com/news/world/china-pakistan-iran-us-strait-of-hormuz-mediation-m74a3a96",
        "source": "Chinese state media / AFP",
        "date": "2026-05-12",
        "text": "Chinese FM Wang Yi called Pakistani Deputy PM Ishaq Dar, urging Pakistan to intensify mediation between Iran and US and help address maritime stability in Hormuz. Wang praised Pakistan's role in extending ceasefire. Both sides acknowledged upcoming 75th anniversary of diplomatic ties. Call timed ahead of Trump-Xi summit May 14-15 in Beijing. Iran FM Araghchi met Wang Yi in Beijing May 6, stating Hormuz opening can be resolved as soon as possible."
    },
    {
        "title": "Trump rejects Iran peace proposal, ceasefire on life support, oil jumps to $105",
        "url": "https://apnews.com/article/iran-us-war-attack-may-10-2026-f8812db41837336d816efaea7bc1c44a",
        "source": "AP News + Guardian",
        "date": "2026-05-11",
        "text": "Trump rejected Iran counterproposal calling it totally unacceptable piece of garbage. Iran demanded war reparations, full sovereignty over Hormuz, end of sanctions, release of seized assets. Trump described ceasefire on massive life support with 1 percent chance of living. Oil prices jumped to over 105 dollar per barrel. Iran Maj Gen Jafari stated no further negotiations until war ends on all fronts, sanctions lifted, funds released. Iran deputy FM warned against French-British maritime mission threatening decisive response."
    },
    {
        "title": "HMM NAMU South Korean vessel struck in Hormuz, Seoul may join US mission",
        "url": "https://www.koreatimes.co.kr/world/20260511/confirmed-vessel-strike-may-shift-seoul-stance-on-us-led-hormuz-mission-experts",
        "source": "Korea Times",
        "date": "2026-05-11",
        "text": "South Korea announced initial investigation findings that two unidentified objects struck the South Korean-operated vessel HMM NAMU while anchored in strait about one minute apart, causing explosion and fire. Investigation ongoing to determine responsibility. Confirmed strike expected to give Seoul grounds to reconsider joining US-led Hormuz security missions."
    },
    {
        "title": "Aramco: 100M barrel weekly loss, transit at 9 tankers vs 100+ baseline",
        "url": "https://www.bloomberg.com/news/articles/2026-05-11/aramco-sees-100-million-barrel-oil-loss-each-week-hormuz-is-shut",
        "source": "Bloomberg + Windward",
        "date": "2026-05-11",
        "text": "Saudi Aramco CEO Amin Nasser stated global oil markets losing 100 million barrels every week Hormuz remains shut, calling it largest energy supply shock ever. Warned normalization would not occur until 2027 if disruption continues. Windward identified only 9 commercial tanker transits through Hormuz on May 11 vs 100+ daily baseline before war. IMO estimates 2000 vessels trapped near Hormuz, 1500 tankers and 20000 seafarers stranded. UN task force warns 45 million people at risk of hunger."
    },
    {
        "title": "Maersk halts all Hormuz transits after escalating tensions",
        "url": "https://www.roic.ai/news/maersk-halts-strait-of-hormuz-transits-amid-rising-gulf-tensions-05-12-2026",
        "source": "ROIC.AI",
        "date": "2026-05-12",
        "text": "Maersk confirmed it has halted all Strait of Hormuz transit operations amid escalating tensions following US-Iran exchange of fire. Follows earlier successful transit on May 4 where US-flagged Alliance Fairfax exited under Project Freedom escort. Major carriers unwilling to commit to regular transits given unresolved security environment and threat of Iranian attacks on non-compliant vessels."
    },
    {
        "title": "IRGC tightens grip on Hormuz, shipping under dark/EMCON conditions",
        "url": "https://www.hstoday.us/subject-matter-areas/maritime-security/iran-tightens-grip-on-strait-of-hormuz-as-shipping-forced-into-controlled-routes/",
        "source": "Defense and Security (HSToday)",
        "date": "2026-05-11",
        "text": "Commercial shipping through Hormuz operating under dark or EMCON conditions. IRGC fast craft activity expanded across both Hormuz corridors deploying swarm-style formations and escort-like behavior near commercial traffic. Windward identified only nine commercial tanker transits through Hormuz on May 11. Dark fleet-linked LPG vessels among few transiting. IRGC tightening operational control over strait rather than loosening."
    },
    {
        "title": "UK parliamentary briefing: almost no shipping, strait effectively closed at 5% volume",
        "url": "https://commonslibrary.parliament.uk/research-briefings/cbp-10636/",
        "source": "UK House of Commons Library",
        "date": "2026-05-10",
        "text": "While conditional ceasefire in place and extended until talks conclude, almost no shipping has used strait and it remains effectively closed. Pre-conflict monthly volume was ~3000 vessels; current numbers stand at around 5 percent of that level. At least 17 merchant ships damaged (7 abandoned), 2 merchant ships captured, 12 seafarers killed or missing. UK and France announced international defensive mission on April 17 to be established once sustainable ceasefire agreed. China and Russia vetoed UNSC resolution April 7."
    },
    {
        "title": "Iran warns US blockade threatens ceasefire, strikes two Iranian tankers",
        "url": "https://www.al-monitor.com/originals/2026/05/iran-warns-ceasefire-violation-us-plans-escort-hormuz-ships",
        "source": "Al-Monitor",
        "date": "2026-05-05",
        "text": "Iran warned it would consider any US attempt to interfere in Hormuz a breach of Mideast ceasefire. Trump announced Project Freedom as humanitarian gesture for crews on ships swept up in blockade. Iran head of parliament national security commission stated any American interference in new maritime regime of Hormuz will be considered ceasefire violation. CENTCOM using guided-missile destroyers, 100+ aircraft, unmanned platforms, 15000 service members in Hormuz effort. More than 900 commercial vessels in Gulf per AXSMarine."
    },
    {
        "title": "US strikes two Iranian-flagged ships amid ceasefire tensions, oil at $100+",
        "url": "https://www.washingtonpost.com/national-security/2026/05/08/us-iran-ceasefire-hormuz-attacks/",
        "source": "Washington Post",
        "date": "2026-05-08",
        "text": "US struck two Iranian-flagged ships as tensions rose amid ceasefire. Trump administration sidestepped widening battle around Hormuz, anticipating reply from Tehran on latest terms for ending war. Iran denounced clashes as crude pressure tactic."
    },
    {
        "title": "US disables Iranian tanker Hasna violating blockade with cannon fire",
        "url": "https://gcaptain.com/u-s-navy-jet-fires-upon-disables-iranian-tanker-accused-of-violating-washingtons-blockade/",
        "source": "GCaptain + CENTCOM",
        "date": "2026-05-07",
        "text": "US Navy F/A-18 Super Hornet from USS Abraham Lincoln fired 20mm cannon rounds at Iranian-flagged tanker M/T Hasna after vessel allegedly ignored repeated warnings while attempting to transit toward Iranian port in violation of blockade. Incident followed similar April interdiction of M/V Touska. Broader US naval blockade targeting Iranian maritime trade remains fully active despite Project Freedom pause."
    },
    {
        "title": "Drone attacks on UAE, Qatar, Kuwait over weekend during ceasefire",
        "url": "https://apnews.com/article/iran-us-war-attack-may-10-2026-f8812db41837336d816efaea7bc1c44a",
        "source": "AP News",
        "date": "2026-05-10",
        "text": "Drone attacks targeted Gulf Arab nations over the weekend. UAE shot down two drones, Qatar reported drone ignited fire on ship off its coast, Kuwait airspace entered by drones. Iran military declared full readiness to protect nuclear sites where 440+ kg of 60-percent-enriched uranium stored."
    },
    {
        "title": "Somali piracy resurgence exploits naval diversion from Hormuz/Red Sea",
        "url": "https://www.dw.com/en/somalia-piracy-global-shipping-trade-routes-strait-of-hormuz-indian-ocean/a-77047750",
        "source": "Deutsche Welle",
        "date": "2026-05-11",
        "text": "Three ships hijacked in three weeks as piracy resurges off Somalia, exploiting stretched international naval patrols diverted by Hormuz and Red Sea crises. Honour 25, Eureka oil tankers, and cargo ship Sward remain under pirate control. Experts at Institute for Security Studies and Danish Institute for Strategy and War Studies report organized crime groups well-resourced using large dhows as mother ships. Adds ~1M dollar per voyage fuel cost for Africa-rerouting ships already displaced by Hormuz closure."
    },
]

# Build the matcher prompt
from framework.news_observation_pipeline import build_matcher_prompt
prompt = build_matcher_prompt(topic, articles)

with open('/c/Claude-Code/NROL-AO/temp-repo/matcher_prompt_hormuz_may12.txt', 'w', encoding='utf-8') as f:
    f.write(prompt)

print(f"Matcher prompt built, {len(articles)} articles, {len(prompt)} chars")
# Show indicator summary for context
indicators = []
for tier_key, tier_list in topic.get('indicators', {}).get('tiers', {}).items():
    for ind in tier_list:
        indicators.append(f"  {ind['id']} ({ind.get('status', '?')}): {ind.get('desc', '')[:80]}")
for ind in topic.get('indicators', {}).get('anti_indicators', []):
    indicators.append(f"  {ind['id']} ({ind.get('status', '?')}): {ind.get('desc', '')[:80]}")
print(f"\nIndicators ({len(indicators)} total):")
for ind in indicators:
    print(ind)
