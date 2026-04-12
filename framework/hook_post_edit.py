"""
PostToolUse hook wrapper — reads Claude Code hook stdin JSON,
extracts file path, runs governor check if it's a topic JSON.

Returns JSON with additionalContext for model injection.
"""

import sys
import json
import subprocess
import os

SCRIPT = os.path.join(os.path.dirname(__file__), "post_edit_check.py")


def main():
    try:
        d = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    f = d.get("tool_input", {}).get("file_path", "") or d.get("tool_response", {}).get("filePath", "")

    if "/topics/" not in f and "\\topics\\" not in f:
        return
    if not f.endswith(".json"):
        return

    r = subprocess.run(
        ["python", SCRIPT, f],
        capture_output=True,
        text=True,
    )

    out = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": r.stderr.strip(),
        },
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
