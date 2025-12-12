#!/usr/bin/env bash
set -euo pipefail

SUMMARY_ONLY=false

usage() {
  cat <<'EOF'
Usage: ./API-caller-script.sh [--summary]

  --summary   Suppress per-state and Python output, printing only the final summary.
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

states=(
  AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS
  MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
)

start_epoch=$(date +%s)
processed_states=()

for state in "${states[@]}"; do
  log "Starting report for ${state} at $(date)"
  run_command python run_report.py "${state}" \
    --months-back 3 \
    --min-entrants 32 \
    --filter-state "${state}" \
    --output "cheerio_${state}.csv"
  log "Finished ${state} at $(date)"
  log
  processed_states+=("$state")
done

end_epoch=$(date +%s)
duration=$((end_epoch - start_epoch))
echo "Summary: generated reports for ${#processed_states[@]} state(s) (${processed_states[*]}). Duration: ${duration}s."
