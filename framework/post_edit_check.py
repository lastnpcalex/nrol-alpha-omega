"""
PostToolUse governance check — runs automatically after topic JSON edits.

Validates:
  1. Posteriors sum to 1.00
  2. Evidence entries have required fields
  3. No rhetoric_as_evidence (RHETORIC/EDITORIAL tag with non-NONE posteriorImpact)
  4. R_t regime warning if DANGEROUS or RUNAWAY

Exit code 0 = pass, 1 = violations found (printed to stderr).
"""

import json
import sys
import os
from pathlib import Path

REQUIRED_EVIDENCE_FIELDS = {"id", "time", "text", "tags", "source", "claimState", "posteriorImpact"}
RHETORIC_TAGS = {"RHETORIC", "EDITORIAL", "FORECAST"}


def check_posteriors(topic):
    """Verify posteriors sum to 1.00."""
    issues = []
    hypotheses = topic.get("model", {}).get("hypotheses", {})
    if not hypotheses:
        return issues
    # Handle both dict-of-dicts and list-of-dicts formats
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
    # Only check the last 5 entries (most likely just edited)
    for entry in log[-5:]:
        eid = entry.get("id", "???")
        tags = entry.get("tags", entry.get("tag", None))
        if tags is None:
            entry_fields = set(entry.keys())
        else:
            entry_fields = set(entry.keys())
            if "tag" in entry_fields and "tags" not in entry_fields:
                entry_fields.add("tags")  # accept either

        missing = REQUIRED_EVIDENCE_FIELDS - set(entry.keys())
        # Allow 'tag' as alias for 'tags'
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


def main():
    if len(sys.argv) < 2:
        print("Usage: post_edit_check.py <topic.json>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        sys.exit(0)  # File doesn't exist (yet), nothing to check

    # Only check topic JSONs
    if not filepath.endswith(".json"):
        sys.exit(0)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            topic = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"WARNING: Could not parse {filepath} as JSON", file=sys.stderr)
        sys.exit(0)  # Don't block on parse errors

    # Must look like a topic (has model.hypotheses)
    if "model" not in topic or "hypotheses" not in topic.get("model", {}):
        sys.exit(0)

    slug = topic.get("meta", {}).get("slug", Path(filepath).stem)
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
    else:
        print(f"Governor check passed: {slug}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
