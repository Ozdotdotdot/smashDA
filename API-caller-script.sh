#!/usr/bin/env bash
  set -euo pipefail

  states=(
    AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS
    MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
  )

  for state in "${states[@]}"; do
    echo "Starting report for ${state} at $(date)"
    python run_report.py "${state}" \
      --months-back 1 \
      --min-entrants 32 \
      --filter-state "${state}" \
      --output "cheerio_${state}.csv"
    echo "Finished ${state} at $(date)"
    echo
  done