# Systemic Revaluation: NRL-Alpha Omega Epistemology Audit

**Date**: 2026-04-10
**Author**: Governance Review

---

## EXECUTIVE SUMMARY

This document addresses four critical meta-questions about the NRL-Alpha Omega system:

1. **Single .md history file**: Can we track evolution without git?
2. **Governor's methodology suggestions**: What improvements does governance recommend?
3. **Kharg Island re-evaluation**: Has the attack occurred? Needs assessment.
4. **Epistemology revaluation**: Do we need systemic rethinking of how we know what we know?

**Verdict**: All four require attention. The system has developed "intellectual debt" from unaddressed failure modes.

---

## 1. SINGLE .MD HISTORY FILE

### Question
Can we maintain a single markdown history file without committing to GitHub?

### Answer: YES — with caveats

### Recommended Solution: `HISTORY.md`

```markdown
# NRL-Alpha Omega Update History
## Single-Sourced Chronicle

This file maintains the complete narrative of the Hormuz tracking operation.
```

### Structure

```markdown
# HORMUZ STRAIT CLOSURE — COMPLETE HISTORY
## Generated: YYYY-MM-DD HHMM UTC
## Classification: UNCLASSIFIED

---

## UPDATE: YYYY-MM-DD HHMM UTC

### Context
- **Day since tracking**: N
- **Ceasefire status**: Active/Expired/Negotiating
- **Governance health**: HEALTHY/DEGRADED/CRITICAL

### New Intel
- [x] Toll regime confirmed active (Fortune + Daily Mail + CBS)
- [x] Oil price rebound to $98 Brent
- [x] Islamabad talks scheduled April 10

### Developments
- Brent: $95.30 → $98.50
- WTI: $96.60 → $97.00
- War day: 35 → 36
- US KIA: 15 (stable)

### Posterior Shift
H1=0.5% | H2=15% | H3=55% | H4=29.5% | E[weeks]=39.6
Rationale: [one paragraph explanation]

### Watchpoints
- [ ] Islamabad talks outcome
- [ ] Ceasefire expiration (April 21)
```

### Pros of .md File
- Human-readable narrative
- Easy to share via email/attachments
- Can include analysis, not just data
- No git history confusion

### Cons
- No version branching
- Diff tooling less sophisticated
- Can get unwieldy with 100+ updates
- Merge conflicts if multiple authors

### Hybrid Solution

Keep git commits **plus** a running `.md` history file:

```bash
# After each update
cat >> HISTORY.md << 'EOF'
---
## UPDATE: YYYY-MM-DD HHMM UTC
[content above]
EOF
```

**Recommendation**: Start a new `history.md` file that's updated each cycle. Use it for narrative; keep git for audit.

---

## 2. GOVERNOR'S METHODOLOGY SUGGESTIONS

### Current Health: DEGRADED

The governor identifies these issues:

```python
issues = [
    "Majority of evidence is stale (197/219)",
    "R_t in SAFE regime (0.00) — well-evidenced but needs fresh intel"
]
```

### Governor's Methodology Recommendations

#### 2.1 Evidence Freshness

**Current**: 197 stale / 22 fresh = 90% stale
**Governor threshold**: `stale > fresh` → DEGRADED

**Fix**: Gather fresh intel each update cycle. Empty searches are fine but must be **logged**.

```python
# Good:
"Searched: [queries]; Result: No new developments"

# Bad (recycled):
"Confirming no change since last update"
```

#### 2.2 Inadmissible Hypotheses

Check `topic["model"]["hypotheses"]`:

```python
for k, v in hypotheses.items():
    if v["posterior"] < 0.05:  # H1 at 0.5%
        mark_as_inadmissible(k)
```

**Implication**: H1 (<6 weeks) at 0.5% is nearly dead. Should we drop it or keep as "narrative dead end"?

#### 2.3 Unfalsifiable Hypotheses

The governor checks for anti-indicators. Current hypotheses:
- H1: <6 weeks (can be falsified by continued closure)
- H2: 6wk-4mo (can be falsified)
- H3: 4-12mo (can be falsified)
- H4: >12mo (can be falsified)

**Issue**: None currently unfalsifiable. Good.

#### 2.4 Uncertainty Ratio

```python
entropy = 1.56/2.00 = 78% uncertainty
```

