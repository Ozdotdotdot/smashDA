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

print_summary() {
  if [[ "$QUIET" -eq 1 ]]; then
    echo "States processed: ${#states[@]}"
    echo "Succeeded: $success_count"
    echo "Failed: ${#failed_states[@]}"
    if ((${#failed_states[@]} > 0)); then
      echo "Failed states: ${failed_states[*]}"
    fi
  fi
}

trap 'print_summary' EXIT

for state in "${states[@]}"; do
  if [[ "$QUIET" -eq 1 ]]; then
    if python run_report.py "${state}" \
      --months-back 1 \
      --min-entrants 32 \
      --filter-state "${state}" \
      --output "cheerio_${state}.csv" > /dev/null; then
      ((success_count++))
    else
      failed_states+=("$state")
      exit 1
    fi
  else
    echo "Starting report for ${state} at $(date)"
    python run_report.py "${state}" \
      --months-back 1 \
      --min-entrants 32 \
      --filter-state "${state}" \
      --output "cheerio_${state}.csv"
    echo "Finished ${state} at $(date)"
    echo
  fi
done

exit 0
