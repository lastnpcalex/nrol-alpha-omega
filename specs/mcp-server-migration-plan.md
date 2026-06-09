# Spec: MCP Server Migration and Authority Boundary
**Status:** DRAFT  
**Scope:** `engine.py`, `framework/pipeline.py`, `framework/news_*`, cron/update entry points, topic JSON write path  
**Goal:** Move engine + search + simple deliberation behind an MCP server so human/LLM operators can propose observations and review decisions without direct code edits or direct topic JSON writes.

---

## Problem

The project already has the right conceptual split:

- operator: perception, hypothesis design, review judgment
- engine: Bayesian update math and calibration
- governor: admissibility, provenance, anti-hallucination gates

The implementation does not yet enforce that split at the system boundary. Claude/Codex-style agents can still operate in a repo with `Write` access, import engine internals, call old update scripts, or edit topic JSON directly. That means the governor is partly a convention rather than a capability boundary.

The MCP server should become the only online writer of operational state. Operators should submit typed proposals to the server. The server should validate, persist, update, park, or reject those proposals through a narrow API.

---

## Mathematical Structure

The live posterior state is a probability vector:

```
p_t = [P_t(H1), ..., P_t(Hn)], sum(p_t) = 1
```

For ordinary evidence, updates must be indicator-bound:

```
p_{t+1}(H_i) = p_t(H_i) * L_i / sum_j p_t(H_j) * L_j
```

where `L_i` is either:

- a pre-committed indicator likelihood vector,
- a mechanically derived partial-observation likelihood from an `observable` block,
- or a pre-registered resolution-class target path.

Evidence quality attenuates likelihoods via the mixture model:

```
L'_i = w * L_i + (1 - w) * mean(L)
```

where `w = claim_state_weight * source_trust`. At `w = 0`, all hypotheses receive the same likelihood and the posterior does not move.

This gives the core invariant:

> The operator can affect posteriors only by submitting an observation that maps to a pre-authorized likelihood surface. The operator cannot submit `p_{t+1}`.

For unmatched evidence, the correct operation is:

```
evidence -> evidenceLog + flagged_for_indicator_review
p_{t+1} = p_t
```

For topic/schema design, operator judgment is allowed, but should be a separate draft/approval workflow, not the same runtime path that updates active posteriors.

---

## Epistemic Design Logic

The system should treat natural language as perception, not authority. A human or LLM can say:

- "this article is relevant"
- "this article appears to match indicator X"
- "the reported value is 4.2"
- "the schema lacks an observable for this direction"
- "this evidence should be reviewed"

It cannot say:

- "therefore H3 should be 0.72"
- "apply LR 6 unless that LR was pre-committed"
- "add this new indicator and fire it in the same step"
- "count these five articles as independent without source-chain review"

That distinction is the core epistemic patch.

### Belief Updates Are State Transitions

Every runtime update should be one of four typed transitions:

| Transition | Input | Posterior movement |
|------------|-------|--------------------|
| `PARK` | relevant but unmatched evidence | none |
| `FIRE` | binary pre-committed indicator match | Bayes via indicator LR |
| `OBSERVE` | numeric/graded observable value | Bayes via mechanical partial LR |
| `SCHEMA_GAP` | relevant evidence with no valid observable | none |

Anything else is an admin/design transition, not a runtime update.

This gives a clean state machine:

```
article
  -> candidate_evidence
  -> match_proposal
  -> {parked | committed_indicator_fire | committed_observation | schema_gap | rejected}
  -> governance_snapshot
  -> digest/review_queue
```

The MCP server owns the transition from proposal to committed state.

### Topic Update Scans

A topic update is not "find one article and update." It is a bounded review cycle over a topic's current epistemic state:

1. identify stale hypotheses, stale indicators, stale dependencies, and high-VoI queries
2. search for recent evidence on each live hypothesis in both directions
3. search wildcard for events that do not fit the current schema
4. dedupe and source-chain the article set
5. classify each article into `PARK`, `FIRE`, `OBSERVE`, `SCHEMA_GAP`, or `IGNORE`
6. commit valid state transitions
7. emit a topic update report, including non-updates

