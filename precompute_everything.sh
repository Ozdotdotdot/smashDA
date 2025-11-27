#!/usr/bin/env bash
set -euo pipefail

# Absolute path to the repo root
BASE_DIR="$HOME/code-repos/smashDA"
cd "$BASE_DIR"

# Point to the venv python explicitly
VENV_PYTHON="$BASE_DIR/.venv/bin/python"

echo "Using Python: $VENV_PYTHON"
"$VENV_PYTHON" --version

echo "Running precompute_metrics.py for all states (3 months back)..."
"$VENV_PYTHON" precompute_metrics.py --all-states --months-back 3

echo "Running precompute_metrics.py for all states (3 months back, auto-series)..."
"$VENV_PYTHON" precompute_metrics.py --all-states --months-back 3 --auto-series

echo "Done."
