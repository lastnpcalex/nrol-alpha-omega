"""
PostToolUse audit hook — logs edits to enforcement code.

Fires after Edit/Write tool calls. If the touched file is part of the
framework's enforcement layer (engine.py, governor.py, framework/*.py,
hook_*.py, skills/*.md), appends an entry to canvas/activity-log.json
with action=FRAMEWORK_CODE_EDIT.

Purpose: make code-rewrite bypass attempts visible. The engine gates
(bayesian_update requires indicator_id, add_indicator requires active
session, save_topic catches direct indicator manipulation) prevent the
common bypass paths. The remaining attack vector is "AI edits engine.py
to weaken a gate, then exploits the weakened code." This hook makes
those edits conspicuous in the audit feed.

Does NOT block — only records. The PreToolUse layer (hook_guard_topic.py)
handles the actual blocking. This hook is for AFTER-THE-FACT visibility.

Reads Claude Code hook stdin JSON. Always returns success — never blocks.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent  # NROL-AO/
ACTIVITY_LOG = REPO_ROOT / "canvas" / "activity-log.json"

# Files whose edits should be audited (relative-path patterns).
ENFORCEMENT_PATTERNS = [
    r"temp-repo[\\/]engine\.py$",
    r"temp-repo[\\/]governor\.py$",
    r"temp-repo[\\/]framework[\\/].*\.py$",
    r"temp-repo[\\/]skills[\\/].*\.md$",
    r"temp-repo[\\/]scripts[\\/].*\.py$",
]


def _is_enforcement_file(file_path: str) -> bool:
    """Match the file against enforcement patterns."""
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    for pat in ENFORCEMENT_PATTERNS:
        if re.search(pat, file_path) or re.search(pat.replace(r"[\\/]", "/"), norm):
            return True
    return False


def _classify_severity(file_path: str, change_summary: str = "") -> str:
    """
    Assign severity to the audit entry.

    HIGH: edits to bayesian_update / add_indicator / save_topic gates,
          or to hook_guard_topic.py — these directly weaken enforcement
    MEDIUM: other engine.py / framework/ edits
    LOW: skills/*.md edits (documentation, doesn't change enforcement)
    """
    norm = file_path.replace("\\", "/").lower()
    if any(x in norm for x in ("hook_guard", "hook_audit")):
        return "HIGH"
    if "engine.py" in norm or "governor.py" in norm:
        return "HIGH"
    if "/framework/" in norm and norm.endswith(".py"):
        return "MEDIUM"
    if "/skills/" in norm:
        return "LOW"
    return "MEDIUM"


def _append_audit_entry(file_path: str, tool_name: str) -> None:
    """Append a FRAMEWORK_CODE_EDIT entry to activity-log.json."""
    severity = _classify_severity(file_path)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "FRAMEWORK_CODE_EDIT",
        "topic": "*",
        "summary": (
            f"AI edited enforcement code: {file_path} (via {tool_name}). "
            f"Severity: {severity}. "
            f"Edits to enforcement code can weaken governor gates; review the diff "
            f"to confirm the change is legitimate framework work and not a gate-bypass."
        ),
        "source": "hook_audit_framework_edits",
        "platform": "harness_hook",
        "route": "FRAMEWORK_CODE_EDIT",
        "file_path": file_path,
        "tool_name": tool_name,
        "severity": severity,
        "notes": "Automatic audit log entry. No action taken; visibility-only.",
    }

    # Read existing log, append, write back. Atomic enough for single-process use.
    try:
        if ACTIVITY_LOG.exists():
            with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        else:
            log_data = {"_schema": "Activity log", "entries": []}
        log_data.setdefault("entries", []).append(entry)
        with open(ACTIVITY_LOG, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        # Don't fail the hook on log-write error. Print to stderr.
        print(f"[hook_audit_framework_edits] log write failed: {e}", file=sys.stderr)


def main():
    try:
        d = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = d.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        return

    file_path = (
        d.get("tool_input", {}).get("file_path", "")
        or d.get("tool_response", {}).get("filePath", "")
    )
    if not _is_enforcement_file(file_path):
        return

    _append_audit_entry(file_path, tool_name)

    # Inject a small reminder to the AI's context — visibility helps the AI
    # itself notice the audit
    severity = _classify_severity(file_path)
    msg = (
        f"\n[FRAMEWORK_CODE_EDIT logged] {file_path} edited. "
        f"Severity: {severity}. "
        f"This edit is recorded in canvas/activity-log.json for operator audit. "
        f"Confirm the change is legitimate framework work, not a gate-bypass."
    )
    out = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        },
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