This is the operational cron job. Its success criterion is not "posterior moved"; its success criterion is "the topic was refreshed against current evidence and every possible mutation was either committed, parked, rejected, or escalated."

Topic scans should be budgeted and auditable:

- fixed time window, e.g. last 12h/24h/7d
- explicit search mandates per hypothesis plus wildcard
- saved search queries and model prompts
- saved article candidates, including ignored duplicates
- scan result hash and governance snapshot
- next recommended scan interval based on R_t and classification

### Hypothesis Proposal

Hypothesis proposal is allowed, but it is not a runtime posterior update. It is a design operation that creates or revises the hypothesis space.

The trigger conditions should be explicit:

- repeated `SCHEMA_GAP` items point to an unmodeled outcome
- wildcard scan finds a plausible outcome not covered by current hypotheses
- existing hypotheses are not mutually exclusive or not exhaustive
- resolution criteria cannot assign future evidence cleanly
- dependency checks imply a downstream topic's hypothesis set is stale

Hypothesis proposal should produce a draft package:

```
proposal_id
topic_slug or new_topic
proposed_hypotheses
mapping_from_old_hypotheses
resolution_criteria
prior_recommendation
evidence_context
red_team_objections
operator_notes
```

The server must not let a hypothesis proposal immediately rewrite active topic state. A proposal goes through design lint, red-team, and operator approval. If accepted for an existing topic, the migration is an admin/design event with an explicit mapping from old probability mass to new hypotheses.

The mass-mapping problem is important:

- split: old H2 becomes H2a/H2b, requiring a conditional allocation
- merge: H2/H3 collapse into one hypothesis, requiring summed mass
- add residual: a new "other" or tail hypothesis takes mass from all existing hypotheses
- reframe: the topic may need to close and spawn a replacement topic instead of mutating in place

For active topics, prefer spawning a replacement topic when the hypothesis space changes materially. In-place migration should be rare and auditable because it complicates calibration history.

### Where Judgment Is Allowed

Judgment is allowed in these places:

- relevance: whether an article bears on a topic
- extraction: what factual claim/value the article reports
- matching proposal: which existing indicator might apply
- hypothesis proposal: whether the current outcome space is missing a live branch
- schema design: which hypotheses and indicators should exist
- schema cleanup: whether parked evidence reveals a missing observable
- brief synthesis: what the current state means operationally

Judgment is not allowed in these places:

- posterior arithmetic
- likelihood invention during runtime
- active hypothesis-space mutation without design/admin workflow
- evidence deduplication/correlation overrides without audit
- direct topic state mutation
- schema extension and posterior update in one atomic operator step

The important rule is "design-time judgment, runtime mechanical inference." Runtime should be mostly recognition and extraction.

### Hypothesis Creation

Hypothesis creation is the riskiest epistemic step because it defines the probability space. The MCP server should support it, but only as a draft workflow:

1. `create_topic_draft()` accepts question, horizon, resolution criteria, candidate hypotheses.
2. `lint_topic_draft()` checks mutual exclusivity, exhaustiveness, falsifiability, measurable resolution, and prior justification.
3. `redteam_topic_draft()` asks for missing hypotheses, ambiguous resolution cases, prior anchoring, and indicator leakage.
4. `commit_topic_draft()` writes an ACTIVE topic only after lint and review pass.

Topic creation should never happen inside the same tool call as evidence ingestion. Otherwise a model can see today's article, create an indicator around it, and immediately update on it. That is disguised freeform updating.

### Indicator Creation

Indicator creation should be separated into two classes:

- design-time indicators: created before evidence flow begins
- cleanup indicators: created after parked evidence accumulates

Cleanup indicators require special handling:

1. parked evidence creates a review queue
2. schema-gap resolver proposes one or more indicators
3. adversarial review checks that the new indicator is not just today's evidence reworded as a general rule
4. if accepted, the indicator may apply to matching parked evidence only if the proposal records why the indicator is generalizable

