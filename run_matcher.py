#!/usr/bin/env python
"""Run matcher subagent and apply decisions through engine.

Usage:
    python run_matcher.py

The matcher prompt is read from matcher_prompt_hormuz_may12.txt (written by matcher_hormuz_may12.py).
This script:
1. Reads the matcher prompt
2. Prints instructions for the operator to dispatch the subagent
3. Provides a parse_and_apply helper for after subagent returns
"""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import load_topic
from framework.news_observation_pipeline import parse_matcher_output, apply_decisions

# Read the matcher prompt
prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'matcher_prompt_hormuz_may12.txt')
if not os.path.exists(prompt_path):
    print(f"ERROR: {prompt_path} not found. Run matcher_hormuz_may12.py first.")
    sys.exit(1)

with open(prompt_path, 'r', encoding='utf-8') as f:
    matcher_prompt = f.read()

print(f"Matcher prompt ready: {len(matcher_prompt)} chars")
print(f"File: {prompt_path}")
print(f"\nDispatch this as the Agent prompt for the matcher subagent.")
print(f"The subagent will return formatted DECISION blocks.")
print(f"\nTo apply decisions after subagent returns:")
print(f"    python apply_matcher_results.py '<path_to_subagent_output.txt>'")

# If a subagent output file is provided as argument, parse and apply
if len(sys.argv) > 1:
    subagent_output_path = sys.argv[1]
    with open(subagent_output_path, 'r') as f:
        subagent_output = f.read()

    decisions = parse_matcher_output(subagent_output)
    print(f"\nParsed {len(decisions)} decisions:")
    for d in decisions:
        action = d.get('action', 'UNKNOWN')
        article = d.get('article', '?')
        print(f"  {article}: {action}")

    slug = 'calibration-hormuz-reopen-2027'
    results = apply_decisions(slug, decisions, lens='RED')
    print(f"\nApply complete: {len(results)} results")
    for r in results:
        status = 'FIRED' if r.get('fired_indicator_id') else 'PARKED' if r.get('parked') else 'IGNORED'
        ev_id = r.get('evidence_id', '?')
        print(f"  [{status}] {ev_id}: {r.get('evidence_text', '')[:80]}...")

    # Show final posteriors
    topic = load_topic(slug)
    print(f"\nFinal posteriors:")
    for k, v in topic['model']['hypotheses'].items():
        print(f"  {k}: {v['posterior']:.4f}")
