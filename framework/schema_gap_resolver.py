"""
Schema-gap resolver: closes the loop between matcher SCHEMA_GAP outputs
and schema extensions.

Without this loop, matcher SCHEMA_GAP outputs accumulate in
governance.flagged_schema_gaps but nothing happens to them. Articles
keep getting flagged scan after scan because the underlying gap is
never fixed.

This module:
  1. Clusters recent gaps by missing-direction pattern (cluster_gaps)
  2. Dispatches a fresh-context subagent to analyze the clusters and
     propose specific schema extensions (build_resolver_prompt + parse)
  3. Persists proposals to governance.proposed_schema_extensions for
     operator review
  4. On operator approval, applies via cleanup-session

Public:
  cluster_gaps(topic) -> list of {pattern, count, examples}
  build_resolver_prompt(topic, clusters) -> str
  parse_resolver_proposals(response_text) -> list of proposal dicts
  persist_proposals(slug, proposals) -> updated topic
  apply_proposal(slug, proposal) -> bool   # uses cleanup-session

Each proposal is one of:
  - extend_observable: change an existing indicator's observable params
  - add_new_indicator: author a new indicator (full cleanup-session flow)
"""

import re
from collections import defaultdict


def cluster_gaps(topic: dict, max_age_scans: int = 3) -> list:
    """
    Group recent flagged_schema_gaps by direction pattern.

    Returns list of {pattern, count, gap_examples} dicts. Patterns are
    derived from the missing_direction text using simple keyword
    bucketing (recovery/escalation/diplomatic/etc.).
    """
    gaps = topic.get("governance", {}).get("flagged_schema_gaps", []) or []
    if not gaps:
        return []

    # Crude clustering: bucket by keyword in missing_direction
    PATTERNS = [
        ("recovery_partial", ["recovery", "recovering", "rising from", "partial recovery"]),
        ("escalation_kinetic", ["kinetic", "mine", "strike", "vessel", "naval"]),
        ("diplomatic", ["diplomatic", "negotiation", "talks", "channel"]),
        ("economic", ["economic", "rial", "exports", "trade"]),
        ("operational", ["escort", "operation", "task force", "freedom"]),
        ("compliance", ["compliance", "agreement", "framework", "deal"]),
        ("other", []),
    ]

    clusters = defaultdict(list)
    for g in gaps:
        text = (g.get("missing_direction", "") + " " +
                g.get("matcher_reason", "") + " " +
                g.get("headline", "")).lower()
        matched = False
        for label, keywords in PATTERNS:
            if any(kw in text for kw in keywords):
                clusters[label].append(g)
                matched = True
                break
        if not matched:
            clusters["other"].append(g)

    return [
        {"pattern": label, "count": len(items), "gap_examples": items[:5]}
        for label, items in clusters.items()
        if items
    ]


def _compute_per_h_coverage(topic: dict) -> dict:
    """For each hypothesis, count observable indicators that push toward / away from it."""
    hypotheses = topic.get("model", {}).get("hypotheses", {}) or {}
    coverage = {h: {"toward": 0, "away": 0} for h in hypotheses}

    inds_block = topic.get("indicators") or {}
    all_inds = []
    for tier_list in (inds_block.get("tiers") or {}).values():
        if isinstance(tier_list, list):
            all_inds.extend(tier_list)
    all_inds.extend(inds_block.get("anti_indicators") or [])

    for ind in all_inds:
        ob = ind.get("observable")
        if not ob or ob.get("family") == "binary_event":
            continue
        lrs = ind.get("likelihoods") or {}
        if not lrs:
            continue
        try:
            favored = max(lrs.items(), key=lambda kv: float(kv[1]))[0]
            disfavored = min(lrs.items(), key=lambda kv: float(kv[1]))[0]
        except (TypeError, ValueError):
            continue
        if favored in coverage:
            coverage[favored]["toward"] += 1
        if disfavored in coverage:
            coverage[disfavored]["away"] += 1
    return coverage


