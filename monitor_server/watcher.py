"""
monitor_server/watcher.py — ── FLEET_WATCH_20261127 ── (TD-112, Phase 2)

What lets friends monitor without watching the portal. A background thread in
the monitor, every TICK_S, sends to the Telegram targets (relay.broadcast):

  OFFLINE      a machine silent for OFFLINE_S during market hours on a trading
               day → "🔴 offline · last seen HH:MM" once; "✅ back online" when it
               returns. A machine that never started today counts (last seen an
               earlier day).
  SUMMARY      at SUMMARY_AT (15:50 IST) on a trading day, one fleet message:
               per machine net, realised, charges, closed trades, carried legs;
               fleet total. Built from day_summary (each machine's last snapshot).
  REMINDER     a CRITICAL / ORDER_REJECTED nobody acknowledged REMIND_AFTER_S
               after it happened (and not older than REMIND_MAX_AGE_S) → posted
               once more with a link to the Alerts page.

Which machines: the same per-machine "Telegram alerts" switch as criticals
(devices.relay). A machine switched off gets none of these.

Trading day without a holiday list: today is a trading day once ANY machine
(switched on or off) has sent a snapshot saying the market is open; that is
the app's own NSE-calendar answer. Market hours = trading day and 09:15–15:30
IST. If no machine is running at all, nothing is known and nothing is sent.

Every notice is recorded in the `notices` table first, so a restart never sends
it twice. Never raises; the thread logs and carries on.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional

import relay
import store

TICK_S = 30
OFFLINE_S = 180
BACK_S = 120
MARKET_FROM = 9 * 60 + 15
MARKET_TO = 15 * 60 + 30
SUMMARY_AT = 15 * 60 + 50
REMIND_AFTER_S = 600
REMIND_MAX_AGE_S = 2 * 3600
PORTAL = "https://139-59-8-202.sslip.io"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (key TEXT PRIMARY KEY, ts REAL NOT NULL, detail TEXT);
"""
_started = [False]


def init() -> None:
    with store.conn() as c:
        c.executescript(SCHEMA)


def _ist(now: float) -> datetime:
    return datetime.fromtimestamp(now, store.IST)


def _minute(now: float) -> int:
    d = _ist(now)
    return d.hour * 60 + d.minute


def _has(key: str) -> bool:
    with store.conn() as c:
        return c.execute("SELECT 1 FROM notices WHERE key=?", (key,)).fetchone() is not None


def _mark(key: str, now: float, detail: str = "") -> bool:
    """Record a notice; False if it was already recorded (another tick sent it)."""
    with store.conn() as c:
        return c.execute("INSERT OR IGNORE INTO notices (key, ts, detail) VALUES (?,?,?)",
                         (key, now, detail[:200])).rowcount == 1


def _unmark(key: str) -> None:
    with store.conn() as c:
        c.execute("DELETE FROM notices WHERE key=?", (key,))


def trading_day(now: float) -> bool:
    """True once any machine's snapshot today said the market is open."""
    day = store.ist_day(now)
    if _has(f"tradingday:{day}"):
        return True
    with store.conn() as c:
        hit = c.execute("SELECT 1 FROM snapshot WHERE day=? AND market_open=1 LIMIT 1", (day,)).fetchone()
    if hit:
        _mark(f"tradingday:{day}", now)
        return True
    return False


def market_hours(now: float) -> bool:
    return MARKET_FROM <= _minute(now) <= MARKET_TO and trading_day(now)


