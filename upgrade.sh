#!/usr/bin/env bash
set -e

APP_HOME="$HOME/.scalp-app"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[scalp] Upgrading"

docker compose -f "$APP_HOME/docker-compose.yml" down

cp "$REPO_DIR/docker-compose.yml" "$APP_HOME/docker-compose.yml"

docker compose -f "$APP_HOME/docker-compose.yml" up -d --build

echo "[scalp] Upgrade complete"
