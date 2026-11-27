"""
monitor_server/store.py — ── FLEET_MONITOR_20261127 ── (TD-110)

The Fleet Monitor's own SQLite database (monitor.db). Separate from the
license server's licenses.db, which it only ever opens READ-ONLY to check a
device's key + machine id and to read the licence label and tier.

Tables
  devices        one row per licence key that has reported
  events         every shipped event, de-duplicated on (device, epoch, seq)
  snapshot       the latest LIVE snapshot per device (the heartbeat)
  day_summary    the last snapshot's totals + rows per device per IST day
  legs           every LIVE leg ever seen in a snapshot, by leg id (trade history)
  acks           who acknowledged an alert, when, with a note
  users          portal logins (see auth.py)
  sessions       portal sessions, token stored as sha256
  audit          logins, acks and admin actions
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = Path(os.environ.get("MONSRV_DB", Path(__file__).resolve().parent / "monitor.db"))
LICENSE_DB = Path(os.environ.get("MONSRV_LICENSE_DB", "/opt/scalp-license/licenses.db"))

ALERT_KINDS = ("CRITICAL", "ORDER_REJECTED", "SYSTEM_ALERT")
TRADE_KINDS = ("TRADE_ENTRY", "TRADE_EXIT", "GROUP_ENTRY", "GROUP_EXIT")
KNOWN_KINDS = ALERT_KINDS + TRADE_KINDS
KEEP_EVENTS_DAYS = 400


def set_paths(db: str, license_db: str) -> None:
    """Tests point the store at temp files."""
    global DB_PATH, LICENSE_DB
    DB_PATH, LICENSE_DB = Path(db), Path(license_db)


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  key TEXT PRIMARY KEY, machine_id TEXT, label TEXT, tier TEXT, app_version TEXT,
  first_seen REAL, last_seen REAL, last_ip TEXT, epoch TEXT,
  enabled INTEGER NOT NULL DEFAULT 1, dropped INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_key TEXT NOT NULL, epoch TEXT NOT NULL,
  seq INTEGER NOT NULL, ts REAL NOT NULL, received_at REAL NOT NULL, kind TEXT NOT NULL,
  strategy_id TEXT, codename TEXT, severity TEXT, summary TEXT, data TEXT NOT NULL,
  UNIQUE (device_key, epoch, seq));
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_dev_ts ON events (device_key, ts);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts);
CREATE TABLE IF NOT EXISTS snapshot (
  device_key TEXT PRIMARY KEY, ts REAL NOT NULL, day TEXT, market_open INTEGER, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS day_summary (
  device_key TEXT NOT NULL, day TEXT NOT NULL, ts REAL NOT NULL, totals TEXT NOT NULL,
  rows TEXT NOT NULL, PRIMARY KEY (device_key, day));
CREATE TABLE IF NOT EXISTS legs (
  device_key TEXT NOT NULL, leg_id TEXT NOT NULL, strategy_id TEXT, day TEXT,
  entry_ts INTEGER, exit_ts INTEGER, is_open INTEGER, data TEXT NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (device_key, leg_id));
CREATE INDEX IF NOT EXISTS legs_day ON legs (day);
CREATE TABLE IF NOT EXISTS acks (
  event_id INTEGER PRIMARY KEY, username TEXT NOT NULL, ts REAL NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, totp_secret TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','viewer')), created_at REAL NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
  locked_until REAL NOT NULL DEFAULT 0, last_totp_step INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
  created REAL NOT NULL, last_seen REAL NOT NULL, ip TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, username TEXT, action TEXT NOT NULL,
  detail TEXT, ip TEXT);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def ist_day(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d")


def audit(username: Optional[str], action: str, detail: str = "", ip: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO audit (ts, username, action, detail, ip) VALUES (?,?,?,?,?)",
                  (time.time(), username, action, detail[:500], ip[:64]))


# ── licence check (read-only) ────────────────────────────────────────
def licence_for(key: str, machine_id: str, legacy_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The licence row when key exists and is bound to one of the ids, else
    None. A revoked or expired licence still reports: it may hold a position."""
    if not key or not machine_id or not LICENSE_DB.exists():
        return None
    c = sqlite3.connect(f"file:{LICENSE_DB}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT key, label, tier, machine_id, revoked FROM licenses WHERE key=?",
                      (key.strip().upper(),)).fetchone()
    finally:
        c.close()
    if r is None or not r["machine_id"]:
        return None
    if r["machine_id"] not in tuple(x for x in (machine_id, legacy_id) if x):
        return None
    return dict(r)