The key lint is "resolution disguise": an indicator must be an observable signal, not a near-restatement of the target hypothesis or a one-off article.

### Deliberation Is Evidence About The Mapping, Not Evidence About The World

A local model debate can improve the proposed mapping from article to action. It should not itself increase confidence in the hypothesis.

Example:

```
Article: official GDP print
Matcher: OBSERVE recession_indicator AT value=...
Rebut: confirms value is in native units
Jury: commit proposal
```

The debate affects whether the observation is accepted. It does not create an additional likelihood ratio. Five models agreeing that an article matters is not five pieces of world evidence; it is one procedural confidence signal about classification.

### Independence And Correlation

The system should assume evidence is correlated until it has a reason not to.

Commit-time defaults:

- same URL: duplicate
- same canonical headline/source wire: likely duplicate
- same primary source quoted by several articles: same information chain
- same underlying event triggering multiple indicators: attenuate by `causal_event_id`
- multiple model votes on the same article: no evidential compounding

This is conservative but correct for sparse news domains. Overconfidence from invisible correlation is a larger long-run error than under-updating on one extra article.

### What Counts As An Epistemic Success

A scan that produces no posterior update can still be successful:

- it found nothing new
- it parked unmatched but relevant evidence
- it found a schema gap
- it confirmed an observable was unchanged and avoided repeated firing
- it degraded governance health due to staleness

The operator digest should make non-mutations visible. Otherwise users and agents will pressure the system to "do something", which is exactly how freeform updates return.

---

## Current Boundary Leaks

### 1. Legacy posterior override path

`framework/update.py` still accepts explicit `--posteriors` and routes them to `update_posteriors()`. Its force branch can manually write normalized posteriors after a governance block. `framework/run.sh` and `framework/runner.py` also expose posterior arguments.

This is the biggest mismatch with the desired system. The operator can still say "make the posterior this" instead of "this indicator fired".

Decision: deprecate `update_posteriors()` for active, unresolved topics except tightly scoped resolution/backfill/admin tools. Ordinary update APIs must call `framework.pipeline.process_evidence()` or `apply_observation()`.

### 2. Cron delegates authority to Claude with write access

`cron-update.sh` runs `claude -p` with `WebSearch,Read,Write` and tells it to save updated state. That gives the cloud operator broad authority over the repo and topic files.

Decision: cron should call the MCP server, not an agent with write access. Agents can still search and deliberate, but their output is data passed to MCP tools.

### 3. Python internals remain callable by any repo agent

The newer pipeline is much better: unmatched evidence parks, and indicator-bound evidence calls `bayesian_update()` with an `indicator_id`. But because the repo itself is the operating environment, agents can still bypass this by editing JSON or importing private functions.

Decision: move the operational state store outside normal repo write access, or run the MCP server as the only process with write permission. Treat the repo as code/config, and state as append-only data owned by the server.

### 4. Resolution-class and admin paths are mixed with live operations

Resolution-class indicators, outcome resolution, replay, deadline elimination, and calibration backfills are valid mutations. They should not be exposed through the same general update channel.

Decision: separate runtime tools from admin tools, with different auth policy and audit flags.

### 5. Deliberation can become accidental authority

Search/match/debate prompts can produce `FIRE`, `OBSERVE`, `PARK`, or `SCHEMA_GAP`. The danger is treating a deliberation verdict as the final mutation instead of as a proposal that a deterministic server validates.

Decision: deliberation produces signed proposals. The MCP server validates indicator existence, observable units, directional alignment metadata, evidence identity, duplicate/correlation state, and governor checks before mutation.

---

## Desired Authority Model

Use four roles:

| Role | Authority |
|------|-----------|
| `reader` | Read topics, evidence, governance, source trust, search history |
| `operator` | Submit evidence, observations, headlines, review parked evidence, request briefs |
| `designer` | Create or amend topic schemas through draft gates |
| `admin` | Resolve outcomes, replay/backfill, repair false-positive resolution events |

