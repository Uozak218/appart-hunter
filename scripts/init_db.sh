#!/usr/bin/env bash
# Bootstraps the database: starts Postgres, waits, runs migrations.
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose up -d postgres
echo "Waiting for Postgres..."
until docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-pfh}" > /dev/null 2>&1; do
    sleep 1
done
echo "Postgres up — running migrations"
docker compose run --rm migrate
