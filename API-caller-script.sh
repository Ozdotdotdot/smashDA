#!/usr/bin/env bash
set -euo pipefail

QUIET=0

usage() {
  cat <<'EOF'
Usage: API-caller-script.sh [--quiet]

Options:
  -q, --quiet   Suppress per-state output and emit a concise summary on completion.
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

states=(
  AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS
  MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
)

declare -i success_count=0
declare -a failed_states=()
SCRIPT_START=$(date +%s)

log() {
  if [[ "$QUIET" -eq 0 ]]; then
    printf '[%s] %s\n' "$(ts)" "$1"
  fi
}

print_summary() {
  local exit_code=$1
  local end_time
  end_time=$(date +%s)
  local duration=$((end_time - SCRIPT_START))
  local status="SUCCESS"
  if [[ $exit_code -ne 0 || ${#failed_states[@]} -gt 0 ]]; then
    status="FAILURE"
  fi

  printf '[%s] API-caller summary: status=%s duration=%ss states=%d succeeded=%d failed=%d\n' \
    "$(ts)" "$status" "$duration" "${#states[@]}" "$success_count" "${#failed_states[@]}"

  if ((${#failed_states[@]} > 0)); then
    printf 'Failed states: %s\n' "${failed_states[*]}"
  fi
}

trap 'print_summary $?' EXIT

printf '[%s] API-caller: starting run for %d states\n' "$(ts)" "${#states[@]}"

for state in "${states[@]}"; do
  log "Starting report for ${state}"
  if python run_report.py "${state}" \
    --months-back 1 \
    --min-entrants 32 \
    --filter-state "${state}" \
    --output "cheerio_${state}.csv" >"$([[ "$QUIET" -eq 1 ]] && echo /dev/null || echo /dev/stdout)"; then
    ((success_count++))
    log "Finished ${state}"
  else
    failed_states+=("$state")
  fi
done

if ((${#failed_states[@]} > 0)); then
  exit 1
fi