# ── ingest ───────────────────────────────────────────────────────────
def _summary(kind: str, d: Dict[str, Any]) -> str:
    name = d.get("codename") or d.get("strategy_id") or ""
    if kind in ALERT_KINDS:
        head = d.get("title") or ""
        msg = d.get("message") or ""
        return (f"{head}: {msg}" if head else msg)[:300]
    if kind in ("GROUP_ENTRY", "GROUP_EXIT"):
        legs = d.get("legs") or []
        tail = f" · P&L {d['pnl']:+,.0f}" if isinstance(d.get("pnl"), (int, float)) else ""
        return f"{name} {len(legs)}-leg {'entry' if kind == 'GROUP_ENTRY' else 'exit'}" \
               f"{(' ' + d['reason']) if d.get('reason') else ''}{tail}"[:300]
    px = d.get("exit_price") if kind == "TRADE_EXIT" else d.get("entry_price")
    tail = f" · P&L {d['pnl']:+,.0f}" if kind == "TRADE_EXIT" and isinstance(d.get("pnl"), (int, float)) else ""
    return (f"{name} {'exit' if kind == 'TRADE_EXIT' else 'entry'} {d.get('symbol') or ''} "
            f"{d.get('side') or ''} {d.get('qty') or ''} @ {px if px is not None else '—'}"
            f"{(' ' + d['reason']) if kind == 'TRADE_EXIT' and d.get('reason') else ''}{tail}")[:300]


def upsert_device(lic: Dict[str, Any], machine_id: str, app_version: Optional[str],
                  ip: str, epoch: str, dropped: int, now: float) -> Dict[str, Any]:
    with conn() as c:
        c.execute(
            "INSERT INTO devices (key, machine_id, label, tier, app_version, first_seen, last_seen,"
            " last_ip, epoch, dropped) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET machine_id=excluded.machine_id, label=excluded.label,"
            " tier=excluded.tier, app_version=COALESCE(excluded.app_version, devices.app_version),"
            " last_seen=excluded.last_seen, last_ip=excluded.last_ip, epoch=excluded.epoch,"
            " dropped=excluded.dropped",
            (lic["key"], machine_id, lic.get("label") or lic["key"][:8], lic.get("tier"),
             (app_version or None), now, now, ip[:64], epoch[:64], int(dropped or 0)))
        return dict(c.execute("SELECT * FROM devices WHERE key=?", (lic["key"],)).fetchone())


def store_events(device_key: str, epoch: str, events: Iterable[Dict[str, Any]],
                 now: float) -> Dict[str, Any]:
    """Insert new events (duplicates ignored). Returns {"acked": max seq seen
    in a valid event, "new": [rows inserted now]}."""
    acked = None
    new: List[Dict[str, Any]] = []
    with conn() as c:
        for ev in events:
            try:
                seq = int(ev["seq"])
                kind = str(ev.get("kind") or "")[:40]
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                ts = float(ev.get("ts") or now)
            except Exception:
                continue
            acked = seq if acked is None else max(acked, seq)
            if kind not in KNOWN_KINDS:
                continue
            body = json.dumps(data, separators=(",", ":"))[:20000]
            cur = c.execute(
                "INSERT OR IGNORE INTO events (device_key, epoch, seq, ts, received_at, kind,"
                " strategy_id, codename, severity, summary, data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (device_key, epoch, seq, ts, now, kind, str(data.get("strategy_id") or "")[:40] or None,
                 str(data.get("codename") or "")[:60] or None,
                 str(data.get("severity") or "")[:10] or None, _summary(kind, data), body))
            if cur.rowcount:
                new.append({"id": cur.lastrowid, "device_key": device_key, "seq": seq, "ts": ts,
                            "kind": kind, "data": data, "summary": _summary(kind, data)})
    return {"acked": acked, "new": new}


def store_snapshot(device_key: str, snap: Dict[str, Any], now: float) -> None:
    if not isinstance(snap, dict):
        return
    body = json.dumps(snap, separators=(",", ":"))[:1_500_000]
    day = str(snap.get("day") or ist_day(now))[:10]
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO snapshot (device_key, ts, day, market_open, data)"
                  " VALUES (?,?,?,?,?)", (device_key, now, day, 1 if snap.get("market_open") else 0, body))
        if snap.get("error"):
            return
        c.execute("INSERT OR REPLACE INTO day_summary (device_key, day, ts, totals, rows)"
                  " VALUES (?,?,?,?,?)",
                  (device_key, day, now, json.dumps(snap.get("totals") or {}),
                   json.dumps(snap.get("rows") or [])))
        for lg in (snap.get("legs") or [])[:1000]:
            if not isinstance(lg, dict) or not lg.get("id"):
                continue
            ets, xts = lg.get("entry_ts"), lg.get("exit_ts")
            c.execute(
                "INSERT OR REPLACE INTO legs (device_key, leg_id, strategy_id, day, entry_ts, exit_ts,"
                " is_open, data, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (device_key, str(lg["id"])[:80], str(lg.get("strategy_id") or "")[:40],
                 ist_day(xts or ets) or day, int(ets or 0) or None, int(xts or 0) or None,
                 1 if lg.get("open") else 0, json.dumps(lg, separators=(",", ":")), now))


