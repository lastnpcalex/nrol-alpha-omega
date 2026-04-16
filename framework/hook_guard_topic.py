"""
PreToolUse guard — blocks direct writes to topic JSON files.

Enforces the governor pipeline: all topic mutations must go through
engine.py (load_topic, add_evidence, update_posteriors, bayesian_update,
fire_indicator, save_topic). Direct file writes bypass the governor's
enrichment, contradiction detection, hallucination checklist, and
governance snapshots.

Reads Claude Code hook stdin JSON, checks if the tool call would write
to a topics/*.json file. If so, blocks the call and injects instructions
to use the engine instead.

Works for both Bash and Edit/Write tool calls.
"""

import sys
import json
import re


def check_bash_command(command: str) -> bool:
    """Return True if the bash command appears to write to a topic JSON file."""
    # Patterns that indicate writing to topic files
    # Match: python scripts that open/write topic files, json.dump, save, redirect to topics/
    topic_path_pattern = r'topics/.*\.json'

    if not re.search(topic_path_pattern, command):
        return False

    # Allow read-only operations
    read_only_patterns = [
        r'^cat\s',
        r'^head\s',
        r'^tail\s',
        r'^less\s',
        r'^more\s',
        r'^wc\s',
        r'json\.load\b(?!.*json\.dump)',  # json.load without json.dump
        r'^python.*-c\s+["\']from engine import',  # Using the engine is OK
        r'^python.*-c\s+["\']from governor import',  # Using the governor is OK
        r'^python.*engine\.py\b',  # Running engine.py directly is OK
        r'^python.*governor\.py\b',  # Running governor.py directly is OK
        r'^python.*framework/',  # Running framework scripts is OK
        r'^git\s',  # Git operations are OK
    ]
    for pat in read_only_patterns:
        if re.search(pat, command):
            return False

    # Patterns that indicate writing
    write_patterns = [
        r'json\.dump',
        r'\.write\(',
        r'open\(.*["\']w',
        r'>.*topics/',
        r'>>.*topics/',
        r'save\(',
        r'json_data\[',
        r'\[.evidenceLog.\]',
        r'\[.model.\]',
        r'\[.hypotheses.\]',
        r'\[.posteriors?\.\]',
        r'\[.meta.\]',
        r'\[.governance.\]',
        r'\[.indicators.\]',
    ]
    for pat in write_patterns:
        if re.search(pat, command):
            return True

    return False


def check_file_path(file_path: str) -> bool:
    """Return True if the file path is a topic JSON that shouldn't be edited directly."""
    if not file_path:
        return False
    # Normalize path separators
    normalized = file_path.replace('\\', '/')
    if '/topics/' in normalized and normalized.endswith('.json'):
        # Allow template
        if '_template.json' in normalized:
            return False
        # Allow design gate marker files
        if '.design_gate/' in normalized:
            return False
        return True
    return False


def main():
    try:
        d = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = d.get("tool_name", "")
    tool_input = d.get("tool_input", {})
    blocked = False

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if check_bash_command(command):
            blocked = True

    elif tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "")
        if check_file_path(file_path):
            blocked = True

    if blocked:
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Direct topic JSON write blocked by governor guard.",
                "additionalContext": (
                    "\n" + "=" * 60 + "\n"
                    "  GOVERNOR GUARD: DIRECT TOPIC WRITE BLOCKED\n"
                    + "=" * 60 + "\n"
                    "You attempted to write directly to a topic JSON file.\n"
                    "This bypasses the governor pipeline (enrichment, contradiction\n"
                    "detection, hallucination checklist, governance snapshots).\n\n"
                    "USE THE ENGINE INSTEAD. Run via Bash:\n\n"
                    "  python -c \"\n"
                    "  from engine import load_topic, add_evidence, save_topic\n"
                    "  from engine import fire_indicator, update_posteriors, bayesian_update\n"
                    "  from engine import suggest_likelihoods, triage_headline\n"
                    "  from governor import governance_report, check_update_proposal\n"
                    "  \n"
                    "  topic = load_topic('topic-slug')\n"
                    "  # ... use engine functions ...\n"
                    "  save_topic(topic)\n"
                    "  \"\n\n"
                    "Key functions:\n"
                    "  triage_headline(headline, source)  — route new information\n"
                    "  add_evidence(topic, entry)          — enriched evidence logging\n"
                    "  fire_indicator(topic, id, note, date) — fire an indicator\n"
                    "  suggest_likelihoods(topic, [ids])   — derive likelihoods from indicators\n"
                    "  bayesian_update(topic, likelihoods, reason, refs) — mechanical Bayes\n"
                    "  update_posteriors(topic, posteriors, reason, refs) — operator posteriors\n"
                    "  check_update_proposal(topic, posteriors, refs) — pre-commit gate\n"
                    "  governance_report(topic)            — full epistemic health\n"
                    "  save_topic(topic)                   — save with governance snapshot\n"
                    + "=" * 60 + "\n"
                ),
            }
        }
        print(json.dumps(result))
    # If not blocked, print nothing (allow by default)


if __name__ == "__main__":
    main()
