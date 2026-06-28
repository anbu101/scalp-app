#!/bin/bash
# =====================================================================
# deploy-license-server.command
#
# ONE-CLICK deploy of the Scalp License Server to the DigitalOcean
# droplet. Double-click this file in Finder (Terminal will open).
#
# First time on your Mac:
#     chmod +x deploy-license-server.command
#   then right-click -> Open (to get past Gatekeeper once).
#
# What it does (safe to re-run any time — re-runs update code and
# restart the service; keys are generated ONCE and never overwritten):
#   1. Copies license_server files to /opt/scalp-license on the droplet
#   2. Installs python3-venv + sqlite3 if missing
#   3. Creates venv + installs requirements
#   4. Runs keygen.py (ONLY if no keys exist yet)
#   5. Installs + starts the systemd service (port 9100)
#   6. Opens port 9100 in ufw (if ufw is active)
#   7. Installs the nightly backup cron (01:17, keeps 14 days)
#   8. Creates your first ADMIN license (only if the DB is empty)
#   9. Saves the PUBLIC KEY to license_server_public_key.pem on your
#      Mac (needed for Phase 2) and shows the admin secret ONCE
#  10. Verifies the server is reachable from your Mac
#
# This script must live in the same folder as server.py / db.py /
# signing.py / keygen.py / notify.py / server_meta.py / requirements.txt /
# scalp-license.service / admin_ui.html.
# It NEVER touches the relay or any other service on the droplet.
# =====================================================================

set -e
cd "$(dirname "$0")"

# ------------------------------------------------------------------
# CONFIG — edit DROPLET_IP here once, or leave blank to be asked.
# ------------------------------------------------------------------
DROPLET_IP=""
SSH_USER="root"
SSH_KEY=""          # optional, e.g. "$HOME/.ssh/id_rsa" — blank = default key
PORT="9100"
REMOTE_DIR="/opt/scalp-license"

# ------------------------------------------------------------------
# PROMPTS
# ------------------------------------------------------------------
if [ -z "$DROPLET_IP" ]; then
  printf "Droplet IP address: "
  read DROPLET_IP
fi
if [ -z "$DROPLET_IP" ]; then
  echo "No IP given — aborting."; exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
if [ -n "$SSH_KEY" ]; then
  SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

echo ""
echo "=================================================================="
echo " Scalp License Server -> $SSH_USER@$DROPLET_IP:$REMOTE_DIR (port $PORT)"
echo "=================================================================="

# ------------------------------------------------------------------
# SANITY: required files present locally
# ------------------------------------------------------------------
for f in server.py db.py signing.py keygen.py notify.py server_meta.py requirements.txt scalp-license.service admin_ui.html; do
  if [ ! -f "$f" ]; then
    echo "MISSING file next to this script: $f — aborting."; exit 1
  fi
done

# ------------------------------------------------------------------
# 1. COPY FILES
# ------------------------------------------------------------------
echo ""
echo "[1/4] Copying files to droplet..."
ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" "mkdir -p $REMOTE_DIR"
scp $SSH_OPTS -q server.py db.py signing.py keygen.py notify.py server_meta.py requirements.txt scalp-license.service admin_ui.html \
    "$SSH_USER@$DROPLET_IP:$REMOTE_DIR/"
echo "      done."

# ------------------------------------------------------------------
# 2. REMOTE SETUP (single ssh session, fails loudly on any error)
# ------------------------------------------------------------------
echo ""
echo "[2/4] Setting up on droplet (deps, venv, keys, service, firewall, cron)..."
ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" bash -s -- "$PORT" "$REMOTE_DIR" << 'REMOTE_SCRIPT'
set -e
PORT="$1"
DIR="$2"
cd "$DIR"

echo "  - dependencies"
if ! command -v sqlite3 >/dev/null 2>&1 || ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq python3-venv sqlite3 >/dev/null
fi

echo "  - python venv"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip >/dev/null
./venv/bin/pip install --quiet -r requirements.txt

if [ ! -f secrets/private_key.pem ]; then
  echo "  - generating keys (FIRST RUN ONLY)"
  ./venv/bin/python keygen.py >/dev/null
else
  echo "  - keys already exist (NOT regenerating — existing tokens stay valid)"
fi
chmod 700 secrets

echo "  - systemd service"
cp scalp-license.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable scalp-license >/dev/null 2>&1 || true
systemctl restart scalp-license
sleep 2
if ! systemctl is-active --quiet scalp-license; then
  echo "  !! service failed to start — last 20 log lines:"
  journalctl -u scalp-license -n 20 --no-pager
  exit 1
fi

