#!/bin/bash
# NRL-Alpha Omega — Framework Runner Shell Script
# ================================================
#
# Usage:
#   ./run.sh update --topic hormuz-closure --mode routine
#   ./run.sh lint --topic hormuz-closure
#   ./run.sh test --topic hormuz-closure --test resolution_achieved
#   ./run.sh audit --topic hormuz-closure
#   ./run.sh diff --topic hormuz-closure
#   ./run.sh health --topic hormuz-closure
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python}"

usage() {
    echo "NRL-Alpha Omega Framework Runner"
    echo ""
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  update   Run update (routine or crisis mode)"
    echo "  lint     Lint evidence log"
    echo "  test     Run test case"
    echo "  audit    Epistemic audit"
    echo "  diff     Show diff from last brief"
    echo "  health   Show health status"
    echo ""
    echo "Examples:"
    echo "  $0 update --topic hormuz-closure --mode routine"
    echo "  $0 lint --topic hormuz-closure"
    echo "  $0 test --topic hormuz-closure --test resolution_achieved"
    echo ""
    exit 1
}

case "${1:-}" in
    update)
        shift
        echo "Running update..."
        $PYTHON "$SCRIPT_DIR/runner.py" update \
            --topic "${2:-hormuz-closure}" \
            --mode "${3:-routine}" \
            --posteriors "${4:-{}}" \
            --submodels "${5:-{}}" \
            --feeds "${6:-{}}"
        ;;
    lint)
        shift
        echo "Running lint..."
        $PYTHON "$SCRIPT_DIR/runner.py" lint \
            --topic "${2:-hormuz-closure}" \
            --check-history "${3:-}"
        ;;
    test)
        shift
        echo "Running test..."
        $PYTHON "$SCRIPT_DIR/runner.py" test \
            --topic "${2:-hormuz-closure}" \
            --test "${3:-}" \
            --evidence "${4:-{}}"
        ;;
    audit)
        shift
        echo "Running audit..."
        $PYTHON "$SCRIPT_DIR/runner.py" audit \
            --topic "${2:-hormuz-closure}"
        ;;
    diff)
        shift
        echo "Showing diff..."
        $PYTHON "$SCRIPT_DIR/runner.py" diff \
            --topic "${2:-hormuz-closure}"
        ;;
    health)
        shift
        echo "Showing health..."
        $PYTHON "$SCRIPT_DIR/runner.py" health \
            --topic "${2:-hormuz-closure}"
        ;;
    *)
        usage
        ;;
esac