def build_resolver_prompt(topic: dict, clusters: list) -> str:
    """
    Build a prompt asking a subagent to propose specific schema
    extensions for the clustered gaps. Includes current per-H coverage
    so the subagent can avoid proposals that worsen directional asymmetry.
    """
    meta = topic.get("meta", {})
    inds_block = topic.get("indicators") or {}
    all_inds = []
    for tier_list in (inds_block.get("tiers") or {}).values():
        if isinstance(tier_list, list):
            all_inds.extend(tier_list)
    all_inds.extend(inds_block.get("anti_indicators") or [])

    inds_summary = "\n".join(
        f"  {ind['id']}: LR={ind.get('likelihoods')} "
        f"observable={'YES' if 'observable' in ind else 'NO'}"
        for ind in all_inds
    )

    coverage = _compute_per_h_coverage(topic)
    coverage_lines = []
    for h, counts in coverage.items():
        coverage_lines.append(
            f"  {h}: toward={counts['toward']}, away={counts['away']}"
        )
    coverage_block = "\n".join(coverage_lines)

    cluster_lines = []
    for c in clusters:
        cluster_lines.append(f"\nCLUSTER: {c['pattern']} (n={c['count']})")
        for g in c["gap_examples"][:3]:
            cluster_lines.append(f"  - {g.get('headline','')[:80]}")
            cluster_lines.append(f"    missing: {g.get('missing_direction','')[:100]}")
    clusters_block = "\n".join(cluster_lines)

    return f"""You are the SCHEMA GAP RESOLVER for the NROL-AO Bayesian framework.

=== SYSTEM PURPOSE ===
The framework tracks calibrated probabilistic beliefs over uncertain
news/current-thing questions. News articles update posteriors via
pre-committed indicators with pre-committed LRs. When the matcher
flags SCHEMA_GAP, it means an article reported topic-relevant evidence
in a direction the schema has no observable for. Your job: propose
specific schema extensions that would let future articles in the same
pattern be processed correctly.

You may propose two kinds of extension:
  1. extend_observable: an existing indicator already favors the right
     direction; just needs an observable block (or its params adjusted)
  2. add_new_indicator: no existing indicator covers the direction;
     a new indicator is needed

Be CONSERVATIVE. Don't propose extensions that would update posteriors
on weak signals. The cluster's pattern shows what's repeatedly missed —
your proposals should give those patterns a *small* partial-LR home,
not full-strength updates on rhetoric.

=== TOPIC ===
Slug: {meta.get('slug', '?')}
Question: {meta.get('question', '?')}

=== EXISTING INDICATORS ===
{inds_summary}

=== CURRENT PER-H OBSERVABLE COVERAGE ===
{coverage_block}

CRITICAL: do NOT propose extensions that pile more observables onto an
already well-covered direction. The framework explicitly prevents
"one hypothesis becomes a magnet" patterns — if you propose extensions
that all favor a single H, you are amplifying existing bias. Each
proposal must:
  - identify which H it favors (its argmax-LR hypothesis)
  - explicitly justify why this proposal does not WORSEN the per-H
    asymmetry shown above
  - prefer proposals that lift undercovered hypotheses (toward count
    near zero)

A cluster of "anti-recovery" gaps may legitimately need an H4-direction
extension — but if H4 already has 4 observables and H3 has 0,
prioritize H3 coverage first OR propose a balanced pair of indicators
where one fires toward each direction.

=== GAP CLUSTERS TO ADDRESS ===
{clusters_block}

=== OUTPUT FORMAT (one block per proposal, no preamble) ===

PROPOSAL
KIND: extend_observable | add_new_indicator
TARGET: <indicator_id for extend_observable, or new id for add_new_indicator>
CLUSTER_ADDRESSED: <pattern label from above>
RATIONALE: <one sentence>
SCHEMA:
  desc: <indicator description if add_new_indicator; "<unchanged>" if extending>
  likelihoods: {{H1: ..., H2: ..., H3: ..., H4: ...}}    # only if add_new_indicator
  observable:
    metric: <topic-prefix:metric_name>
    family: logistic | count_event | binary_event
    threshold_value: <number>
    baseline: <number>
    direction: higher_strengthens | lower_strengthens
END

If a cluster has no clean fix (e.g., the missing pattern is genuinely
rhetorical not factual), output:

PROPOSAL
KIND: no_fix
CLUSTER_ADDRESSED: <pattern>
RATIONALE: <why no fix is appropriate>
END
"""