echo "  - local health check"
HEALTH=""
for i in $(seq 1 15); do
  HEALTH=$(curl -s -m 5 "http://127.0.0.1:${PORT}/health" || true)
  case "$HEALTH" in
    *'"status":"ok"'*) break ;;
  esac
  sleep 1
done
case "$HEALTH" in
  *'"status":"ok"'*) echo "      OK: $HEALTH" ;;
  *) echo "  !! health check FAILED after 15s: $HEALTH"; journalctl -u scalp-license -n 20 --no-pager; exit 1 ;;
esac

echo "  - firewall (ufw)"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow ${PORT}/tcp >/dev/null
  echo "      ufw: port ${PORT} opened"
else
  echo "      ufw not active — skipping (if you use a DO Cloud Firewall, see note at end)"
fi

echo "  - min_version endpoint check"
MV=""
for i in $(seq 1 10); do
  MV=$(curl -s -m 5 "http://127.0.0.1:${PORT}/min_version" || true)
  case "$MV" in
    *'"min_version"'*) break ;;
  esac
  sleep 1
done
case "$MV" in
  *'"min_version"'*) echo "      OK: $MV" ;;
  *) echo "  !! /min_version not responding ($MV) — server_meta.py may be missing"; exit 1 ;;
esac

echo "  - nightly backup cron"
mkdir -p backups
( crontab -l 2>/dev/null | grep -v 'scalp-license-backup' ; \
  echo "17 1 * * * sqlite3 $DIR/licenses.db \".backup $DIR/backups/licenses-\$(date +\%F).db\" && find $DIR/backups -name 'licenses-*.db' -mtime +14 -delete # scalp-license-backup" \
) | crontab -

echo "  - first ADMIN license (only if DB is empty)"
SECRET=$(cat secrets/admin_secret.txt)
COUNT=$(curl -s "http://127.0.0.1:${PORT}/admin/list" -H "X-Admin-Secret: $SECRET" \
        | ./venv/bin/python -c "import json,sys; print(len(json.load(sys.stdin)['licenses']))")
if [ "$COUNT" = "0" ]; then
  RESP=$(curl -s -X POST "http://127.0.0.1:${PORT}/admin/create" \
      -H "Content-Type: application/json" -H "X-Admin-Secret: $SECRET" \
      -d '{"label":"Anbu - main","tier":"ADMIN"}')
  ADMIN_KEY=$(echo "$RESP" | ./venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['license']['key'])")
  echo "NEW_ADMIN_LICENSE_KEY=$ADMIN_KEY"
else
  echo "      DB already has $COUNT license(s) — not creating another"
fi

echo "REMOTE_SETUP_COMPLETE"
REMOTE_SCRIPT
echo "      done."

# ------------------------------------------------------------------
# 3. PULL PUBLIC KEY + SHOW ADMIN SECRET
# ------------------------------------------------------------------
echo ""
echo "[3/4] Fetching public key + admin secret..."
ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" "cat $REMOTE_DIR/secrets/public_key.pem" > license_server_public_key.pem
ADMIN_SECRET=$(ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" "cat $REMOTE_DIR/secrets/admin_secret.txt")

echo ""
echo "  PUBLIC KEY saved to:  $(pwd)/license_server_public_key.pem"
echo "  (needed in Phase 2 — gets embedded in the desktop app)"
echo ""
echo "  ADMIN SECRET (store in your password manager NOW; used by the"
echo "  Telegram admin bot in Phase 4):"
echo ""
echo "      $ADMIN_SECRET"

# ------------------------------------------------------------------
# 4. EXTERNAL REACHABILITY CHECK (from this Mac)
# ------------------------------------------------------------------
echo ""
echo "[4/4] Checking the server is reachable from this Mac..."
EXT=$(curl -s -m 8 "http://$DROPLET_IP:$PORT/health" || true)
case "$EXT" in
  *'"status":"ok"'*)
    echo "      REACHABLE: $EXT"
    ;;
  *)
    echo "  !! NOT reachable from outside, although it is running on the droplet."
    echo "     Most likely cause: a DigitalOcean CLOUD FIREWALL is attached to"
    echo "     this droplet (it filters traffic BEFORE the droplet sees it)."
    echo "     Fix: DO console -> Networking -> Firewalls -> your firewall ->"
    echo "     Inbound Rules -> add: Custom / TCP / port $PORT / All IPv4."
    echo "     Then re-run this script to verify."
    ;;
esac

echo ""
echo "=================================================================="
echo " DONE. Useful commands:"
echo "   status : ssh $SSH_USER@$DROPLET_IP 'systemctl status scalp-license'"
echo "   logs   : ssh $SSH_USER@$DROPLET_IP 'journalctl -u scalp-license -n 50'"
echo "=================================================================="