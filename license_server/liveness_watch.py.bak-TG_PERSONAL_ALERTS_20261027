# license_server/liveness_watch.py
#
# ── LIVENESS WATCH ── the droplet half of the dead-man's switch (2026-09-01)
# ============================================================================
# The app POSTs to /liveness every 60 s (backend/app/jobs/liveness_ping.py).
# This module stores the last-seen record per license and runs a background
# thread (same pattern as notify.start_expiry_watcher) that, every 30 s:
#
#   for each record that carries a Telegram target:
#     if IST now is a weekday, NOT in that record's own holiday list, and
#        09:15 <= now <= 15:35, and last_seen is > 3 min old, and no alert
#        is outstanding                          → alert the USER's Telegram
#     if an alert is outstanding and pings resumed → recovery note, clear
#
# Alerts go to the USER'S bot + chat (sent with each ping) — not the admin
# channel — because the person who needs to know is the one whose laptop
# is asleep. If notify.enabled() (admin Telegram on the droplet) an admin
# copy is sent too, so the fleet operator learns about a friend's outage.
#
# NO TELEGRAM = NO ALERT, NO ERROR. A record without a target is stored
# (admin visibility) and simply never alerts.
#
# STORAGE: liveness.json beside the license DB. Alert state lives in it, so
# a server restart neither re-alerts nor forgets an outstanding alert.
# Bot tokens are stored here in plain text on the operator's own droplet —
# the same trust boundary as the license DB itself.
#
# HOLIDAYS: per-record, sent by the app from its single NSE calendar. A
# record with an empty list treats every weekday as trading — that errs
# toward MORE alerts, never fewer.
# ============================================================================

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_START = (9, 15)
WINDOW_END = (15, 35)
SILENCE_S = 180
SCAN_EVERY_S = 30

_DATA_DIR = Path(os.environ.get("LICSRV_DATA_DIR",
                                Path(__file__).resolve().parent / "data"))
STATE_FILE = _DATA_DIR / "liveness.json"
_lock = threading.Lock()


# ── state ────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save(state: dict) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[LIVENESS] save failed: {e!r}")


def record(key: str, machine_id: str, label: str, telegram: dict | None,
           holidays: list) -> None:
    """Called by the /liveness route after the key/machine pair validated."""
    with _lock:
        st = _load()
        rec = st.get(key) or {}
        rec.update({
            "machine_id": machine_id,
            "label": (label or "")[:64],
            "last_seen": int(time.time()),
            "telegram": (telegram if isinstance(telegram, dict)
                         and telegram.get("bot_token")
                         and telegram.get("chat_id") else None),
            "holidays": sorted({str(h)[:10] for h in (holidays or [])}),
        })
        rec.setdefault("alerted_at", None)
        st[key] = rec
        _save(st)


def snapshot() -> dict:
    """Admin view: last_seen per license, tokens redacted."""
    with _lock:
        st = _load()
    now = int(time.time())
    return {k: {"label": v.get("label"), "machine_id": v.get("machine_id"),
                "silent_for_s": now - int(v.get("last_seen") or 0),
                "has_telegram": bool(v.get("telegram")),
                "alerted_at": v.get("alerted_at")}
            for k, v in st.items()}


# ── telegram ─────────────────────────────────────────────────────────────
def _send(bot_token: str, chat_id: str, text: str) -> bool:
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                       "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=8):
            return True
    except Exception as e:
        print(f"[LIVENESS] telegram send failed: {e!r}")
        return False


def _admin_copy(text: str) -> None:
    try:
        import notify
        if notify.enabled():
            notify.send(text)
    except Exception:
        pass


# ── the rule ─────────────────────────────────────────────────────────────
def in_window(now_ist: datetime, holidays: list) -> bool:
    if now_ist.weekday() >= 5:
        return False
    if now_ist.date().isoformat() in set(holidays or []):
        return False
    hm = (now_ist.hour, now_ist.minute)
    return WINDOW_START <= hm <= WINDOW_END


def scan_once(now_ts: int | None = None) -> list:
    """One pass. Returns the actions taken (for tests). Pure with respect
    to the clock when now_ts is given."""
    now = int(now_ts or time.time())
    now_ist = datetime.fromtimestamp(now, IST)
    actions = []
    with _lock:
        st = _load()
        for key, rec in st.items():
            tg = rec.get("telegram")
            silent = now - int(rec.get("last_seen") or 0)
            label = rec.get("label") or key[-6:]
            if rec.get("alerted_at"):
                if silent <= SILENCE_S:
                    rec["alerted_at"] = None
                    actions.append(("recover", key))
                    if tg:
                        _send(tg["bot_token"], tg["chat_id"],
                              f"✅ <b>Scalp Terminal on {label}</b> is back "
                              f"online ({now_ist:%H:%M} IST).")
                    _admin_copy(f"✅ liveness: {label} back online")
                continue
            if not tg:
                continue
            if silent > SILENCE_S and in_window(now_ist, rec.get("holidays")):
                rec["alerted_at"] = now
                actions.append(("alert", key))
                _send(tg["bot_token"], tg["chat_id"],
                      f"🔴 <b>Scalp Terminal on {label} has gone silent</b>\n"
                      f"No liveness ping for {silent // 60} min "
                      f"({now_ist:%H:%M} IST, market hours).\n"
                      f"Laptop asleep, Wi-Fi off, or the app is down — "
                      f"strategies cannot trade until it is back.")
                _admin_copy(f"🔴 liveness: {label} silent {silent // 60} min")
        _save(st)
    return actions


def start_watcher() -> None:
    def loop():
        while True:
            try:
                scan_once()
            except Exception as e:
                print(f"[LIVENESS] scan error: {e!r}")
            time.sleep(SCAN_EVERY_S)
    threading.Thread(target=loop, name="liveness-watch", daemon=True).start()
    print("[LIVENESS] watcher started")