The human and cloud LLM usually get `operator`. Local deliberation models get `operator` or `reader` depending on the job. No operator gets filesystem write access to `topics/`.

---

## MCP Tool Surface

### Read tools

- `list_topics(status?, classification?)`
- `get_topic(slug, include_history=false)`
- `get_governance(slug)`
- `get_evidence(slug, query?, since?, limit?)`
- `get_flagged_evidence(slug)`
- `get_source_trust(source?, domain?)`
- `search_memory(query, topic_slug?, limit?)`

These are safe for cloud operators and local models.

### Runtime mutation tools

- `submit_article(article)`  
  Stores a fetched article or headline as a candidate observation. No posterior movement.

- `triage_article(article_id | article)`  
  Runs deterministic keyword/embedding triage and returns candidate topics/indicators. No posterior movement.

- `propose_match(article_id, slug, action, indicator_id?, observed_value?, rationale)`  
  Records an LLM/human proposal. No posterior movement.

- `commit_match(proposal_id)`  
  The server validates and applies exactly one of:
  - `PARK`: `process_evidence(..., fired_indicator_id=None)`
  - `FIRE`: `process_evidence(..., fired_indicator_id=indicator_id)`
  - `OBSERVE`: `apply_observation(..., indicator_id, observed_value)`
  - `SCHEMA_GAP`: log and enqueue schema gap

- `run_scan(slug?, time_window?, mode="routine")`  
  Server-side orchestration for search + deliberation + proposal creation. It may use local models for simple deliberation. Commits require server validation.

- `run_topic_update(slug, time_window?, max_articles?, auto_commit_policy?)`  
  Full topic refresh: governance orientation, hypothesis-directed search,
  wildcard search, dedupe, match proposals, safe commits, parked/schema-gap
  queues, and an update digest.

- `propose_hypothesis(topic_slug, proposal)`  
  Records a candidate new/revised hypothesis set. No active topic mutation.

- `review_hypothesis_proposal(proposal_id)`  
  Runs hypothesis-space lint and adversarial review. No active topic mutation.

- `generate_brief(slug, mode?)`  
  Read current state and write a derived brief. It must not accept posterior changes.

### Design/admin tools

- `create_topic_draft(config)`
- `lint_topic_draft(draft_id)`
- `commit_topic_draft(draft_id)`
- `commit_hypothesis_proposal(proposal_id)`
- `start_indicator_cleanup(slug, evidence_ids)`
- `propose_schema_extension(session_id, proposal)`
- `commit_schema_extension(session_id)`
- `resolve_topic(slug, resolved_hypothesis, evidence_refs)`
- `admin_replay(slug, replay_id)`
- `admin_reset_resolution_indicator(slug, indicator_id, reason)`

These tools should be unavailable to cron jobs and ordinary cloud operators.

---

## Actual Tool Flow

### Flow A: Human/Cloud Operator Drops A News Item

```
operator -> submit_article(article)
MCP      -> ArticleSubmitted

operator/local model -> triage_article(article_id)
MCP                  -> candidate topics + candidate indicators, no mutation

operator/local model -> propose_match(article_id, slug, action, indicator_id?, observed_value?, rationale)
MCP                  -> MatchProposalRecorded, no mutation

operator or cron policy -> commit_match(proposal_id)
MCP                    -> validate proposal
                        -> process_evidence/apply_observation
                        -> save projection
                        -> GovernanceSnapshot
                        -> CommitResult
```

The operator can stop at `propose_match`; cron can auto-commit only if the proposal is low-risk and passes deterministic validation.

### Flow B: Scheduled Scan

```
scheduler -> run_topic_update(slug, time_window)
MCP       -> orient topic: posteriors, R_t, stale deps, flagged evidence, open gaps
          -> build search mandates from hypotheses, indicators, watchpoints, VoI
          -> run search workers
          -> dedupe articles
          -> run local matcher
          -> optionally run local advocate/rebut/jury for PARK candidates
          -> record proposals
          -> auto-commit safe proposals if policy allows
          -> park/reject the rest
          -> update lastScanned
          -> return digest
```

