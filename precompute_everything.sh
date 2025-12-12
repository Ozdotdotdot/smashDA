#!/usr/bin/env bash
set -euo pipefail

QUIET=0

usage() {
  cat <<'EOF'
Usage: precompute_everything.sh [--quiet]

Options:
  -q, --quiet   Suppress detailed output and emit a concise summary.
  -h, --help    Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -q|--quiet)
      QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

# Absolute path to the repo root
BASE_DIR="$HOME/code-repos/smashDA"
cd "$BASE_DIR"

# Point to the venv python explicitly
VENV_PYTHON="$BASE_DIR/.venv/bin/python"

log() {
  if [[ "$QUIET" -eq 0 ]]; then
    echo "$*"
  fi
}

declare -a TASK_SUMMARY=()

print_summary() {
  if [[ "$QUIET" -eq 1 && ${#TASK_SUMMARY[@]} -gt 0 ]]; then
    echo "Precompute summary:"
    for line in "${TASK_SUMMARY[@]}"; do
      echo " - $line"
    done
  fi
}

trap 'print_summary' EXIT

if [[ "$QUIET" -eq 0 ]]; then
  log "Using Python: $VENV_PYTHON"
  "$VENV_PYTHON" --version
else
  "$VENV_PYTHON" --version > /dev/null
fi

run_task() {
  local desc=$1
  shift
  if [[ "$QUIET" -eq 1 ]]; then
    if "$VENV_PYTHON" "$@" > /dev/null; then
      TASK_SUMMARY+=("$desc: OK")
    else
      TASK_SUMMARY+=("$desc: FAILED")
      exit 1
    fi
  else
    log "$desc"
    "$VENV_PYTHON" "$@"
  fi
}

run_task "Running precompute_metrics.py for all states (1 months back)..." \
  precompute_metrics.py --all-states --months-back 1 --offline-only

run_task "Running precompute_metrics.py for all states (1 months back, auto-series)..." \
  precompute_metrics.py --all-states --months-back 1 --auto-series --offline-only

log "Done."
