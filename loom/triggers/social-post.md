NROL-AO SOCIAL MEDIA PIPELINE — process a social media post through the framework.

## URL
{{headline}}

## Source (user-provided)
{{source}}

## Platform Detected
{{platform}}

## Instructions

This is a social media post. Social posts require the same epistemic discipline as any other evidence — the framework handles trust, not you.

### 1. Fetch the Post Content

Route by platform:

- **Bluesky** (`bsky.app`): Use the bsky-get-post or bsky-get-thread skill.
- **Twitter/X** (`x.com`, `twitter.com`): Extract username from URL. Attempt WebFetch. If blocked, try `fixupx.com` mirror.
- **Reddit**: Fetch via `old.reddit.com` version.
- **YouTube**: WebFetch the page, extract title + description.
- **Other**: WebFetch. Extract what you can.

Extract: the post text, the author handle/name, the timestamp, and any linked sources or quoted content.

### 2. Source Trust — USE THE FRAMEWORK, DO NOT INVENT NUMBERS

The Governor's `get_effective_weight()` defines a 5-tier trust lookup chain. You MUST follow it:

1. **Per-topic calibration**: Check `topic["sourceCalibration"]["effectiveTrust"]` for this source handle.
2. **Cross-topic domain trust**: Check `canvas/source_db.json` → `sources[handle].domains[tag].domainTrust`.
3. **Cross-topic overall trust**: Check `canvas/source_db.json` → `sources[handle].effectiveTrust`.
4. **Base priors**: Check `canvas/source-trust.json` for the source name.
5. **Unknown fallback**: If the source is not in ANY of the above, assign **0.50** (maximum ignorance prior). Do not adjust this based on vibes, follower count, verification status, or your assessment of the account. 0.50 means "we have no data." The Bayesian machinery will update it from resolved claims.

**PROHIBITED**: Assigning trust scores like "~0.75 because they seem credible" or "0.35 because anonymous accounts are unreliable." These are the LLM performing rationality. If the source isn't in the database, it's 0.50. Period. The calibration system will learn the real number from evidence.

If you want to register a new source with a category-based prior (e.g., a government account at 0.90), note that in the activity log as a recommendation — do not unilaterally assign it. Write: `"notes": "New source @handle not in source_db. Assigned 0.50 unknown prior. RECOMMEND: register as [category] with base trust [X] via calibrate.py register."` The user decides.

### 3. Extract the Claim — Separate Fact from Rhetoric

The Governor's lint module flags `rhetoric_as_evidence` as a HIGH severity failure mode. Apply this rigorously:

- **Factual claim** (something happened, a number changed, an action was taken): tag as the appropriate domain tag (EVENT, DATA, MILITARY, etc.)
- **Rhetoric** (opinion, prediction, threat, posturing, "X will do Y"): tag as RHETORIC. `posteriorImpact: NONE`. Rhetoric does not move posteriors. Log it for the record but the Governor suppresses its weight.
- **Mixed**: Extract the factual component only. The rhetoric wrapping is noise.

Social media is disproportionately rhetoric. Most posts will be tagged RHETORIC with posteriorImpact NONE. This is correct behavior, not a failure.

### 4. Log Evidence

Append to `canvas/topics/{slug}.json` evidenceLog with:
- `tag`: from topic's `tagConfig.availableTags`
- `text`: factual summary (not the post verbatim — strip rhetoric, normalize)
- `provenance`: source handle + platform (e.g., `@user on X`)
- `source`: the handle
- `time`: ISO 8601 now
- `posteriorImpact`: NONE for rhetoric. For factual claims, determine from indicator tier match (tier1 → MAJOR, tier2 → MODERATE, tier3 → MINOR, no match → NONE).
- Let the Governor compute: `ledger` (FACT or DECISION), `claimState` (from evidence log overlap), `effectiveWeight` (from trust chain above).

### 5. Posterior Update

Only if `posteriorImpact` is MODERATE or MAJOR AND the claim is factual (not RHETORIC):
- Record current posteriors as "before"
- Use `tagConfig.directionHints[tag]` and matched indicator `posteriorEffect` to determine direction
- Shift posteriors. Ensure sum = 1.0.
- Append to `posteriorHistory`
- Record new posteriors as "after"

If `claimState` is PROPOSED (single source, unverified): cap the posterior shift. A PROPOSED claim from an unknown source (trust=0.50) should produce minimal movement. The math handles this via `effectiveWeight` — trust it.

### 6. Update Source DB and Activity Log

- If the source handle is new: add to `canvas/source_db.json` with `baseTrust: 0.50`, `category: "social_media"`, empty domains, empty topicHistory.
- If this confirms/refutes existing evidence: update the ledger.
- Append to `canvas/activity-log.json` with full audit trail.

### 7. Report

State what you found, what you logged, and what (if anything) moved. If the post was rhetoric, say so. Do not apologize for logging rhetoric at zero weight — that IS the system working.
