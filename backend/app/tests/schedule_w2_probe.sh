#!/bin/bash
# ============================================================
# schedule_w2_probe.sh — one-shot unattended W2 probe runner
#
# Start it in a Terminal tab BEFORE you leave; it keeps the Mac
# awake (caffeinate), waits until the target time, then SSHes to
# the OCI box and runs the probe in --auto-arm --no-trigger-test
# mode. Full output lands in ~/Desktop/w2_probe_<time>.log and a
# macOS notification fires when done.
#
# SETUP (one time, before starting):
#   1. cp w2_probe.env.example ~/w2_probe.env
#   2. Edit ~/w2_probe.env with the four Angel credentials.
#   3. chmod 600 ~/w2_probe.env
#   4. Keep the Mac PLUGGED IN with the LID OPEN (closed lid
#      sleeps regardless of caffeinate).
#
# USAGE:
#   ./schedule_w2_probe.sh            # runs at 11:15 local (IST)
#   ./schedule_w2_probe.sh 12:30      # custom time today
# ============================================================
set -euo pipefail

KEY="$HOME/Downloads/ssh-key-2026-03-31-4.key"
HOST="opc@140.245.243.226"
ENVFILE="$HOME/w2_probe.env"
PROBE="angel_w2_orderpath_probe.py"
TARGET="${1:-11:15}"
LOG="$HOME/Desktop/w2_probe_$(date +%Y%m%d_%H%M).log"

# Keep the machine awake for the lifetime of this script.
if [ -z "${CAFFEINATED:-}" ]; then
  exec caffeinate -s env CAFFEINATED=1 "$0" "$@"
fi

# ---- preflight ----
[ -f "$KEY" ]     || { echo "SSH key missing: $KEY"; exit 1; }
[ -f "$ENVFILE" ] || { echo "Credentials file missing: $ENVFILE (copy w2_probe.env.example)"; exit 1; }
[ -f "$PROBE" ]   || { echo "Run this from the folder containing $PROBE"; exit 1; }
chmod 600 "$ENVFILE"
# shellcheck disable=SC1090
source "$ENVFILE"
for v in ANGEL_API_KEY ANGEL_CLIENT ANGEL_PIN ANGEL_TOTP_SECRET; do
  [ -n "${!v:-}" ] || { echo "$v is empty in $ENVFILE"; exit 1; }
done
ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$KEY" "$HOST" "echo SSH_OK" \
  || { echo "SSH preflight to $HOST failed"; exit 1; }
echo "Preflight OK. Target time: $TARGET  Log: $LOG"

# ---- wait until target (today, local clock = IST) ----
now_s=$(date +%s)
tgt_s=$(date -j -f "%H:%M" "$TARGET" +%s 2>/dev/null || true)
if [ -z "$tgt_s" ]; then echo "Bad time format: $TARGET (use HH:MM)"; exit 1; fi
if [ "$tgt_s" -le "$now_s" ]; then
  hh=$(date +%H%M)
  if [ "$hh" -lt 1500 ]; then
    echo "Target already passed — running immediately (window still open)."
  else
    echo "Target passed and market window closing/closed. Not running."; exit 1
  fi
else
  echo "Waiting $(( (tgt_s - now_s) / 60 )) minutes... (leave this tab open)"
  sleep $(( tgt_s - now_s ))
fi

# ---- ship probe + creds, run remotely, capture everything ----
echo "=== W2 probe run $(date) ===" | tee "$LOG"
scp -i "$KEY" "$PROBE" "$HOST":~/ 2>&1 | tee -a "$LOG"

ssh -T -i "$KEY" "$HOST" \
  "ANGEL_API_KEY='$ANGEL_API_KEY' ANGEL_CLIENT='$ANGEL_CLIENT' \
   ANGEL_PIN='$ANGEL_PIN' ANGEL_TOTP_SECRET='$ANGEL_TOTP_SECRET' bash -s" \
  <<'REMOTE' 2>&1 | tee -a "$LOG"
set -e
cd ~
if [ ! -d probe-venv ]; then python3 -m venv probe-venv; fi
source probe-venv/bin/activate
pip install -q requests pyotp
python3 angel_w2_orderpath_probe.py --offset 700 --auto-arm --no-trigger-test
REMOTE
RC=${PIPESTATUS[0]:-0}

# ---- notify ----
if grep -q "PROBE SUMMARY" "$LOG" && ! grep -q "MANUAL CLEANUP\|SQUARE OFF MANUALLY\|still armed" "$LOG"; then
  MSG="W2 probe finished cleanly — read $LOG"
else
  MSG="W2 probe finished WITH WARNINGS — read $LOG and check the Angel app"
fi
osascript -e "display notification \"$MSG\" with title \"Scalp W2 Probe\"" || true
echo "$MSG"
exit "$RC"