_PROPOSAL_BLOCK = re.compile(
    r"PROPOSAL\s*\n"
    r"KIND:\s*([a-z_]+)\s*\n"
    r"(?:TARGET:\s*([^\n]+)\n)?"
    r"CLUSTER_ADDRESSED:\s*([^\n]+)\n"
    r"RATIONALE:\s*([^\n]+)\n"
    r"((?:.|\n)*?)END",
    re.MULTILINE | re.DOTALL,
)


def parse_resolver_proposals(response_text: str) -> list:
    """Parse subagent output into proposal dicts."""
    out = []
    for m in _PROPOSAL_BLOCK.finditer(response_text or ""):
        kind = m.group(1).strip().lower()
        target = (m.group(2) or "").strip()
        cluster = m.group(3).strip()
        rationale = m.group(4).strip()
        body = m.group(5) or ""
        out.append({
            "kind": kind,
            "target": target,
            "cluster_addressed": cluster,
            "rationale": rationale,
            "body": body.strip(),
        })
    return out


def _extract_lrs_from_proposal(proposal: dict) -> dict:
    """Parse the SCHEMA: block in a proposal body to extract likelihoods."""
    body = proposal.get("body", "")
    # likelihoods: {H1: 0.78, H2: 0.55, ...}
    m = re.search(r"likelihoods:\s*\{([^}]+)\}", body)
    if not m:
        return {}
    pairs = m.group(1)
    out = {}
    for kv in pairs.split(","):
        kv = kv.strip()
        if ":" in kv:
            k, v = kv.split(":", 1)
            try:
                out[k.strip()] = float(v.strip())
            except (TypeError, ValueError):
                pass
    return out


def _favored_h_of_existing(topic: dict, indicator_id: str) -> str:
    """Find the favored H of an existing indicator by its committed LRs."""
    inds_block = topic.get("indicators") or {}
    all_inds = []
    for tier_list in (inds_block.get("tiers") or {}).values():
        if isinstance(tier_list, list):
            all_inds.extend(tier_list)
    all_inds.extend(inds_block.get("anti_indicators") or [])
    for ind in all_inds:
        if ind.get("id") == indicator_id:
            lrs = ind.get("likelihoods") or {}
            if not lrs:
                return None
            try:
                return max(lrs.items(), key=lambda kv: float(kv[1]))[0]
            except (TypeError, ValueError):
                return None
    return None