# ── reads for the portal ─────────────────────────────────────────────
def _rows(sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def devices() -> List[Dict[str, Any]]:
    return _rows("SELECT * FROM devices ORDER BY label COLLATE NOCASE")


def device(key: str) -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM devices WHERE key=?", (key,))
    return r[0] if r else None


def set_enabled(key: str, enabled: bool) -> None:
    with conn() as c:
        c.execute("UPDATE devices SET enabled=? WHERE key=?", (1 if enabled else 0, key))


def snapshots() -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in _rows("SELECT * FROM snapshot"):
        try:
            r["data"] = json.loads(r["data"])
        except Exception:
            r["data"] = {}
        out[r["device_key"]] = r
    return out


def unacked_counts(since: float) -> Dict[str, int]:
    rows = _rows("SELECT e.device_key, COUNT(*) AS n FROM events e LEFT JOIN acks a ON a.event_id=e.id"
                 " WHERE e.kind IN ('CRITICAL','ORDER_REJECTED') AND a.event_id IS NULL AND e.ts>=?"
                 " GROUP BY e.device_key", (since,))
    return {r["device_key"]: r["n"] for r in rows}


def alerts(device_key: Optional[str], unacked_only: bool, since: float, limit: int = 300) -> List[Dict[str, Any]]:
    sql = ("SELECT e.*, a.username AS ack_user, a.ts AS ack_ts, a.note AS ack_note FROM events e"
           " LEFT JOIN acks a ON a.event_id=e.id WHERE e.kind IN ('CRITICAL','ORDER_REJECTED','SYSTEM_ALERT')"
           " AND e.ts>=?")
    args: list = [since]
    if device_key:
        sql += " AND e.device_key=?"
        args.append(device_key)
    if unacked_only:
        sql += " AND a.event_id IS NULL AND e.kind IN ('CRITICAL','ORDER_REJECTED')"
    sql += " ORDER BY e.ts DESC LIMIT ?"
    args.append(int(limit))
    return _rows(sql, tuple(args))


def ack(event_id: int, username: str, note: str) -> bool:
    with conn() as c:
        if not c.execute("SELECT 1 FROM events WHERE id=? AND kind IN ('CRITICAL','ORDER_REJECTED','SYSTEM_ALERT')",
                         (int(event_id),)).fetchone():
            return False
        c.execute("INSERT OR IGNORE INTO acks (event_id, username, ts, note) VALUES (?,?,?,?)",
                  (int(event_id), username, time.time(), (note or "")[:300]))
        return True


def events(day: str, device_key: Optional[str], kinds: Iterable[str], limit: int = 1000) -> List[Dict[str, Any]]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp()
    kinds = tuple(kinds)
    sql = (f"SELECT * FROM events WHERE ts>=? AND ts<? AND kind IN ({','.join('?' * len(kinds))})")
    args: list = [start, start + 86400, *kinds]
    if device_key:
        sql += " AND device_key=?"
        args.append(device_key)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    return _rows(sql, tuple(args))


def legs(day_from: str, day_to: str, device_key: Optional[str], strategy_id: Optional[str],
         limit: int = 2000) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM legs WHERE ((day>=? AND day<=?) OR is_open=1)"
    args: list = [day_from, day_to]
    if device_key:
        sql += " AND device_key=?"
        args.append(device_key)
    if strategy_id:
        sql += " AND strategy_id=?"
        args.append(strategy_id)
    sql += " ORDER BY COALESCE(exit_ts, entry_ts) DESC LIMIT ?"
    args.append(int(limit))
    out = []
    for r in _rows(sql, tuple(args)):
        try:
            r["data"] = json.loads(r["data"])
        except Exception:
            r["data"] = {}
        out.append(r)
    return out


def day_summaries(day: str) -> List[Dict[str, Any]]:
    out = []
    for r in _rows("SELECT * FROM day_summary WHERE day=?", (day,)):
        r["totals"] = json.loads(r["totals"] or "{}")
        r["rows"] = json.loads(r["rows"] or "[]")
        out.append(r)
    return out


def event_counts(day: str) -> Dict[str, Dict[str, int]]:
    """Per device: trade events streamed that day (for the summary's check)."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp()
    out: Dict[str, Dict[str, int]] = {}
    for r in _rows("SELECT device_key, kind, COUNT(*) AS n FROM events WHERE ts>=? AND ts<?"
                   " GROUP BY device_key, kind", (start, start + 86400)):
        out.setdefault(r["device_key"], {})[r["kind"]] = r["n"]
    return out


def housekeeping(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with conn() as c:
        c.execute("DELETE FROM events WHERE ts < ? AND id NOT IN (SELECT event_id FROM acks)",
                  (now - KEEP_EVENTS_DAYS * 86400,))
        c.execute("DELETE FROM audit WHERE ts < ?", (now - KEEP_EVENTS_DAYS * 86400,))
