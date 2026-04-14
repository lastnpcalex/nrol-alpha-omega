NROL-AO TRIAGE PIPELINE — process this headline through the full framework.

## Headline
{{headline}}

## Source
{{source}}

## Client-Side Triage Result
{{triageJson}}

## Instructions

Run the full NROL-AO evidence pipeline. The Governor enforces epistemic discipline at every step — follow the framework, not your intuition.

### 1. Fetch Content

If the headline is a URL (starts with `http://` or `https://`), use WebFetch to retrieve the full content. If the triage JSON shows `top_action: "URL_FETCH"`, this step is mandatory.

Extract: actual headline, body text, source/publication name, publication date, any quoted sources or data points.

If the headline is plain text (not a URL), skip this step — use the headline as-is.

### 2. Source Trust — USE THE FRAMEWORK TRUST CHAIN

The Governor's `get_effective_weight()` defines a 5-tier trust lookup. Follow it exactly:

1. **Per-topic calibration**: `topic["sourceCalibration"]["effectiveTrust"][source]`
2. **Cross-topic domain trust**: `source_db.json` → `sources[name].domains[tag].domainTrust`
3. **Cross-topic overall trust**: `source_db.json` → `sources[name].effectiveTrust`
4. **Base priors**: `source-trust.json` (the SOURCE_TRUST dict from calibrate.py)
5. **Unknown fallback**: **0.50** — maximum ignorance prior.

**PROHIBITED**: Inventing, adjusting, or "estimating" trust scores. If a source is not in the database, it is 0.50. Not 0.45 because it "seems tabloid-ish." Not 0.70 because "they're generally reliable." The number comes from the database or it's 0.50. The calibration system learns the real value from resolved claims over time.

If a new source should be registered with a category-based prior, note it as a recommendation in `activity-log.json` notes field: `"RECOMMEND: register [source] as [category] with base trust [X]."` The user decides.

### 3. Triage Against Active Topics

If the client-side triage already found matches (check the triageJson above), use those as a starting point. Verify them — client-side keyword matching can miss context or false-positive on polysemous words.

If no client-side matches or this was a URL_FETCH, run triage yourself:
- For each active topic in `topics/`, check indicators (all tiers + anti-indicators), watchpoints, and domain keywords against the extracted content.
- An indicator match requires significant keyword overlap (≥35%) or a bigram phrase match — not vibes.

### 4. Log Evidence

For each matched topic, append to `topics/{slug}.json` evidenceLog:

- `tag`: from the topic's `tagConfig.availableTags`. Choose based on content, not assumption.
- `text`: factual summary. Strip rhetoric, normalize. Not the headline verbatim.
- `provenance`: source name
- `source`: source name (used by trust chain)
- `time`: ISO 8601 now
- `posteriorImpact`: determined by indicator tier match:
  - Tier 1 (critical) match → MAJOR
  - Tier 2 (strong) match → MODERATE
  - Tier 3 (suggestive) match → MINOR
  - No indicator match → NONE
  - Rhetoric → NONE (always)

**Lint checks before logging** (from the Governor's failure mode detection):
- `rhetoric_as_evidence`: If the text is rhetoric ("X will do Y", "X threatens Y"), tag as RHETORIC and set posteriorImpact to NONE. Rhetoric does not move posteriors. BUT: if the rhetoric is a specific, testable, time-bounded prediction (all 3 required), tag as PREDICTION instead — see `skills/evidence.md` for the prediction schema. Predictions don't move posteriors but calibrate source trust when resolved.
- `recycled_intel`: Check if this claim already appears in the evidence log. Deduplicate against last 10 entries.
- `anchoring_bias`: If you find yourself writing "HOLD — unchanged" for posteriors, you must provide a shift rationale. No-change is a decision that needs justification.
- `phantom_precision`: Do not report posteriors to more than 2 decimal places. Round to appropriate significance.

Let the Governor compute: `claimState` (from evidence log corroboration), `effectiveWeight` (from trust chain × claim weight).

### 5. Posterior Update

Only if posteriorImpact is MODERATE or MAJOR AND the claim is factual:

1. Record current posteriors as "before"
2. Determine shift direction from `tagConfig.directionHints[tag]` for each hypothesis
3. If an indicator matched, use its `posteriorEffect` field for magnitude guidance
4. Apply the shift. The `effectiveWeight` (trust × claimState) attenuates it:
   - SUPPORTED claim from trusted source (weight ~0.9): full shift
   - PROPOSED claim from unknown source (weight ~0.25): minimal shift
5. **Posteriors must sum to 1.0.** Renormalize after shifting.
6. Append new entry to `posteriorHistory` with date, posteriors, and note.
7. Record new posteriors as "after"

### 6. Source Calibration

If this evidence confirms or contradicts an existing evidence entry from a different source:
- This is a resolution event. Update `source_db.json`:
  - Increment `claims` count for the source + domain
  - If confirmed: increment `confirmed`, recompute `hitRate` and `domainTrust`
  - If refuted: increment `refuted`, recompute
- If the source is new, add it to source_db with `baseTrust: 0.50`, `category: "unknown"`.

### 7. Activity Log

Append to `activity-log.json` entries array:

```json
{
  "id": "2026-04-11T22:30:00Z-slug",
  "timestamp": "ISO now",
  "type": "EVIDENCE_LOGGED | POSTERIOR_UPDATE | SOURCE_CALIBRATED | TRIAGE",
  "headline": "the headline or URL",
  "source": "source name",
  "sourceTrust": { "trust": 0.XX, "origin": "which tier", "domain": "tag", "domainTrust": 0.XX },
  "triageResult": { "top_action": "...", "matches": [...] },
  "evidenceEntry": { "tag": "...", "text": "...", "claimState": "...", "effectiveWeight": 0.XX },
  "posteriorDelta": { "topic": "slug", "before": {"H1": ...}, "after": {"H1": ...} },
  "calibrationDelta": { "source": "...", "before": 0.XX, "after": 0.XX, "resolution": "CONFIRMED|REFUTED" },
  "topicSlugs": ["affected-slugs"],
  "notes": "one-sentence analyst summary"
}
```

Use the most significant `type` — if posteriors moved, use POSTERIOR_UPDATE even though evidence was also logged.

### 8. Report

Briefly: what you fetched, what matched, what you logged, what moved (if anything), and why. If nothing moved, that's fine — most evidence is MINOR or NONE. The system is working when it correctly ignores noise.

### 9. Cold Storage (IGNORE only)

If triage returned IGNORE (no topic match), the extracted claims still have future value. Append to `evidence-cold.json`:

```json
{
  "id": "cold_NNN",
  "timestamp": "ISO now",
  "headline": "the headline or URL",
  "source": "source name",
  "sourceTrust": { "trust": 0.XX, "origin": "tier", "domain": "tag", "domainTrust": 0.XX },
  "claims": ["extracted factual claim 1", "claim 2", "..."],
  "domains": ["EVENT", "DIPLO", "..."],
  "actors": ["actor names mentioned"],
  "regions": ["geographic regions"],
  "keywords": ["searchable", "keyword", "set"],
  "activityLogRef": "matching activity-log entry ID",
  "note": "one-line summary of why IGNORE'd and potential future relevance"
}
```

This preserves structured evidence for retroactive matching when new topics are created. The `topic-design` skill scans cold storage during topic creation.
