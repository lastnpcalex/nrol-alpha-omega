"""
PostToolUse hook wrapper — reads Claude Code hook stdin JSON,
extracts file path, runs governor check if it's a topic JSON.

Two paths:
  1. Runtime edit → post_edit_check.py validates invariants → additionalContext
  2. New topic detected → post_edit_check.py runs design gate → if mechanical
     checks pass, injects adversarial review prompt as a MANDATORY directive
     in additionalContext. Claude MUST run it as a subagent and parse the
     result through parse_review_response() before the topic is active.

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
        timeout=15,
    )

    stderr_msg = r.stderr.strip()

    # Check if stdout contains design gate JSON
    gate_result = None
    if r.stdout.strip():
        try:
            gate_result = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            pass

    if gate_result and gate_result.get("design_gate"):
        slug = gate_result.get("slug", "unknown")

        if not gate_result.get("mechanical_passed"):
            # Mechanical checks failed — inject blockers as hard stop
            blockers = gate_result.get("blockers", [])
            context = (
                f"\n{'='*60}\n"
                f"  DESIGN GATE BLOCKED: {slug}\n"
                f"{'='*60}\n"
                f"This is a NEW TOPIC that failed mechanical design checks.\n"
                f"You MUST fix these blockers before this topic can enter the system:\n\n"
            )
            for b in blockers:
                context += f"  [X] {b}\n"
            context += (
                f"\n{'='*60}\n"
                f"Fix the blockers, then save the topic again to re-run the gate.\n"
            )

            out = {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": context,
                },
            }
            print(json.dumps(out))
            return

        # Mechanical passed — inject adversarial review as mandatory directive
        review_prompt = gate_result.get("review_prompt")
        warnings = gate_result.get("warnings", [])

        context = (
            f"\n{'='*60}\n"
            f"  DESIGN GATE — NEW TOPIC: {slug}\n"
            f"{'='*60}\n"
            f"Mechanical checks PASSED.\n"
        )

        if warnings:
            context += "\nWarnings (should fix):\n"
            for w in warnings:
                context += f"  [!] {w}\n"

        if review_prompt:
            context += (
                f"\n{'='*60}\n"
                f"  MANDATORY ADVERSARIAL REVIEW\n"
                f"{'='*60}\n"
                f"You MUST run the following as an Agent subagent call before\n"
                f"this topic is considered ACTIVE. Do not log evidence, update\n"
                f"posteriors, or report this topic as ready until the adversarial\n"
                f"review has been executed and all FAIL verdicts addressed.\n"
                f"\n"
                f"1. Launch Agent with the review prompt below (use model: sonnet)\n"
                f"2. Parse the response with parse_review_response() from\n"
                f"   framework/topic_design_gate.py\n"
                f"3. If OVERALL: FAIL, address all blocking objections\n"
                f"4. If OVERALL: PASS, mark the gate passed by creating:\n"
                f"   topics/.design_gate/{slug}.passed\n"
                f"\n"
                f"--- BEGIN REVIEW PROMPT ---\n"
                f"{review_prompt}\n"
                f"--- END REVIEW PROMPT ---\n"
            )

        out = {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            },
        }
        print(json.dumps(out))
        return

    # Normal runtime check — pass through stderr
    out = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": stderr_msg,
        },
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
