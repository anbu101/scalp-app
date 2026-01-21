#!/usr/bin/env bash
set -e

APP_HOME="$HOME/.scalp-app"

echo "[scalp] Stopping services"
docker compose -f "$APP_HOME/docker-compose.yml" down || true

echo "[scalp] Removing app home"
rm -rf "$APP_HOME"

echo "[scalp] Uninstalled"
