#!/usr/bin/env bash
# Dump the Postgres database to ./backups/ and keep the last N days.
# Usage:
#   bash scripts/backup_db.sh           # 7-day retention
#   RETENTION_DAYS=30 bash scripts/backup_db.sh
#
# Install as a daily cron (on the VPS, as user pfh):
#   (crontab -l 2>/dev/null; echo "15 3 * * * cd $HOME/paris-flat-hunter && /bin/bash scripts/backup_db.sh >> logs/backup.log 2>&1") | crontab -
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups logs

RETENTION_DAYS="${RETENTION_DAYS:-7}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="backups/pfh-${STAMP}.sql.gz"

# Read DB creds from compose env (docker compose auto-loads .env).
DB_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2)"
DB_NAME="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)"
: "${DB_USER:=pfh}"
: "${DB_NAME:=pfh}"

echo "==> pg_dump ${DB_NAME} -> ${OUT}"
docker compose exec -T postgres pg_dump -U "${DB_USER}" -d "${DB_NAME}" --no-owner \
    | gzip -9 > "${OUT}"

# File size sanity check
SIZE="$(stat -c%s "${OUT}" 2>/dev/null || stat -f%z "${OUT}")"
if [[ "${SIZE}" -lt 1024 ]]; then
    echo "ERROR: dump suspiciously small (${SIZE} bytes)" >&2
    exit 1
fi

echo "==> Rotating backups older than ${RETENTION_DAYS} days"
find backups -type f -name 'pfh-*.sql.gz' -mtime +"${RETENTION_DAYS}" -print -delete || true

echo "==> Done. Current backups:"
ls -lh backups | tail -n +2
