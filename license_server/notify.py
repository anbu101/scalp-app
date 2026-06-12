"""
license_server/notify.py

Telegram notifications for the license server (Phase 4, notifications-only).

Sends to YOUR chat (the admin), never to users:
  - "activated"          when a license binds to a machine for the first time
  - "expiring in N days" at 7 / 3 / 1 days before expiry (once per threshold)
  - "expired"            on the day a license lapses

Configuration (optional - everything here is silently disabled if absent):
  secrets/telegram.json   {"bot_token": "123456:ABC...", "chat_id": "123456789"}

Design rules:
  - stdlib only (urllib) - no new dependencies
  - fire-and-forget threads with short timeouts - a Telegram outage can
    NEVER slow down or break an /activate or /heartbeat call
  - never raises; failures are printed (visible in journalctl) and dropped
  - duplicate-suppression state in notify_state.json next to the DB
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SECRETS_DIR = Path(os.environ.get(
    "LICSRV_SECRETS_DIR", Path(__file__).resolve().parent / "secrets"
))
TELEGRAM_FILE = SECRETS_DIR / "telegram.json"
STATE_FILE = Path(os.environ.get(
    "LICSRV_NOTIFY_STATE", Path(__file__).resolve().parent / "notify_state.json"
))

EXPIRY_WARN_DAYS = (7, 3, 1, 0)      # 0 = "expired today"
SCAN_INTERVAL_SECONDS = 6 * 3600     # expiry scan cadence

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

def _config() -> dict | None:
    """Returns {"bot_token", "chat_id"} or None when notifications are off."""
    try:
        if not TELEGRAM_FILE.exists():
            return None
        cfg = json.loads(TELEGRAM_FILE.read_text())
        if cfg.get("bot_token") and cfg.get("chat_id"):
            return cfg
    except Exception as e:
        print(f"[NOTIFY] telegram.json unreadable: {e}")
    return None


def enabled() -> bool:
    return _config() is not None


# --------------------------------------------------
# SENDING (fire-and-forget)
# --------------------------------------------------

def _send_blocking(text: str):
    cfg = _config()
    if not cfg:
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": cfg["chat_id"],
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
            data=data,
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception as e:
        print(f"[NOTIFY] Telegram send failed: {e}")


def send(text: str):
    """Non-blocking, never raises."""
    try:
        threading.Thread(target=_send_blocking, args=(text,), daemon=True).start()
    except Exception as e:
        print(f"[NOTIFY] could not spawn sender: {e}")


# --------------------------------------------------
# EVENT NOTIFICATIONS (called from server.py)
# --------------------------------------------------

def notify_activation(label: str, tier: str, key: str, machine_id: str):
    send(
        "✅ <b>License activated</b>\n"
        f"{label} ({tier})\n"
        f"<code>{key}</code>\n"
        f"machine {machine_id[:10]}…"
    )


# --------------------------------------------------
# EXPIRY WATCHER (background thread, started from server.py)
# --------------------------------------------------

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except Exception as e:
        print(f"[NOTIFY] could not persist notify_state: {e}")


def _scan_once(list_licenses_fn):
    """One pass over all licenses; notifies thresholds not yet notified."""
    if not enabled():
        return
    state = _load_state()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    changed = False

    for lic in list_licenses_fn():
        if lic.get("revoked"):
            continue
        try:
            expiry = datetime.strptime(lic["expires_at"], "%Y-%m-%d").replace(tzinfo=IST)
            days_left = (expiry.date() - datetime.now(IST).date()).days
        except Exception:
            continue
        if days_left < 0 or days_left not in EXPIRY_WARN_DAYS:
            continue

        key = lic["key"]
        marker = state.setdefault(key, {})
        tag = str(days_left)
        if marker.get(tag) == today or (tag in marker and days_left > 0):
            continue  # this threshold already announced

        if days_left == 0:
            send(
                "❌ <b>License expired today</b>\n"
                f"{lic['label']}\n<code>{key}</code>\n"
                "Extend it from the dashboard if intended to continue."
            )
        else:
            send(
                f"⏳ <b>License expiring in {days_left} day{'s' if days_left != 1 else ''}</b>\n"
                f"{lic['label']}\n<code>{key}</code>\n"
                f"expires {lic['expires_at']}"
            )
        marker[tag] = today
        changed = True

    if changed:
        _save_state(state)


def start_expiry_watcher(list_licenses_fn):
    """Launch the background scan loop. Safe to call when disabled - the
    loop just idles cheaply and picks up telegram.json if it appears later."""
    def loop():
        time.sleep(20)  # let the server settle first
        while True:
            try:
                _scan_once(list_licenses_fn)
            except Exception as e:
                print(f"[NOTIFY] expiry scan error: {e}")
            time.sleep(SCAN_INTERVAL_SECONDS)

    threading.Thread(target=loop, daemon=True).start()