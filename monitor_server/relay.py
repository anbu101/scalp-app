"""
monitor_server/relay.py — ── FLEET_MONITOR_20261127 ── (TD-110)

Sends every new CRITICAL / ORDER_REJECTED / SYSTEM_ALERT from any install to
Telegram, so installs with no bot of their own (non-admin) still reach a
phone. Config (all optional; relay is off when no target resolves):

  secrets/monitor.json
    {"telegram_targets": [{"chat_id": "123", "admin_devices": false},
                          {"chat_id": "-100…", "admin_devices": true}],
     "bot_token": "…"}                 # optional: else the license server's
  /opt/scalp-license/secrets/telegram.json  {"bot_token", "chat_id"}
    → used for the bot token, and as the one default target (admin_devices
      false) when monitor.json lists none.

admin_devices false = skip installs on an ADMIN licence (Anbu's own Macs,
which already page him themselves); a group chat usually wants true.

Floods: at most PER_MIN messages per install per minute; the rest fold into
one "+N more" line. Backlog: an event older than STALE_S when it arrives
(an install catching up after being offline) is not sent one by one; the
install's backlog becomes one summary line. Never raises; sends on a
background thread with a short timeout.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SECRETS_DIR = Path(os.environ.get("MONSRV_SECRETS_DIR", Path(__file__).resolve().parent / "secrets"))
LICENSE_TG = Path(os.environ.get("MONSRV_LICENSE_TELEGRAM", "/opt/scalp-license/secrets/telegram.json"))
PER_MIN = 6
STALE_S = 30 * 60
EMOJI = {"CRITICAL": "🚨", "ORDER_REJECTED": "🚫", "SYSTEM_ALERT": "⚠️"}

_q: "queue.Queue[tuple]" = queue.Queue(maxsize=2000)
_sent: Dict[str, List[float]] = {}
_folded: Dict[str, int] = {}
_started = [False]


def _read(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception as e:
        print(f"[RELAY] {p} unreadable: {e}")
        return {}


def config() -> Dict[str, Any]:
    mon = _read(SECRETS_DIR / "monitor.json")
    lic = _read(LICENSE_TG)
    token = str(mon.get("bot_token") or lic.get("bot_token") or "").strip()
    targets = [t for t in (mon.get("telegram_targets") or []) if isinstance(t, dict) and t.get("chat_id")]
    if not targets and lic.get("chat_id"):
        targets = [{"chat_id": str(lic["chat_id"]), "admin_devices": False}]
    return {"bot_token": token, "targets": targets}


def _text(dev: Dict[str, Any], ev: Dict[str, Any]) -> str:
    d = ev.get("data") or {}
    label = escape(str(dev.get("label") or "install"))
    strat = escape(str(d.get("codename") or "")) if d.get("codename") else ""
    head = {"CRITICAL": "CRITICAL", "ORDER_REJECTED": "ORDER REJECTED",
            "SYSTEM_ALERT": escape(str(d.get("title") or "SYSTEM ALERT"))}.get(ev["kind"], ev["kind"])
    lines = [f"{EMOJI.get(ev['kind'], '⚠️')} <b>{head}</b> · {label}" + (f" · {strat}" if strat else "")]
    if d.get("symbol"):
        lines.append(f"Symbol: <code>{escape(str(d['symbol']))}</code>")
    lines.append(escape(str(d.get("message") or ""))[:900])
    lines.append(datetime.fromtimestamp(float(ev.get("ts") or time.time()), IST).strftime("Time: %H:%M:%S IST"))
    return "\n".join(x for x in lines if x)


def plan(dev: Dict[str, Any], new_events: List[Dict[str, Any]], now: float) -> List[str]:
    """The messages to send for this batch (pure; tests call it)."""
    alerts = [e for e in new_events if e["kind"] in EMOJI]
    if not alerts:
        return []
    key = dev["key"]
    fresh = [e for e in alerts if now - float(e.get("ts") or now) <= STALE_S]
    stale = [e for e in alerts if e not in fresh]
    out: List[str] = []
    if stale:
        crit = sum(1 for e in stale if e["kind"] != "SYSTEM_ALERT")
        out.append(f"🕘 <b>{escape(str(dev.get('label') or 'install'))}</b> reconnected with "
                   f"{len(stale)} older alert(s) ({crit} critical) — see the Fleet Monitor portal")
    recent = [t for t in _sent.get(key, []) if t > now - 60]
    for e in fresh:
        if len(recent) < PER_MIN:
            out.append(_text(dev, e))
            recent.append(now)
        else:
            _folded[key] = _folded.get(key, 0) + 1
    if _folded.get(key) and len(recent) < PER_MIN:
        out.append(f"➕ {_folded.pop(key)} more alert(s) from <b>{escape(str(dev.get('label') or 'install'))}</b>"
                   " — see the Fleet Monitor portal")
        recent.append(now)
    _sent[key] = recent
    return out


def submit(dev: Dict[str, Any], new_events: List[Dict[str, Any]]) -> int:
    """Queue this batch's messages. Returns how many were queued."""
    try:
        cfg = config()
        if not cfg["bot_token"] or not cfg["targets"]:
            return 0
        msgs = plan(dev, new_events, time.time())
        n = 0
        for t in cfg["targets"]:
            if str(dev.get("tier") or "").upper() == "ADMIN" and not t.get("admin_devices"):
                continue
            for m in msgs:
                try:
                    _q.put_nowait((cfg["bot_token"], str(t["chat_id"]), m))
                    n += 1
                except queue.Full:
                    print("[RELAY] queue full — message dropped")
        _ensure_worker()
        return n
    except Exception as e:
        print(f"[RELAY] submit failed: {e!r}")
        return 0


def _send(token: str, chat_id: str, text: str) -> bool:
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception as e:
        print(f"[RELAY] send failed: {type(e).__name__}")
        return False


def _worker() -> None:
    while True:
        token, chat_id, text = _q.get()
        if not _send(token, chat_id, text):
            time.sleep(2)
            _send(token, chat_id, text)
        time.sleep(0.05)


def _ensure_worker() -> None:
    if not _started[0]:
        _started[0] = True
        threading.Thread(target=_worker, name="relay", daemon=True).start()


def _reset_for_tests() -> None:
    _sent.clear()
    _folded.clear()
