# Skill: Meta-Health Check

Periodic structured review of framework + topic health. Cyborgist
(metrics + subagent debate + operator review). Outputs a single JSON
report; doesn't trigger any cleanups, doesn't mutate topic state.
Operator reads, decides, runs cleanup workflow separately if needed.

## When to use

- Operator clicks "Run new health check" on the canvas HEALTH tab
- Anything that triggers `begin_meta_health` action via review-action.md
- Periodic cadence (weekly is reasonable — this is a "look at the system" loop)

Do **not** run this skill from inside a cleanup-sweep conversation.
Meta-health and cleanup are separate workflows; conflating them
re-introduces context anchoring.

## Steps

### 1. Compute metrics

```python
from framework.meta_health import compute_full_health_report
report = compute_full_health_report()
```

Returns a dict with:
- `timestamp`
- `metrics.system`: aggregate counts (topics by status / health / saturation,
  cleanup activity, framework code edits, bypass attempts, lens calibration)
- `metrics.topics`: per-topic row (slug, max_p, saturation, indicator
  schema completeness, parked count, lens stamping rate, legacy freeform
  debt, direction drift, etc.)

The skill is read-only on topics. No edits.

### 2. Dispatch red and blue team subagents in parallel

Build prompts inline. The metrics dump goes into both prompts as context.
Each team has a budget of 5 turns and 5 web searches; spell that out so
the subagent doesn't blow past it.

**Red team** argues "the system has a meta-failure these metrics aren't
catching." Specifically: P(unhealthy framework | observed signals) is
non-trivial; what's the strongest case for that?

**Blue team** argues "the metrics are calibrated; the system is working
as designed."

Both teams have native access to: Bash (which can run
`python -c "from framework.topic_search import search_evidence; ..."`),
WebSearch, WebFetch, Read. Use them.

```python
red_prompt = f\"\"\"You are the META-HEALTH RED TEAM. Argue the framework
has a failure that the structured metrics below are missing or
under-counting.

Look for things like:
  - Topics whose posteriors look fine but were derived through paths the
    metrics don't audit (e.g. legacy freeform that the framework now
    counts as 'debt' but operators may interpret as resolved)
  - Indicator schemas that look complete (LRs filled) but with
    direction-wrong or magnitude-wrong values
  - Calibration data that's accumulating in ways that overrate one
    lens or topic
  - Bypass attempts that aren't being captured in the audit trail
  - Schema gaps where new evidence keeps parking but indicators aren't
    being authored to cover the gap (operator bandwidth issue masked as
    healthy parking)
  - Disagreements between governance.health and actual posterior state

Use topic_search and WebSearch. Cite specific findings. If you can't
find a meta-failure, say so honestly — don't invent objections.

Budget: 5 turns, 5 web searches. After your investigation, output JSON:

```json
{{
  "verdict": "STRONG_CONCERN" | "WEAK_CONCERN" | "NO_CONCERN",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "claims": [
    {{"claim": "...", "evidence": "...", "source": "..."}}
  ],
  "summary": "..."
}}
```

Metrics dump:
{json.dumps(report['metrics'], indent=2)}
\"\"\"

blue_prompt = f\"\"\"You are the META-HEALTH BLUE TEAM. Defend the metrics
interpretation. Argue the system is working as designed and the
indicators of issues are appropriately surfacing the right concerns.

Look for confirming evidence:
  - Bypass attempts being correctly captured + blocked
  - Cleanup activity proportional to surfaced debt
  - Lens calibration accumulating where appropriate
  - Saturation flags catching the topics they should

Use topic_search and WebSearch. Cite specific findings. If the system
has visible gaps the metrics surface honestly, acknowledge them — your
job is to defend the metrics' calibration, not pretend nothing is wrong.

Budget: 5 turns, 5 web searches. Output JSON same shape as the red
team's verdict but with verdict values:

```json
{{
  "verdict": "STRONG_DEFENSE" | "WEAK_DEFENSE" | "PARTIAL_DEFENSE",
  ...
}}
```

Metrics dump:
{json.dumps(report['metrics'], indent=2)}
\"\"\"
```

Spawn both subagents in **the same message** so they execute concurrently:

```
Agent(description="meta-health red team", prompt=red_prompt,
      subagent_type="general-purpose")
Agent(description="meta-health blue team", prompt=blue_prompt,
      subagent_type="general-purpose")
```

This is the anti-anchoring step. Each team has a fresh context; you do
not reason through their positions in your own thread.

### 3. Parse responses

```python
import re, json as _json

def _extract_json(text):
    m = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    if not m:
        return {"_parse_error": "no json block"}
    for candidate in reversed(m):
        try:
            return _json.loads(candidate)
        except _json.JSONDecodeError:
            continue
    return {"_parse_error": "json unparseable"}

red_report = _extract_json(red_response_text)
blue_report = _extract_json(blue_response_text)
```

