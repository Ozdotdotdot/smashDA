#!/usr/bin/env bash
set -euo pipefail

readonly REPO_DIR="/opt/smashDA"
readonly LOG_FILE="/var/log/smashapi/refresh.log"

export VIRTUAL_ENV="$REPO_DIR/.venv"
export PATH="$VIRTUAL_ENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

cd "$REPO_DIR"

{
  echo "[$(date --iso-8601=seconds)] refresh: START"
  "$REPO_DIR/API-caller-script.sh" --summary
  "$REPO_DIR/precompute_everything.sh" --summary
  echo "[$(date --iso-8601=seconds)] refresh: DONE"
  echo
} >>"$LOG_FILE" 2>&1
