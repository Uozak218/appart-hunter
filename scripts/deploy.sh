#!/usr/bin/env bash
# Deploy local code → Hetzner prod server.
#
# Usage:
#   ./scripts/deploy.sh              # rsync + restart scraper+api (no rebuild)
#   ./scripts/deploy.sh --build      # rsync + docker build + recreate
#   ./scripts/deploy.sh --logs       # also tail scraper logs after
#   ./scripts/deploy.sh --build --logs
#
# What it deliberately does NOT touch:
#   - /opt/lapepite/.env  (secrets live on server; never overwritten)
#   - /opt/lapepite/logs/ (kept across deploys)
#   - /opt/lapepite/backups/ (DB snapshots, 7-day rolling)
#   - playwright_state docker volume (cookie cache)
set -euo pipefail

SERVER="${LAPEPITE_SERVER:-root@178.104.167.239}"
REMOTE="${LAPEPITE_REMOTE:-/opt/lapepite}"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

BUILD=0
TAIL_LOGS=0
for arg in "$@"; do
    case "$arg" in
        --build) BUILD=1 ;;
        --logs)  TAIL_LOGS=1 ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *) echo "Unknown flag: $arg" >&2; exit 2 ;;
    esac
done

echo "→ rsyncing code to ${SERVER}:${REMOTE}"
rsync -az --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
    --exclude 'logs/' --exclude '.playwright/' \
    --exclude '.env*' --exclude '.DS_Store' --exclude '*.pyc' \
    --exclude 'node_modules/' --exclude '.pytest_cache/' --exclude '.mypy_cache/' \
    --exclude '.ruff_cache/' --exclude '.claude/' --exclude 'backups/' \
    --exclude 'docker-compose.override.yml' \
    "${LOCAL_ROOT}/" "${SERVER}:${REMOTE}/"

if [[ $BUILD -eq 1 ]]; then
    echo "→ docker build on server (rebuilds Python deps + image)"
    ssh "$SERVER" "cd ${REMOTE} && docker compose build"
    echo "→ recreating containers"
    ssh "$SERVER" "cd ${REMOTE} && docker compose up -d"
else
    echo "→ restart scraper + api + bot (no rebuild; use --build if Dockerfile/pyproject changed)"
    ssh "$SERVER" "cd ${REMOTE} && docker compose restart scraper api bot"
fi

echo "→ post-deploy check"
ssh "$SERVER" "cd ${REMOTE} && docker compose ps && curl -sf -m 5 http://127.0.0.1:8000/health && echo ''"

if [[ $TAIL_LOGS -eq 1 ]]; then
    echo "→ tailing scraper logs (Ctrl-C to stop)"
    ssh "$SERVER" "cd ${REMOTE} && docker compose logs -f --tail=20 scraper"
fi

echo "✅ deployed"