def _hm(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    d = _ist(float(ts))
    return d.strftime("%H:%M") if store.ist_day(ts) == store.ist_day(now) else d.strftime("%d %b %H:%M")


def _on(dev: Dict[str, Any]) -> bool:
    return bool(dev.get("enabled", 1)) and bool(dev.get("relay", 1))


def check_offline(now: float) -> List[str]:
    out: List[str] = []
    if not market_hours(now):
        return out
    for d in store.devices():
        if not _on(d):
            continue
        key = f"offline:{d['key']}"
        age = now - float(d.get("last_seen") or 0)
        label = escape(str(d.get("label") or "machine"))
        if age > OFFLINE_S and not _has(key):
            if _mark(key, now):
                out.append(f"🔴 <b>OFFLINE</b> · {label}\nNo check-in for {int(age // 60)} min "
                           f"(last seen {_hm(d.get('last_seen'), now)} IST). Its open positions are not "
                           f"being reported.\n{PORTAL}")
        elif age <= BACK_S and _has(key):
            _unmark(key)
            out.append(f"✅ <b>BACK ONLINE</b> · {label}\nChecking in again ({_ist(now).strftime('%H:%M')} IST).")
    return out


def _money(v: Any) -> str:
    try:
        x = round(float(v))
    except Exception:
        return "—"
    return ("+" if x > 0 else "−" if x < 0 else "") + f"₹{abs(x):,}"


def daily_summary(now: float) -> Optional[str]:
    if _minute(now) < SUMMARY_AT or not trading_day(now):
        return None
    day = store.ist_day(now)
    if _has(f"summary:{day}"):
        return None
    devs = {d["key"]: d for d in store.devices() if _on(d)}
    rows = [r for r in store.day_summaries(day) if r["device_key"] in devs]
    if not _mark(f"summary:{day}", now):
        return None
    if not rows:
        return None
    lines = [f"📊 <b>FLEET SUMMARY</b> · {_ist(now).strftime('%a %d %b')}"]
    fleet = 0.0
    for r in sorted(rows, key=lambda r: str(devs[r["device_key"]].get("label") or "")):
        t = r["totals"] or {}
        fleet += float(t.get("net") or 0)
        lines.append(f"\n<b>{escape(str(devs[r['device_key']].get('label') or 'machine'))}</b>: "
                     f"{_money(t.get('net'))}  (realised {_money(t.get('realised'))}, charges "
                     f"{_money(-float(t.get('charges') or 0))}) · {int(t.get('closed_trades') or 0)} closed"
                     + (f" · {int(t.get('open_legs'))} leg(s) carried" if t.get("open_legs") else ""))
        for s in r["rows"] or []:
            if s.get("state") != "IDLE":
                lines.append(f"   {escape(str(s.get('codename') or s.get('id')))}: {_money(s.get('net'))}")
    lines.append(f"\n<b>Fleet net: {_money(fleet)}</b>\n{PORTAL}/day")
    return "\n".join(lines)


def reminders(now: float) -> List[str]:
    out: List[str] = []
    devs = {d["key"]: d for d in store.devices()}
    with store.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT e.id, e.device_key, e.ts, e.kind, e.summary FROM events e LEFT JOIN acks a ON a.event_id=e.id"
            " WHERE e.kind IN ('CRITICAL','ORDER_REJECTED') AND a.event_id IS NULL AND e.ts<=? AND e.ts>=?",
            (now - REMIND_AFTER_S, now - REMIND_MAX_AGE_S))]
    for r in rows:
        d = devs.get(r["device_key"])
        if not d or not _on(d) or _has(f"remind:{r['id']}"):
            continue
        if _mark(f"remind:{r['id']}", now):
            out.append(f"⏰ <b>NOT ACKNOWLEDGED</b> for {int((now - r['ts']) // 60)} min · "
                       f"{escape(str(d.get('label') or 'machine'))}\n{escape(str(r['summary'] or ''))[:600]}\n"
                       f"Acknowledge: {PORTAL}/alerts?unacked=1")
    return out


def tick(now: Optional[float] = None) -> int:
    """One pass. Returns how many messages were queued."""
    now = time.time() if now is None else now
    msgs: List[str] = []
    for fn in (check_offline, reminders):
        try:
            msgs += fn(now)
        except Exception as e:
            print(f"[WATCH] {fn.__name__} failed: {e!r}")
    try:
        s = daily_summary(now)
        if s:
            msgs.append(s)
    except Exception as e:
        print(f"[WATCH] summary failed: {e!r}")
    return sum(relay.broadcast(m) for m in msgs)


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:
            print(f"[WATCH] tick failed: {e!r}")
        time.sleep(TICK_S)


def start() -> None:
    if not _started[0]:
        _started[0] = True
        threading.Thread(target=_loop, name="watcher", daemon=True).start()
