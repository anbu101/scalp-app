#!/usr/bin/env bash
set -e

# --------------------------------------------------
# HARD CONSTANTS (NO GUESSING)
# --------------------------------------------------
APP_HOME="$HOME/.scalp-app"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COMPOSE_SRC="$REPO_ROOT/docker-compose.yml"
COMPOSE_DST="$APP_HOME/docker-compose.yml"

echo "[scalp] Installing Scalp App"
echo "[scalp] Repo root   : $REPO_ROOT"
echo "[scalp] App home    : $APP_HOME"
echo "[scalp] Compose src : $COMPOSE_SRC"
echo "[scalp] Compose dst : $COMPOSE_DST"

# --------------------------------------------------
# VALIDATE SOURCE
# --------------------------------------------------
if [ ! -f "$COMPOSE_SRC" ]; then
  echo "[FATAL] docker-compose.yml NOT FOUND in repo root"
  exit 1
fi

# --------------------------------------------------
# CREATE APP HOME (EXPLICIT)
# --------------------------------------------------
mkdir -p "$APP_HOME"
mkdir -p "$APP_HOME"/{data,logs,state,config,bin,trade_intents,zerodha}

# --------------------------------------------------
# COPY (ONE DIRECTION ONLY)
# --------------------------------------------------
cp -f "$COMPOSE_SRC" "$COMPOSE_DST"

# --------------------------------------------------
# INSTALL CLI
# --------------------------------------------------
cat <<'EOS' > "$APP_HOME/bin/scalp"
#!/usr/bin/env bash
APP_HOME="$HOME/.scalp-app"
COMPOSE="$APP_HOME/docker-compose.yml"

if [ ! -f "$COMPOSE" ]; then
  echo "[scalp][ERROR] docker-compose.yml missing in $APP_HOME"
  exit 1
fi

case "$1" in
  start)   docker compose -f "$COMPOSE" up -d ;;
  stop)    docker compose -f "$COMPOSE" down ;;
  restart) docker compose -f "$COMPOSE" down && docker compose -f "$COMPOSE" up -d ;;
  status)  docker compose -f "$COMPOSE" ps ;;
  logs)    docker compose -f "$COMPOSE" logs -f ;;
  *) echo "Usage: scalp {start|stop|restart|status|logs}" ;;
esac
EOS

chmod +x "$APP_HOME/bin/scalp"

# --------------------------------------------------
# PATH
# --------------------------------------------------
grep -q 'scalp-app/bin' "$HOME/.zshrc" || \
  echo 'export PATH="$HOME/.scalp-app/bin:$PATH"' >> "$HOME/.zshrc"

echo "[scalp] ✅ Install complete"
echo "[scalp] Run: source ~/.zshrc"
echo "[scalp] Then: scalp start"