def validate_proposals_balance(topic: dict, proposals: list) -> list:
    """
    For each proposal, mechanically determine which H it would fire toward
    and decide whether applying it would balance or worsen per-H coverage.

    Returns a list parallel to `proposals` with verdict added per item:
        verdict: "balanced" | "neutral" | "asymmetric_warning" | "unknown"
        favored_h: str | None
        reason: str

    The verdict is informational; operator + lint enforcement still gate
    actual application. But a proposal verdicted "asymmetric_warning"
    should not be auto-approved without explicit operator override.
    """
    coverage = _compute_per_h_coverage(topic)
    if not coverage:
        return [
            {**p, "verdict": "unknown", "favored_h": None,
             "reason": "no hypotheses found in topic"}
            for p in proposals
        ]

    # Identify undercovered H (toward-count == 0) and well-covered H (>= 3)
    undercovered = [h for h, c in coverage.items() if c["toward"] == 0]
    overcovered_threshold = 3
    overcovered = [h for h, c in coverage.items() if c["toward"] >= overcovered_threshold]

    annotated = []
    for p in proposals:
        kind = p.get("kind", "")
        favored_h = None

        if kind == "no_fix":
            annotated.append({
                **p, "verdict": "neutral", "favored_h": None,
                "reason": "no_fix proposal — no schema change",
            })
            continue

        if kind == "add_new_indicator":
            lrs = _extract_lrs_from_proposal(p)
            if lrs:
                try:
                    favored_h = max(lrs.items(), key=lambda kv: float(kv[1]))[0]
                except (TypeError, ValueError):
                    favored_h = None

        elif kind == "extend_observable":
            target = p.get("target", "").strip()
            favored_h = _favored_h_of_existing(topic, target)

        if favored_h is None:
            annotated.append({
                **p, "verdict": "unknown", "favored_h": None,
                "reason": f"could not determine favored H from kind={kind!r}",
            })
            continue

        # Decision logic
        if favored_h in undercovered:
            verdict = "balanced"
            reason = (
                f"proposal favors {favored_h} which is currently undercovered "
                f"(toward=0). Lifts directional balance."
            )
        elif favored_h in overcovered:
            verdict = "asymmetric_warning"
            reason = (
                f"proposal favors {favored_h} which already has "
                f"{coverage[favored_h]['toward']} observables toward it; "
                f"adding another amplifies bias. Operator must explicitly "
                f"justify, or the proposal should be rejected."
            )
        else:
            verdict = "neutral"
            reason = (
                f"proposal favors {favored_h} (toward count "
                f"{coverage[favored_h]['toward']}). Doesn't worsen "
                f"asymmetry but doesn't lift undercovered H either."
            )

        annotated.append({
            **p, "verdict": verdict, "favored_h": favored_h,
            "reason": reason,
        })

    return annotated


def persist_proposals(slug: str, proposals: list, validated: bool = True) -> dict:
    """
    Stamp proposals onto governance.proposed_schema_extensions for operator
    review.

    If `validated=True` (default), runs validate_proposals_balance first
    so each proposal gets a verdict marker. asymmetric_warning proposals
    are persisted but flagged so operator review can catch them.
    """
    from engine import load_topic, save_topic
    from datetime import datetime, timezone
    topic = load_topic(slug)
    if validated:
        proposals = validate_proposals_balance(topic, proposals)

    gov = topic.setdefault("governance", {})
    queue = gov.setdefault("proposed_schema_extensions", [])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for p in proposals:
        p["proposed_at"] = now
        # asymmetric_warning proposals require explicit operator override
        if p.get("verdict") == "asymmetric_warning":
            p["status"] = "pending_operator_review_asymmetric_flag"
        else:
            p["status"] = "pending_operator_review"
        queue.append(p)
    save_topic(topic)
    return topic


def should_dispatch_resolver(topic: dict, threshold: int = 3) -> tuple:
    """
    Decide whether news-scan should auto-dispatch the resolver after this
    apply phase.

    Returns (should_dispatch: bool, reason: str). True if accumulated
    flagged_schema_gaps exceeds threshold AND there are no pending
    proposals already awaiting operator review (avoid double-dispatch).
    """
    gov = topic.get("governance", {}) or {}
    gaps = gov.get("flagged_schema_gaps", []) or []
    pending = [
        p for p in (gov.get("proposed_schema_extensions", []) or [])
        if p.get("status", "").startswith("pending_operator_review")
    ]
    if pending:
        return False, f"{len(pending)} proposals already pending operator review; resolve those first"
    if len(gaps) >= threshold:
        return True, f"flagged_schema_gaps={len(gaps)} >= threshold={threshold}; auto-dispatch resolver"
    return False, f"flagged_schema_gaps={len(gaps)} below threshold={threshold}"


def list_pending_proposals(slug: str) -> list:
    """Return proposals not yet approved/rejected."""
    from engine import load_topic
    topic = load_topic(slug)
    queue = topic.get("governance", {}).get("proposed_schema_extensions", []) or []
    return [p for p in queue if p.get("status") == "pending_operator_review"]
