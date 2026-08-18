#!/usr/bin/env bash
set -euo pipefail

readonly DB_PATH="${SMASHCC_DB_PATH:?SMASHCC_DB_PATH must be set}"
readonly BACKUP_DIR="/var/backups/smashapi"
readonly RETENTION_DAYS="${SMASHCC_BACKUP_RETENTION_DAYS:-70}"
readonly TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly FINAL_PATH="$BACKUP_DIR/smash-$TIMESTAMP.db.gz"

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database does not exist: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
temp_db="$(mktemp "$BACKUP_DIR/.smash-backup-XXXXXX.db")"

cleanup() {
  if [[ -n "${temp_db:-}" && -f "$temp_db" ]]; then
    rm -f -- "$temp_db"
  fi
}
trap cleanup EXIT

sqlite3 "$DB_PATH" ".timeout 30000" ".backup '$temp_db'"

if [[ "$(sqlite3 "$temp_db" 'PRAGMA quick_check;')" != "ok" ]]; then
  echo "Backup integrity check failed" >&2
  exit 1
fi

gzip -c "$temp_db" >"$FINAL_PATH"
chmod 0640 "$FINAL_PATH"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'smash-*.db.gz' -mtime "+$RETENTION_DAYS" -delete

echo "Created $FINAL_PATH"