Suggested auto-commit policy:

- `PARK`: auto-commit
- `SCHEMA_GAP`: auto-commit to queue
- `OBSERVE`: auto-commit only for numeric official/data-source evidence with clean units
- `FIRE`: require human approval for tier 1; allow auto-commit for tier 2/3 only if source trust, dedupe, and direction checks pass

That keeps cron useful while preventing high-impact automatic jumps.

The digest should include:

- posterior changes, if any
- evidence committed without posterior movement
- parked evidence
- schema gaps
- hypothesis-gap proposals
- stale dependencies
- source-trust changes
- next scan recommendation

### Flow B2: Manual Topic Update

```
operator -> run_topic_update(slug, mode="review", auto_commit_policy="none")
MCP -> same scan pipeline
    -> records proposals only
    -> returns review queue

operator -> commit_match(proposal_id)
MCP -> validates and mutates one proposal at a time
```

Manual topic updates are useful when the operator wants cloud-level synthesis but does not want automatic commits. This should be the default for volatile or high-impact topics.

### Flow C: Parked Evidence Cleanup

```
operator -> get_flagged_evidence(slug)
operator/local model -> start_indicator_cleanup(slug, evidence_ids)
MCP -> cleanup session opened

local model -> propose_schema_extension(session_id, proposal)
MCP -> lint indicator shape + direction + reference-class rationale

operator/designer -> commit_schema_extension(session_id)
MCP -> add indicator if approved
    -> optionally fire against selected parked evidence through normal commit path
    -> close session
```

The cleanup session is where new schema enters. It should be impossible to add an indicator outside a session, and impossible for a newly added indicator to bypass the normal commit machinery.

### Flow D: Topic Design

```
designer -> create_topic_draft(config)
MCP -> draft saved outside active topics

designer/local model -> lint_topic_draft(draft_id)
MCP -> structural lint + prior lint + indicator shape lint

designer/local model -> redteam_topic_draft(draft_id)
MCP -> missing hypothesis / resolution ambiguity / prior anchor report

designer -> commit_topic_draft(draft_id)
MCP -> ACTIVE topic projection written
```

No news article should be in the prompt context when authoring priors unless it is explicitly part of the prior evidence packet. Otherwise the topic design inherits the same anchoring problem the governor is supposed to block.

### Flow D2: Hypothesis Proposal For Existing Topic

```
operator/local model -> propose_hypothesis(topic_slug, proposal)
MCP -> HypothesisProposalRecorded, no active mutation

operator/local model -> review_hypothesis_proposal(proposal_id)
MCP -> exhaustiveness/mutual-exclusion/resolution/prior-anchor review

designer/admin -> commit_hypothesis_proposal(proposal_id)
MCP -> either:
       - reject
       - spawn replacement topic
       - migrate active topic with explicit mass mapping
```

Default policy: spawn a replacement topic when hypotheses materially change. Active-topic migration is allowed only when the mapping is simple and calibration history remains interpretable.

### Flow E: Outcome Resolution/Admin

```
admin -> resolve_topic(slug, resolved_hypothesis, evidence_refs)
MCP -> verify resolution evidence
    -> record outcome
    -> compute Brier/calibration
    -> freeze topic or mark resolved
```

Admin flows should be intentionally inconvenient: separate role, explicit reason, audit event, and no cron access.

---

## State Model

Move from "topic JSON is the editable database" to "topic JSON is a projection":

- append-only event log: submitted articles, evidence entries, match proposals, commits, rejects, admin events
- canonical state: SQLite or JSONL-backed store controlled by the MCP server
- projections: current topic JSON, mirror/canvas topics, briefs, activity log

Minimum viable version can keep topic JSON as the canonical file, but only the MCP process may write it. The stronger version stores canonical events in SQLite and regenerates topic JSON projections.

Required event types:

