#!/usr/bin/env bash
set -euo pipefail

SUMMARY_ONLY=false

usage() {
  cat <<'EOF'
Usage: ./precompute_everything.sh [--summary]

  --summary   Suppress intermediate output, printing only the final summary.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --summary)
      SUMMARY_ONLY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

log() {
  if [[ "$SUMMARY_ONLY" != "true" ]]; then
    echo "$@"
  fi
}

run_command() {
  if [[ "$SUMMARY_ONLY" == "true" ]]; then
    "$@" >/dev/null
  else
    "$@"
  fi
}

# Absolute path to the repo root
BASE_DIR="$HOME/code-repos/smashDA"
cd "$BASE_DIR"

# Point to the venv python explicitly
VENV_PYTHON="$BASE_DIR/.venv/bin/python"

start_epoch=$(date +%s)

log "Using Python: $VENV_PYTHON"
run_command "$VENV_PYTHON" --version

log "Running precompute_metrics.py for all states (3 months back)..."
run_command "$VENV_PYTHON" precompute_metrics.py --all-states --months-back 3 --offline-only

log "Running precompute_metrics.py for all states (3 months back, auto-series)..."
run_command "$VENV_PYTHON" precompute_metrics.py --all-states --months-back 3 --auto-series --offline-only

end_epoch=$(date +%s)
duration=$((end_epoch - start_epoch))
echo "Summary: completed precompute_everything in ${duration}s using ${VENV_PYTHON}."