**Governor warning**: "Near-maximum uncertainty — model is not discriminating" if >90%

**Current**: 78% is healthy range, though high due to toll regime ambiguity.

#### 2.5 R_t Regime

```
R_t = 0.00 → SAFE regime
Meaning: Well-evidenced, recently updated
```

**Implication**: Model is not "runaway" but governance is degrading from stale evidence.

### Methodology Changes Required

1. **Evidence freshness**: Must gather fresh intel every cycle
2. **H1 inadmissibility**: At 0.5%, consider dropping H1 or marking inadmissible
3. **Search logging**: Document every search attempt, even empty results
4. **Cross-reference requirement**: 2+ sources before adding evidence

---

## 3. KHARG ISLAND RE-EVALUATION

### Evidence Log Check

```python
iceland_entries = [e for e in log if 'iceland' in e.get('text', '').lower()]
print(f"Kharg entries: {len(iceland_entries)}")
```

**Result**: 0 explicit "Kharg Island attack" entries

### Why No Entries?

Looking at posterior history, April 7 note says:
```
"Kharg attack Apr 7 = decisive military resolution move"
```

But no explicit evidence entry for:
- "US forces seized Kharg Island"
- "Kharg assault completed"
- "ICeland under US control"

### The Problem

The April 7 brief mentions:
> "Explosions reported April 7 by Mehr News Agency"

But explosions ≠ seizure. We need to distinguish:

| Event | Logged? | Evidence |
|--|--|--|
| Explosions on Kharg | ✅ Yes | Mehr News report |
| Seizure of Kharg | ❌ No | ??? |
| US forces landed | ❌ No | ??? |
| Holding the island | ❌ No | ??? |

### Assessment: Attack Status

**Scenario A: Explosions Only**
- Kharg damaged but not seized
- Toll regime continues
- Closure persists
- H3/H4 remain dominant

**Scenario B: Seizure Completed**
- US holds Kharg
- Base operational
- Closure shortened (or ended?)
- H2/H3 shift up

**Current Evidence Points to Scenario A**:
1. No explicit "seized" entries
2. Toll regime still active (Fortune/Daily Mail)
3. Traffic at 11 ships/day (not 40/day threshold)
4. War day 36 (not resolved)

### Recommendation

**Add evidence if applicable**:

```python
# If seized:
add_evidence(topic, {
    "tag": "INTEL",
    "text": "US forces seized Kharg Island on April 7, 2026. Explosions confirmed by Mehr News. Island under US control.",
    "provenance": "OBSERVED",
    "source": "US CENTCOM / Pentagon press release",
    "posteriorImpact": "MAJOR",
})

# If only explosions:
add_evidence(topic, {
    "tag": "EVENT",
    "text": "Explosions on Kharg Island confirmed April 7. No seizure. Infrastructure damage continues. Toll regime unaffected.",
    "provenance": "OBSERVED",
    "source": "Mehr News + Axios cross-reference",
    "posteriorImpact": "MINOR",
})
```

---

## 4. EPISTEMOLOGY REVALUATION

### The Core Issue

The system has developed "intellectual debt" from recurring failure modes:

1. **Intel gathering laziness** → Assuming offline without verifying
2. **Brief recycling** → Re-reading prior brief instead of searching
3. **Evidence dedup** → Same event logged multiple times across feeds
4. **Feed key confusion** → camelCase vs underscore
5. **Governance degradation** → DEGRADED health, not fixed

### Epistemology Problems

#### 4.1 Source Hierarchy

**Current**: All sources treated equally

**Problem**: A Daily Mail tabloid shouldn't have same weight as CENTCOM

**Fix**: Add `source_trust_score` or `credibility` field

| Source | Trust Score |
|--|--|
| CENTCOM / Pentagon | 0.9 |
| Reuters/AP | 0.8 |
| WSJ | 0.75 |
| Bloomberg | 0.7 |
| CNN/Fortune | 0.6 |
| Daily Mail | 0.4 |
| Iran state media | 0.2 (for Iranian claims) |

#### 4.2 Evidence Categories

**Current**: OSINT | SIGINT | ECON | INTEL | DIPLO

**Problem**: Doesn't capture:
- **Rhetoric** vs **Fact** (e.g., Trump threats)
- **Prediction** vs **Observation** (e.g., "will attack" vs "did attack")
- **Official** vs **Leak** (WaPo Pentagon leak vs anonymous source)