- `ArticleSubmitted`
- `EvidenceParked`
- `IndicatorFired`
- `ObservableApplied`
- `SchemaGapFlagged`
- `SourceTrustUpdated`
- `GovernanceSnapshot`
- `TopicDraftCommitted`
- `OutcomeResolved`
- `AdminRepair`

Every event should include:

```
event_id, timestamp, actor_id, role, tool_name, input_hash,
topic_slug, evidence_ids, before_hash, after_hash, decision, rationale
```

---

## Search And Deliberation Placement

Use cloud operator for high-level synthesis and question design. Use local models for repetitive search/match debate where possible.

Proposed flow:

1. `run_scan()` builds search mandates from topic hypotheses plus wildcard.
2. Search workers retrieve articles and dedupe by URL/canonical headline.
3. Local matcher proposes `OBSERVE/FIRE/PARK/SCHEMA_GAP`.
4. Optional local advocate/rebut/jury only changes proposal confidence, not state.
5. MCP validates the final proposal.
6. MCP commits valid mutations and parks/rejects the rest.
7. Cloud operator receives a digest: commits, parked items, schema gaps, governance health, open review queue.

The cloud operator can ask for additional search, propose a match, or approve a schema cleanup. It cannot edit files or directly set probabilities.

---

## Validation Rules

`commit_match()` must enforce:

- article/evidence exists and has stable ID
- topic exists and is active
- action is one of `OBSERVE`, `FIRE`, `PARK`, `SCHEMA_GAP`
- `FIRE` indicator exists, is eligible, and is not a stale duplicate
- `OBSERVE` indicator has an observable block and numeric value in native units
- proposal includes directional rationale
- no freeform likelihoods unless the indicator already contains them
- no explicit target posteriors except resolution-class/admin paths
- evidence has source, URL or provenance, tag, claim text, and timestamp
- same URL/canonical claim is not already committed
- same `causal_event_id` is attenuated or rejected according to existing logic
- governor passes before posterior movement
- saved projection hash matches the in-memory post-commit hash

`commit_topic_draft()` must enforce:

- hypotheses are mutually exclusive enough to score
- hypotheses are collectively exhaustive enough to avoid untracked residual mass
- resolution criteria identify exactly one winning hypothesis or a declared tie/void rule
- priors sum to 1 and have an explanation
- non-uniform priors have adversarial review
- indicators reference observable states, not desired conclusions
- indicator likelihoods are bounded and calibrated or explicitly marked as operator judgment
- every indicator has shape review metadata

`commit_hypothesis_proposal()` must enforce:

- proposal states whether this is split, merge, add-residual, reframe, or replacement
- old-to-new probability mass mapping is explicit
- resolution criteria still produce a scoreable outcome
- calibration history impact is declared
- no evidence from the triggering scan is silently used as prior evidence unless explicitly cited
- red-team reviewed missing outcomes and ambiguous boundary cases
- operator/admin approval is present

`commit_schema_extension()` must enforce:

- cleanup session is active and scoped to specific parked evidence
- proposed indicator is general, not article-specific
- proposed LR/observable was reviewed before firing
- adding the indicator and firing it are logged as separate events
- if fired against old parked evidence, the history records that the schema was authored after the evidence arrived

---

## Red-Team Failure Modes

### Capability bypass

Failure: agent edits `topics/*.json` or imports internal functions.  
Mitigation: operational state outside repo write sandbox; MCP process is sole writer; file integrity check on startup and before each commit.

### Freeform posterior resurrection

Failure: old CLI paths continue accepting `--posteriors`.  
Mitigation: remove/deprecate runtime exposure; make `update_posteriors()` admin-only for resolved/backfill paths; add tests that active topics reject operator-posteriors.

### Search confirmation bias

Failure: per-hypothesis searchers hunt only supporting articles.  
Mitigation: keep bidirectional hypothesis mandate plus wildcard; log search prompts and returned articles; compare surfaced-via channels.

### Deliberation anchoring

