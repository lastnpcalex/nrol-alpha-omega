#!/bin/bash
# NRL-Alpha Omega — Cron Entry Point
# Usage: cron-update.sh <topic-slug> [mode]
# Modes: overnight|midday|friday-dump|sunday-futures|weekend|alert

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SLUG="${1:?Usage: cron-update.sh <topic-slug> [mode]}"
MODE="${2:-routine}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cd "$SCRIPT_DIR"

claude -p "You are the NRL-Alpha Omega Bayesian Estimator. \
  Read SPEC.md for the analytical framework. \
  Load the topic state from topics/${SLUG}.json. \
  Mode: $MODE. Current time: $TIMESTAMP. \
  Search for the latest news using the search queries defined in the topic config. \
  Check indicators against new evidence. \
  Assess whether posteriors need updating — only move on evidence, not vibes. \
  Tag all evidence with provenance. \
  Write a briefing to briefs/${SLUG}/ and save updated state. \
  Be concise. Actions over rhetoric. Don't front-run the model." \
  --allowedTools "WebSearch,Read,Write" \
  2>&1 | tee -a "logs/cron-${SLUG}-$(date +%Y%m%d).log"
