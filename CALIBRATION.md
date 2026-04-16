# Calibration Plan

> This is the calibration plan appendix to the main [README](README.md). It covers the empirical validation strategy for operator calibration, including proposed topics and timeline.

## Calibration Roadmap

### The problem

The engine's Bayesian mechanics are implemented and tested. What's missing is *empirical validation that the operator is well-calibrated* — that the likelihoods fed into `bayesian_update()` produce posteriors that track reality. Without this, the system is a well-oiled machine pointed at an unknown angle.

Brier scoring infrastructure exists (`snapshot_posteriors`, `record_outcome`, `compute_brier`). The LK-99 topic has been backfilled with 6 prediction snapshots and scores (average Brier 0.069, WELL_CALIBRATED). But backfilled topics validate mechanics, not judgment — you can't un-know the answer. Real calibration requires **prospective predictions**: commit to posteriors *before* resolution, then score honestly when the answer arrives.

### What's needed

1. **Prospective predictions only.** Snapshots must be timestamped before resolution. No retroactive scoring.
2. **Non-cherry-picked topics.** If you only forecast things you're confident about, Brier scores flatter you and teach nothing. Selection criteria, not vibes.
3. **Pre-committed resolution criteria.** Each topic defines what "resolved" means and by when. Open-ended topics that never close are unfalsifiable.
4. **Minimum corpus.** N ≥ 10 resolved prospective topics before deriving empirical weight functions. N ≥ 20 before automating them.
5. **Domain diversity.** Calibration on physics alone doesn't validate geopolitical judgment. The corpus needs to span domains.

### Goal: learn claim-state weights from data

Right now, claim-state weights are hand-set: SUPPORTED = 1.0, PROPOSED = 0.5, CONTESTED = 0.2, INVALIDATED = 0.0. The long-term goal is to derive these empirically — what weight on PROPOSED evidence actually produces the best-calibrated posteriors? This requires enough resolved topics with enough prediction snapshots to fit a curve. We're not there yet, and pretending otherwise would be the kind of false precision the governor exists to catch.

### Proposed calibration topics

Ten topics designed using the framework's own criteria: multiple plausible hypotheses (not just yes/no), observable indicators and data feeds, genuine prior uncertainty, and a time horizon that actually resolves. Each topic below sketches the hypothesis space and the evidence feeds that would drive updates — the operator sets up the full topic file prospectively and commits to regular updates before resolution.

**Topic 1: 750 GeV diphoton excess** (particle physics — backfill only)
- *Hypotheses*: H1: new particle, H2: statistical fluctuation, H3: detector artifact, H4: BSM physics but not a resonance
- *Resolution*: ICHEP 2016 data release. **Already resolved** (H2 won). Backfill like LK-99 — tests mechanics, not prospective judgment.
- *Why include*: operator had documented early skepticism, updated before consensus. Second historical anchor alongside LK-99.

**Topic 2: Iran war — Strait of Hormuz status by August 2026** (geopolitics/trade)
- *Hypotheses*: H1: full unconditional reopen (<3 months), H2: conditional reopen (Iran retains inspection/fee regime), H3: remains effectively closed through August, H4: escalation closes Bab al-Mandeb too
- *Indicators*: daily transit counts, Brent/WTI spread, ceasefire compliance reports, Islamabad negotiation outcomes, Houthi posture shifts
- *Current state*: ceasefire agreed Apr 8 but Hormuz still at standstill as of Apr 10. Iran insists on transit permission regime. Genuinely uncertain — the ceasefire could collapse or calcify.
- *Horizon*: ~4 months. *Difficulty*: hard.

**Topic 3: US recession by Q4 2026** (economics)
- *Hypotheses*: H1: no recession (GDP stays positive), H2: technical recession (2 negative quarters, shallow), H3: significant recession (unemployment >5.5%), H4: stagflation (negative growth + inflation >4%)
- *Indicators*: ISM PMI (below 50 already), consumer confidence (declining 3 quarters), unemployment (4.6% and rising), GDP prints, yield curve, Sahm Rule trigger
- *Current state*: Polymarket prices ~25% recession probability. Mixed signals — labor weakening but growth forecasts still positive (1.5-2.0%). Tariff uncertainty adds volatility.
- *Horizon*: ~6 months (Q3/Q4 GDP prints). *Difficulty*: medium — the indicators disagree, which is exactly when Bayesian updating earns its keep.

