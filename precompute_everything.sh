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

ts() {
  date --iso-8601=seconds
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
    printf '[%s] %s\n' "$(ts)" "$1"
  fi
}

declare -a TASK_SUMMARY=()
SCRIPT_START=$(date +%s)

print_summary() {
  local exit_code=$1
  local end_time
  end_time=$(date +%s)
  local duration=$((end_time - SCRIPT_START))
  local status="SUCCESS"
  if [[ $exit_code -ne 0 ]]; then
    status="FAILURE"
  fi

  printf '[%s] precompute_everything summary: status=%s duration=%ss\n' "$(ts)" "$status" "$duration"
  if ((${#TASK_SUMMARY[@]} > 0)); then
    for line in "${TASK_SUMMARY[@]}"; do
      printf ' - %s\n' "$line"
    done
  fi
}

trap 'print_summary $?' EXIT

if [[ "$QUIET" -eq 0 ]]; then
  log "Using Python: $VENV_PYTHON"
  "$VENV_PYTHON" --version
else
  "$VENV_PYTHON" --version > /dev/null
fi

run_task() {
  local desc=$1
  shift
  local summary_line=""

  if [[ "$QUIET" -eq 1 ]]; then
    printf '[%s] %s\n' "$(ts)" "$desc"
    if summary_line=$("$VENV_PYTHON" "$@" | awk '/Finished processing/{line=$0} END{print line}'); then
      summary_line=${summary_line:-"Finished without summary output."}
      TASK_SUMMARY+=("$desc -> $summary_line")
      printf '[%s] %s\n' "$(ts)" "$summary_line"
    else
      TASK_SUMMARY+=("$desc -> FAILED (see log)")
      printf '[%s] %s\n' "$(ts)" "Task failed: $desc"
      exit 1
    fi
  else
    log "$desc"
    "$VENV_PYTHON" "$@"
  fi
}

printf '[%s] precompute_everything: starting tasks\n' "$(ts)"

run_task "Running precompute_metrics.py for all states (1 months back)..." \
  precompute_metrics.py --all-states --months-back 1 --offline-only

run_task "Running precompute_metrics.py for all states (1 months back, auto-series)..." \
  precompute_metrics.py --all-states --months-back 1 --auto-series --offline-only

log "Done."