**Fix**: Add `category` field:
```python
{
    "type": "OBSERVED",  # Fact, not prediction
    "category": "RHEOTRIC",  # Future tense prediction
    "category": "OFFICIAL",  # Pentagon/IRGC statement
    "category": "LEAK",  # WaPo leak
}
```

#### 4.3 Hallucination Failure Modes

The governor tracks these (from methodology):

1. **Rhetoric-as-evidence**: "Iran is cornered" → not evidence
2. **Anchoring**: Previous posterior pulls new one
3. **Narrative momentum**: Once on "military resolution" track, can't pivot
4. **Phantom precision**: Fake precision in numbers
5. **Recycled intel**: Re-reading prior brief
6. **Empty search not logged**: "No new intel" without searching
7. **Feed key confusion**: Wrong variable names
8. **Stale evidence ignored**: Governance DEGRADED not fixed
9. **Cross-reference skipped**: One source, not two
10. **Posterior held without rationale**: Why hold?

**Current status**: Several active (recycling, stale evidence, empty search)

#### 4.4 Resolution Criterion Evolution

**Original**: "Sustained >30% of pre-war traffic (~40 transits/day)"

**Reality Check**:
- Current: 11 ships/day (dual-corridor)
- Pre-war: ~138 ships/day
- 11/138 = 8% (not 30%)

**Problem**: Even if traffic increases, toll regime = Iranian control.

**New criterion needed**:
- **Freedom of navigation**: Can vessels freely pass without permission?
- **Sovereignty claim**: Is Iran asserting sovereignty via toll?
- **Toll regime permanence**: Is this temporary or permanent?

**Framework update**:
```
Resolution = {
    "traffic_threshold": 40,  # 30% of pre-war
    "freedom_of_navigation": True,  # Can we pass freely?
    "toll_regime_resolved": False,  # Toll ≠ resolution
    "sovereignty_claim": "IRGC asserts control",
}
```

#### 4.5 Sub-model Completeness

**Current sub-models**:
- meuMission (Kharg/escort/Larak/etc.)
- trumpUltimatum (power plants/deadline/etc.)
- talksTrack (mediators/Trump overstates/etc.)

**Missing sub-models**:
- **Resolution**: Free navigation vs toll regime
- **Market**: Oil price dynamics
- **Casualties**: KIA trends
- **Diplomatic**: Consortium talks progress

**Fix**: Add sub-models for key variables

---

## RECOMMENDATIONS

### Immediate (next update)

1. **Create history.md**: Start single-sourced narrative file
2. **Add Kharg entry**: Clarify explosions vs seizure
3. **Fix evidence freshness**: Search for April 9-10 developments
4. **Cross-reference sources**: Add trust scores

### Short-term (this week)

1. **Add resolution sub-tracks**:
   - `freedom_of_navigation`: Can vessels freely pass?
   - `toll_regime`: Is toll permanent or temporary?
   - `sovereignty`: Who claims control?

2. **Improve source hierarchy**: Trust scores for credibility

3. **Evidence categories**: Rhetoric vs fact vs prediction

4. **Kharg status**: Add explicit seizure evidence (or explosions-only)

### Long-term (ongoing)

1. **Automated feeds**: Brent prices, AIS transits, etc.
2. **RSS aggregation**: Monitor news feeds automatically
3. **Source calibration**: Learn which sources are more accurate
4. **Sub-model expansion**: Add missing variables
5. **History file**: Parallel to git, for narrative

---

## CONCLUSION

The system has served well but accumulated intellectual debt:

| Issue | Status | Fix |
|--|--|--|
| Single history file | Needs creation | Start `history.md` |
| Governor suggestions | DEGRADED health | Gather fresh intel |
| Kharg Island | Explosions only? | Add explicit entry |
| Epistemology | Multiple failures | Source hierarchy, categories, sub-models |

**The toll regime confirmation is a game-changer**: It shifts the narrative from "will Iran reopen?" to "how permanent is Iran's control?" This is regime change, not blockade.

**Recommendation**: Address all four issues before next update cycle.

---

*Generated: 2026-04-10*
*Governance health: DEGRADED (needs fresh intel)*
