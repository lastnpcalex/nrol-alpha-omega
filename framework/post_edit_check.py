"""
PostToolUse governance check — runs automatically after topic JSON edits.

Two modes:
  1. RUNTIME checks (every edit): posteriors sum, evidence fields, rhetoric lint
  2. DESIGN GATE (new topics only): full mechanical checks + adversarial review
     prompt injection. Detected when posteriorHistory has <=1 entry and no
     governance snapshot exists.

Exit code 0 = pass, 1 = violations found (printed to stderr).
Design gate output goes to stdout as JSON for hook_post_edit.py to parse.
"""

import json
import sys
import os
from pathlib import Path

REQUIRED_EVIDENCE_FIELDS = {"id", "time", "text", "tags", "source", "claimState", "posteriorImpact"}
RHETORIC_TAGS = {"RHETORIC", "EDITORIAL", "FORECAST"}

# Marker file directory — tracks which topics have passed adversarial review
GATE_DIR = Path(__file__).parent.parent / "topics" / ".design_gate"


def check_posteriors(topic):
    """Verify posteriors sum to 1.00."""
    issues = []
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    if not hypotheses:
        return issues
    if isinstance(hypotheses, dict):
        vals = hypotheses.values()
    else:
        vals = hypotheses
    total = sum(h.get("posterior", 0) for h in vals if isinstance(h, dict))
    if abs(total - 1.0) > 0.005:
        issues.append(f"CRITICAL: Posteriors sum to {total:.4f}, not 1.00")
    return issues


def check_evidence_fields(topic):
    """Verify latest evidence entries have required fields."""
    issues = []
    log = topic.get("evidenceLog", [])
    for entry in log[-5:]:
        eid = entry.get("id", "???")
        tags = entry.get("tags", entry.get("tag", None))
        if tags is None:
            entry_fields = set(entry.keys())
        else:
            entry_fields = set(entry.keys())
            if "tag" in entry_fields and "tags" not in entry_fields:
                entry_fields.add("tags")

        missing = REQUIRED_EVIDENCE_FIELDS - set(entry.keys())
        if "tags" in missing and "tag" in entry:
            missing.discard("tags")
        if missing:
            issues.append(f"Evidence {eid}: missing fields {missing}")
    return issues


def check_rhetoric_lint(topic):
    """Flag rhetoric_as_evidence: RHETORIC/EDITORIAL with non-NONE posteriorImpact."""
    issues = []
    log = topic.get("evidenceLog", [])
    for entry in log[-10:]:
        tags = entry.get("tags", entry.get("tag", []))
        if isinstance(tags, str):
            tags = [tags]
        impact = entry.get("posteriorImpact", "NONE")
        if any(t in RHETORIC_TAGS for t in tags):
            if not impact.startswith("NONE"):
                eid = entry.get("id", "???")
                issues.append(f"RHETORIC_AS_EVIDENCE: {eid} has tag {tags} but posteriorImpact=\"{impact[:60]}\"")
    return issues


def is_new_topic(topic, slug):
    """Detect whether this is a new/unreviewed topic.

    The sole authority is the marker file: topics/.design_gate/{slug}.passed
    If the marker exists, the topic has cleared adversarial review.
    If it doesn't exist, we check heuristics to decide if the topic is
    genuinely new (vs. a legacy topic created before the gate existed).
    """
    # Already passed design gate
    if GATE_DIR.exists() and (GATE_DIR / f"{slug}.passed").exists():
        return False
    # Template file
    if slug in ("CHANGE-ME", "_template"):
        return False
    # Legacy topic detection: if it has >1 posteriorHistory entry or
    # evidence, it was created before the gate existed — don't block it.
    # New topics created through the proper flow will have exactly 1
    # posteriorHistory entry (the initial prior) and no evidence.
    history = topic.get("model", {}).get("posteriorHistory", [])
    if len(history) > 1:
        return False
    if len(topic.get("evidenceLog", [])) > 0:
        return False
    # This is a new, unreviewed topic
    return True


def run_design_gate_check(topic):
    """Run mechanical design gate checks. Returns (blockers, warnings, info, review_prompt_or_none)."""
    # Import here to avoid circular deps at module level
    framework_dir = str(Path(__file__).parent)
    if framework_dir not in sys.path:
        sys.path.insert(0, framework_dir)
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    from topic_design_gate import run_design_gate

    gate = run_design_gate(topic)
    mechanical = gate["mechanical"]
    review_prompt = gate.get("review_prompt")

    return mechanical["blockers"], mechanical["warnings"], mechanical["info"], review_prompt


def mark_gate_passed(slug):
    """Create marker file indicating this topic passed adversarial review."""
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    (GATE_DIR / f"{slug}.passed").write_text("")


def main():
    if len(sys.argv) < 2:
        print("Usage: post_edit_check.py <topic.json>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        sys.exit(0)

    if not filepath.endswith(".json"):
        sys.exit(0)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            topic = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"WARNING: Could not parse {filepath} as JSON", file=sys.stderr)
        sys.exit(0)

    if "model" not in topic or "hypotheses" not in topic.get("model", {}):
        sys.exit(0)

    slug = topic.get("meta", {}).get("slug", Path(filepath).stem)

    # ── Runtime checks (always run) ──
    issues = []
    issues.extend(check_posteriors(topic))
    issues.extend(check_evidence_fields(topic))
    issues.extend(check_rhetoric_lint(topic))

    if issues:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  GOVERNOR POST-EDIT CHECK: {slug}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        for issue in issues:
            print(f"  !! {issue}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        sys.exit(1)

    # ── Design gate (new topics only) ──
    if is_new_topic(topic, slug):
        print(f"New topic detected: {slug} — running design gate...", file=sys.stderr)

        blockers, warnings, info, review_prompt = run_design_gate_check(topic)

        # Build structured output for hook_post_edit.py to parse
        gate_result = {
            "design_gate": True,
            "slug": slug,
            "mechanical_passed": len(blockers) == 0,
            "blockers": blockers,
            "warnings": warnings,
        }

        if blockers:
            # Mechanical checks failed — block and report
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"  DESIGN GATE BLOCKED: {slug}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            for b in blockers:
                print(f"  [X] {b}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            gate_result["review_prompt"] = None
            print(json.dumps(gate_result))
            sys.exit(1)

        if warnings:
            print(f"Design gate warnings for {slug}:", file=sys.stderr)
            for w in warnings:
                print(f"  [!] {w}", file=sys.stderr)

        # Mechanical passed — include review prompt for subagent
        gate_result["review_prompt"] = review_prompt
        print(json.dumps(gate_result))
        print(f"Design gate mechanical checks PASSED for {slug}", file=sys.stderr)
        print(f"Adversarial review prompt generated — awaiting subagent execution", file=sys.stderr)
        sys.exit(0)

    # Existing topic, runtime checks passed
    print(f"Governor check passed: {slug}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