### 4. Generate recommended_actions

Based on the red/blue debate + the raw metrics, produce a list of
operator-facing recommendations. Format:

```python
recommended_actions = [
    {
        "priority": "high" | "medium" | "low",
        "action": "<short verb-led action>",
        "target": "<topic slug or 'system' or 'schema:<slug>'>",
        "reason": "<one sentence why>",
    },
    ...
]
```

Examples (don't write these specifics — generate from actual metrics):
- "high / run cleanup-indicator-sweep / target=fed-rate-2026 / reason=8 legacy
  freeform entries on a topic with full indicator schema"
- "medium / backfill indicator LRs / target=schema:ai-corporate-moat-2028 /
  reason=0/9 indicators have LRs; topic cannot fire indicators until backfilled"
- "low / red-team review of saturation / target=hormuz-closure / reason=
  posterior at 0.755 with direction drift flag; not pegged but worth scrutiny"

The recommendation is informational. The operator decides what to act on
and via which workflow.

### 5. Write the report + update index

```python
from datetime import datetime, timezone
from pathlib import Path

ts = datetime.now(timezone.utc).isoformat()
report["red_team"] = red_report
report["blue_team"] = blue_report
report["recommended_actions"] = recommended_actions

reports_dir = Path("canvas/meta-health-reports")
reports_dir.mkdir(parents=True, exist_ok=True)

filename = f"{ts.replace(':', '-')}.json"
report_path = reports_dir / filename
with open(report_path, "w", encoding="utf-8") as f:
    _json.dump(report, f, indent=2, ensure_ascii=False)

# Update _index.json so the canvas can list reports without directory access
index_path = reports_dir / "_index.json"
if index_path.exists():
    with open(index_path, "r", encoding="utf-8") as f:
        index_data = _json.load(f)
else:
    index_data = {"reports": []}

# Top-line summary for the index entry — small, fast for canvas to render
sys = report["metrics"]["system"]
index_data["reports"].append({
    "filename": filename,
    "timestamp": ts,
    "summary": {
        "topic_count": sys.get("topic_count"),
        "saturated_count": sys.get("saturated_count"),
        "with_legacy_freeform_debt_count": sys.get("with_legacy_freeform_debt_count"),
        "topics_by_health": sys.get("topics_by_health", {}),
        "red_verdict": red_report.get("verdict"),
        "blue_verdict": blue_report.get("verdict"),
        "n_recommended_actions": len(recommended_actions or []),
    },
})
# Keep sorted newest first
index_data["reports"].sort(key=lambda r: r.get("timestamp", ""), reverse=True)
index_data["_updated"] = ts

with open(index_path, "w", encoding="utf-8") as f:
    _json.dump(index_data, f, indent=2, ensure_ascii=False)
```

### 6. Surface to operator

Print a concise summary to chat:
- One-line system summary (e.g., "23 active topics, 18 saturated, 0 active cleanup sessions")
- Red team verdict + summary (one sentence each)
- Blue team verdict + summary
- Top 3 recommendations by priority

Then point at the report file path. Operator opens it / opens the canvas
HEALTH tab to read in detail.

Log a one-line activity entry:

```python
from framework.pipeline import log_activity
log_activity({
    "timestamp": ts,
    "action": "META_HEALTH_CHECK",
    "topic": "*",
    "summary": f"Meta-health check completed. Red verdict={red_report.get('verdict')}, "
               f"blue verdict={blue_report.get('verdict')}. "
               f"{len(recommended_actions)} recommendations. Report: {report_path}",
    "source": "meta-health-skill",
    "platform": "framework",
}, platform="framework")
```

### 7. Stop

The skill ends here. The skill does NOT:
- Mutate any topic
- Trigger any cleanup
- Mark itself as auto-suppressing alerts
- Run again on a schedule (cron jobs aren't this skill's concern)

If the operator wants to act on a recommendation, they trigger the
appropriate workflow separately (cleanup-indicator-sweep, schema
authoring, etc.).

## Budget enforcement

The 5-turn / 5-web-call budget is a soft constraint imposed by the
prompt. Subagents may exceed it; the skill doesn't enforce hard caps.
If subagent reports come back with empty findings (budget exhausted
before conclusion), record that as part of the report — operator sees
that the budget was insufficient and can re-run with a larger budget.

## Constraints

- This skill is **read-only on topics**. Do not call `start_indicator_cleanup_session`,
  do not call `bayesian_update`, do not call `add_indicator`. Even
  "harmless cleanup" of stale state is out of scope.
- Red and blue team **must be dispatched via Agent tool**. Do not run
  the analysis in your own context. The whole point is fresh-context-per-team
  to prevent the cross-context anchoring failure mode.
- Recommendations are advisory. Do not phrase them as commands; they are
  one-line suggestions the operator may or may not act on.
- One report per invocation. Don't run subagents twice or "re-check" within
  the same skill invocation.