**Topic 4: Ukraine — formal ceasefire by end 2026** (geopolitics)
- *Hypotheses*: H1: comprehensive peace deal (territorial settlement), H2: frozen conflict (de facto ceasefire, no agreement), H3: limited truces only (Easter-style, no lasting agreement), H4: escalation (new offensive or external actor entry)
- *Indicators*: negotiation track (Abu Dhabi/Geneva/Paris), territorial control changes, Western arms deliveries, Russian domestic pressure, energy leverage shifts
- *Current state*: 32-hour Easter ceasefire agreed Apr 10, but broader talks stalled. Territory impasse unresolved. Washington distracted by Iran. Genuine multi-hypothesis uncertainty.
- *Horizon*: ~8 months. *Difficulty*: hard.

**Topic 5: South China Sea — kinetic incident with casualties by end 2026** (geopolitics)
- *Hypotheses*: H1: continued gray zone only (no casualties), H2: incident with injuries but no deaths, H3: lethal incident (deaths), H4: MDT Article IV invocation
- *Indicators*: ADIZ incursions, warship near-misses (one on Mar 30), joint exercise frequency (500+ US-PH exercises planned), diplomatic track, China defense budget trajectory
- *Current state*: near-miss between BRP Benguet and PLA frigate Jingzhou. Escalation pattern clear, but both sides have so far avoided casualties. Classic "slow burn, high consequence" — exactly the kind of tail risk the system's entropy weighting is designed to flag.
- *Horizon*: ~8 months. *Difficulty*: hard — low base rate but rising indicators.

**Topic 6: Section 122 tariffs at 150-day expiry (late July 2026)** (economics/policy)
- *Hypotheses*: H1: expire as scheduled, H2: renewed at same 10% rate, H3: expanded (higher rate or broader scope), H4: replaced by bilateral deals (partial rollback)
- *Indicators*: White House statements, Section 301 investigation outcomes, trade deficit data, business lobbying activity, Congressional action, WTO rulings
- *Current state*: 10% tariff on ~$1.2T of imports, effective Feb 24. SCOTUS struck down IEEPA tariffs; Section 122 has a statutory 150-day limit. New Section 301 probes launched into EU, Mexico, China. Political incentives unclear — expiry vs. renewal both have constituencies.
- *Horizon*: ~3.5 months (late July 2026). *Difficulty*: medium — policy prediction with observable leading indicators.

**Topic 7: Fed funds rate by end 2026** (economics)
- *Hypotheses*: H1: no further cuts (stays 3.50-3.75%), H2: one cut (to 3.25-3.50%), H3: two+ cuts (to 3.00-3.25% or below), H4: rate hike (inflation forces reversal)
- *Indicators*: CPI/PCE prints, unemployment rate, Fed dot plot, FOMC minutes language, market-implied probabilities, tariff impact on prices
- *Current state*: Fed held steady in March. Median dot plot projects one cut in 2026. Goldman forecasts two. Bankrate forecasts three. The tariff-inflation tension creates genuine ambiguity — cut for growth or hold for inflation?
- *Horizon*: ~8 months. *Difficulty*: medium — data-driven, falsifiable, multiple expert forecasts to calibrate against.

**Topic 8: US midterms 2026 — House control** (domestic politics)
- *Hypotheses*: H1: Democrats flip House (>218 seats), H2: Democrats gain seats but fall short, H3: Republicans hold (status quo ±5 seats), H4: Republicans gain seats
- *Indicators*: generic ballot polling, Trump approval (currently ~41%, economic approval 31%), special election results, redistricting outcomes, candidate recruitment, fundraising
- *Current state*: historical base rate strongly favors opposition gains in midterms. Trump approval declining (net -19%). But GOP getting midterm polling boost despite Trump's numbers — unusual divergence worth tracking.
- *Horizon*: ~7 months (Nov 2026). *Difficulty*: medium — strong historical prior but current cycle has unusual dynamics.