Failure: advocate/rebut/jury sees prior proposals and rationalizes them.  
Mitigation: strict matcher first; debate only for parked candidates; fresh context per stage; final deterministic validation.

### Schema gap force-fit

Failure: relevant article is routed through the closest wrong-direction indicator.  
Mitigation: first-class `SCHEMA_GAP`; directional alignment field required; commit rejects wrong-direction proposals.

### Correlated evidence inflation

Failure: multiple outlets report one underlying event as independent evidence.  
Mitigation: require `causal_event_id`/`informationChain`; URL/source dedupe; use existing same-event LR attenuation; add scan-level bundling.

### Source laundering

Failure: secondary sources quote the same original source.  
Mitigation: article ingestion extracts primary source when possible; source trust uses minimum trust across cited sources; same-chain entries do not compound.

### Cron drift

Failure: scheduled local model slowly accumulates parked noise or repeated observations.  
Mitigation: sustained-observation guard; per-topic scan budget; no posterior movement without commit validation; daily digest of non-mutations.

### Admin misuse

Failure: resolution/backfill/replay tools are used as normal update tools.  
Mitigation: separate admin role, explicit reason, two-step confirmation, audit event, and no cron access.

---

## Migration Plan

### Phase 0: Freeze and inventory

- Mark `framework/update.py --posteriors`, `framework/run.sh update ... --posteriors`, and `framework/runner.py update --posteriors` as legacy.
- Add a short warning in docs: active runtime updates must use `pipeline.process_evidence()` or `apply_observation()`.
- Inventory every write path to `topics/`, `loom/topics/`, `canvas/topics/`, briefs, activity logs, and source DB.

### Phase 1: Build MCP wrapper around the good path

- Add `mcp_server.py` exposing read tools and `process_evidence`/`apply_observation`-backed mutation tools.
- Keep the existing topic JSON storage initially.
- Add an auth/role shim even if it is just local config at first.
- Add structured audit events for every MCP mutation.

### Phase 2: Replace cron

- Replace `cron-update.sh` with a call to `run_scan()` or a small scheduler script that calls MCP tools.
- Remove `Write` from agent-based cron. If an agent is used for search, its output is articles/proposals only.
- Emit daily digest JSON/Markdown for operator review.

### Phase 3: Remove direct posterior authority

- Make runtime `update_posteriors()` inaccessible through CLI/MCP for active topics.
- Keep only admin/resolution use cases, guarded by role and explicit event type.
- Add tests:
  - active topic rejects explicit posteriors through MCP
  - unmatched evidence parks
  - matched indicator updates via Bayes
  - observable applies partial LR
  - schema gap does not move posterior

### Phase 4: Move state behind the server

- Move canonical operational state to SQLite or append-only JSONL.
- Regenerate topic JSON as projections for dashboard/mirror.
- On startup, verify projection hashes against latest committed event.
- Treat manual projection edits as corruption requiring admin repair.

### Phase 5: Local deliberation service

- Add configurable local model backend for matcher/advocate/rebut/jury.
- Cache prompts, outputs, model name, and hashes.
- Let cloud operator request high-level reviews, but not direct commits.

### Phase 6: Hardening

- Add capability tests that simulate malicious operator requests:
  - "set H3 to 80%"
  - "edit the JSON"
  - "force-fire closest indicator"
  - "reuse yesterday's article"
  - "ignore contradiction and proceed"
- Add startup lint that fails if legacy writable scripts are still used by cron or docs.
- Add per-tool rate limits and scan budgets.

---

## Minimal Implementation Target

The smallest useful version is:

1. `mcp_server.py` with `get_topic`, `get_governance`, `submit_article`, `propose_match`, `commit_match`, `run_scan`.
2. Topic JSON still stored where it is, but operators run without filesystem write access.
3. `cron-update.sh` replaced by MCP calls.
4. Explicit posterior updates removed from normal CLI/docs.
5. Tests proving only indicator/observable commits can move active posteriors.

That gets the main safety property without waiting for the full event-sourced store.
