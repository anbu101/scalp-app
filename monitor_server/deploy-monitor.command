#!/bin/bash
# =====================================================================
# deploy-monitor.command — ── FLEET_MONITOR_20261127 ── (TD-110)
#
# ONE-CLICK deploy of the Scalp Fleet Monitor to the license droplet.
# Double-click in Finder, or: ./monitor_server/deploy-monitor.command
# Safe to re-run: code is updated and the service restarted; the database,
# users and secrets are never overwritten.
#
#   1. Checks ports 80/443 on the droplet are free (or already Caddy's)
#   2. Copies monitor_server files to /opt/scalp-monitor
#   3. Installs python3-venv, sqlite3, qrencode, Caddy (official apt repo)
#   4. Creates the venv, installs requirements
#   5. Installs + starts scalp-monitor (127.0.0.1:9200, never public)
#   6. Writes /etc/caddy/Caddyfile (HTTPS on 443 for 139-59-8-202.sslip.io)
#      — refuses if that file is someone else's, so nothing else breaks
#   7. Opens 80 + 443 in ufw when ufw is active
#   8. Nightly backup of monitor.db at 01:27, keeps 14 days
#   9. Checks https://139-59-8-202.sslip.io/healthz from this Mac
#
# It never touches the license server (scalp-license, port 9100).
# Then add logins:  ssh root@139.59.8.202
#                   cd /opt/scalp-monitor && ./venv/bin/python users.py add <name> [--admin]
# =====================================================================

set -e
cd "$(dirname "$0")"

DROPLET_IP="139.59.8.202"
HOSTNAME_TLS="139-59-8-202.sslip.io"
SSH_USER="root"
SSH_KEY=""          # optional, e.g. "$HOME/.ssh/id_rsa" — blank = default key
REMOTE_DIR="/opt/scalp-monitor"
# ── MONITOR_DEPLOY_FILES_20261127 ── (DEF-110) every server module, found here, not typed in: the hand-kept list missed
# accounts.py (PORTAL_USERS_ADMIN_20261127) and the service died on its first import. Tests stay home.
FILES="$(ls *.py | grep -v '^test_' | tr '\n' ' ')requirements.txt scalp-monitor.service Caddyfile"

SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
[ -n "$SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY"

for f in $FILES; do
  [ -f "$f" ] || { echo "!! missing $f next to this script"; exit 1; }
done

echo "[1/5] Checking ports 80/443 on $DROPLET_IP ..."
PORTS=$(ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" "ss -ltnpH '( sport = :80 or sport = :443 )' || true")
if [ -n "$PORTS" ] && ! echo "$PORTS" | grep -q caddy; then
  echo "!! Something other than Caddy already listens on 80/443:"
  echo "$PORTS"
  echo "   Not continuing (it would break that service). Tell Claude what it is."
  exit 1
fi
echo "      free (or already Caddy)."

echo "[2/5] Copying files ..."
ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" "mkdir -p $REMOTE_DIR"
# shellcheck disable=SC2086
scp $SSH_OPTS -q $FILES "$SSH_USER@$DROPLET_IP:$REMOTE_DIR/"
echo "      done."

echo "[3/5] Setting up on the droplet ..."
ssh $SSH_OPTS "$SSH_USER@$DROPLET_IP" bash -s -- "$REMOTE_DIR" << 'REMOTE_SCRIPT'
set -e
DIR="$1"
cd "$DIR"
export DEBIAN_FRONTEND=noninteractive

echo "  - packages"
NEED=""
command -v sqlite3 >/dev/null 2>&1 || NEED="$NEED sqlite3"
command -v qrencode >/dev/null 2>&1 || NEED="$NEED qrencode"
python3 -c 'import ensurepip' >/dev/null 2>&1 || NEED="$NEED python3-venv"
if [ -n "$NEED" ]; then
  apt-get update -qq >/dev/null
  apt-get install -y -qq $NEED >/dev/null
fi
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq >/dev/null
  apt-get install -y -qq caddy >/dev/null
fi
echo "      caddy $(caddy version | cut -d' ' -f1)"

echo "  - python venv"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip >/dev/null
./venv/bin/pip install --quiet -r requirements.txt
mkdir -p secrets backups
chmod 700 secrets
[ -f secrets/monitor.json ] || echo '{"telegram_targets": []}' > secrets/monitor.json
chmod 600 secrets/monitor.json

echo "  - service"
cp scalp-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable scalp-monitor >/dev/null 2>&1 || true
systemctl restart scalp-monitor
OK=""
for i in $(seq 1 15); do
  if curl -s -m 3 http://127.0.0.1:9200/healthz | grep -q '"ok":true'; then OK=1; break; fi
  sleep 1
done
[ -n "$OK" ] || { echo "  !! scalp-monitor did not start:"; journalctl -u scalp-monitor -n 30 --no-pager; exit 1; }
echo "      scalp-monitor running on 127.0.0.1:9200"

echo "  - caddy config"
CF=/etc/caddy/Caddyfile
if [ -f "$CF" ] && ! grep -q 'FLEET_MONITOR_' "$CF" && ! grep -q '/usr/share/caddy' "$CF"; then
  echo "  !! $CF exists and is not the default or ours — not overwriting. Merge by hand:"
  cat Caddyfile
  exit 1
fi
[ -f "$CF" ] && cp "$CF" "$CF.bak-$(date +%Y%m%d%H%M%S)"
cp Caddyfile "$CF"
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy 2>/dev/null || true
caddy validate --config "$CF" --adapter caddyfile >/dev/null
chown -R caddy:caddy /var/log/caddy   # ── FLEET_MONITOR_FIX_20261127 ── (DEF-108) validate ran as root and left the
                                      # log file root-owned: caddy (user caddy) could not open it
systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo "  - firewall (ufw)"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 8443/tcp >/dev/null   # ── LICENSE_HTTPS_20261127 ── licence over HTTPS
  echo "      ufw: 80 and 443 opened"
else
  echo "      ufw not active — skipping"
fi

echo "  - nightly backup cron"
# ── FLEET_MONITOR_FIX_20261127 ── (DEF-109) under set -e a bare `crontab -l | grep -v` stopped the subshell when the
# crontab was empty (grep exits 1), so `crontab -` installed an EMPTY crontab and no backup ran.
{ { crontab -l 2>/dev/null || true; } | { grep -v 'scalp-monitor-backup' || true; }; \
  echo "27 1 * * * sqlite3 $DIR/monitor.db \".backup $DIR/backups/monitor-\$(date +\%F).db\" && find $DIR/backups -name 'monitor-*.db' -mtime +14 -delete # scalp-monitor-backup"; \
} | crontab -
crontab -l | grep -q 'scalp-monitor-backup' || { echo "  !! backup cron was not installed"; exit 1; }
echo "REMOTE_SETUP_COMPLETE"
REMOTE_SCRIPT

echo "[4/5] Waiting for the HTTPS certificate (first run can take a minute) ..."
EXT=""
for i in $(seq 1 30); do
  EXT=$(curl -s -m 5 "https://$HOSTNAME_TLS/healthz" || true)
  case "$EXT" in *'"ok":true'*) break ;; esac
  sleep 3
done
case "$EXT" in
  *'"ok":true'*) echo "      HTTPS OK: https://$HOSTNAME_TLS"
     LIC=$(curl -s -m 8 "https://$HOSTNAME_TLS:8443/health" || true)   # ── LICENSE_HTTPS_20261127 ──
     case "$LIC" in *'"status":"ok"'*) echo "      licence server over HTTPS OK: https://$HOSTNAME_TLS:8443" ;;
       *) echo "  !! https://$HOSTNAME_TLS:8443 not answering (apps fall back to plain HTTP). Firewall: allow TCP 8443." ;; esac ;;
  *)
    echo "  !! https://$HOSTNAME_TLS is not answering yet."
    echo "     - DigitalOcean Cloud Firewall: allow inbound TCP 80 and 443 (Networking → Firewalls)."
    echo "     - Certificate: ssh $SSH_USER@$DROPLET_IP 'journalctl -u caddy -n 50 --no-pager'"
    echo "       If sslip.io is refused by Let's Encrypt, a free DuckDNS name is the fallback (ask Claude)."
    exit 1 ;;
esac

echo "[5/5] Done."
echo ""
echo "  Portal : https://$HOSTNAME_TLS"
echo "  Logins : ssh $SSH_USER@$DROPLET_IP"
echo "           cd $REMOTE_DIR && ./venv/bin/python users.py add <name>          (friend, read-only)"
echo "           cd $REMOTE_DIR && ./venv/bin/python users.py add <name> --admin  (you)"
echo "  Group  : edit $REMOTE_DIR/secrets/monitor.json, e.g."
echo '           {"telegram_targets": [{"chat_id": "<your chat id>", "admin_devices": false},'
echo '                                 {"chat_id": "<group chat id>", "admin_devices": true}]}'
echo "           (no restart needed; empty list = your license-server chat, your own Macs skipped)"
echo "  Logs   : ssh $SSH_USER@$DROPLET_IP 'journalctl -u scalp-monitor -n 50 --no-pager'"