**Topic 9: Houthi Red Sea posture by mid-2026** (conflict/trade)
- *Hypotheses*: H1: attacks resume at pre-ceasefire intensity, H2: selective targeting (political screening, not indiscriminate), H3: de facto ceasefire holds (no commercial attacks), H4: Houthis escalate beyond Red Sea (Bab al-Mandeb full closure)
- *Indicators*: MARAD advisories, shipping insurance rates, Houthi statements, Iran war ceasefire status, Operation Rough Rider outcomes, maritime tracking data
- *Current state*: Houthis paused commercial attacks after Gaza ceasefire (Oct 2025), resumed Israel strikes in March 2026. Currently screening ships by political identity rather than attacking indiscriminately. Holding Red Sea leverage in reserve. Directly coupled to Hormuz topic — not independent, and that correlation itself is worth tracking.
- *Horizon*: ~3 months. *Difficulty*: medium — dependent on Iran war trajectory.

**Topic 10: Next RTSC claim survives independent replication** (physics)
- *Hypotheses*: H1: claim published and replicated within 12 months (≥2 independent labs), H2: claim published, partial replication (1 lab, or only some properties), H3: claim published, fails replication, H4: no credible claim published in window
- *Indicators*: arXiv preprints, journal publications, replication attempts, materials characterization data, theory predictions
- *Current state*: post-LK-99, the field has higher replication standards and faster debunking cycles. Base rate for H1 is near zero historically. But the operator's prior (skeptical, updated early on LK-99) is testable — does that calibration transfer to the next claim?
- *Horizon*: rolling (12-month window from any new claim). *Difficulty*: hard — rare events with high noise.

**Selection rationale:**
- **Domains**: geopolitics (2, 4, 5), economics (3, 6, 7), domestic politics (8), conflict/trade (9), physics (1, 10). Weighted toward geopolitics/economics because that's where the operator has active judgment to test.
- **Timescales**: 3 months (6, 9) to rolling (10). Clustering at 6-8 months ensures bulk resolution by early 2027.
- **Difficulty**: deliberate mix. Topics 6 and 7 are data-driven (observable indicators, expert forecasts to benchmark against). Topics 4 and 5 are genuinely hard (multi-actor, low-frequency events). Topic 10 is a known-hard problem that tests whether skeptical priors transfer across domains.
- **Correlation structure**: topics 2 and 9 are coupled (Hormuz and Houthi posture move together). Topics 3, 6, and 7 are correlated through macroeconomic channels. This is deliberate — the system needs to handle correlated topics honestly, and the calibration corpus should test that.
- **Topic 1 is the exception**: already resolved, backfill only. Included as a second historical anchor alongside LK-99.

### Timeline

- **Phase 1 — now (April 2026):** Backfill topic 1 (750 GeV). Set up topics 2, 6, and 9 prospectively — these have the shortest horizons (3-4 months) and the most observable indicators. Begin regular update cycles. Topic 2 (Hormuz) already has an active topic file; the others need new ones.
- **Phase 2 — May/June 2026:** Set up remaining topics (3, 4, 5, 7, 8, 10). First prospective resolutions expected: topic 6 (Section 122 expiry, late July) and topic 9 (Houthi posture, mid-2026).
- **Phase 3 — late 2026 / early 2027:** Bulk of resolutions arrive (midterms in November, Fed rate by December, Hormuz/recession/Ukraine by year-end). Compute aggregate Brier scores across domains. If N ≥ 10 resolved: derive preliminary empirical weight functions. If N < 10: identify which topics stalled and why.
- **Phase 4 — 2027+:** If calibration holds across domains, automate claim-state weight derivation. If it doesn't, diagnose *where* operator judgment diverges from outcomes — is it likelihood-setting, indicator design, or evidence selection? The Brier decomposition (reliability + resolution + uncertainty) tells you which component is failing. Feed that back into the topic design process, not just the weight functions.

The system will tell you honestly whether you're calibrated. The only thing it can't do is make you set up the topics in the first place. That's on you.
