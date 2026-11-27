# backend/app/monitor/outbox.py — ── FLEET_MONITOR_20261127 ── (TD-110)
#
# Durable local queue of monitor events. Its own SQLite file
# (~/.scalp-app/state/monitor_outbox.db), never app.db, so the outbox can
# never take the trading database's lock.
#
#   enqueue(kind, payload)  one INSERT; never raises; returns the seq or None
#   pending(limit)          oldest unsent rows
#   mark_sent(upto_seq)     rows <= upto_seq are sent
#   prune()                 sent rows older than KEEP_SENT_S go; unsent rows
#                           are capped at MAX_UNSENT (oldest dropped, counted)
#   epoch()                 random id made with the file: the server de-dups on
#                           (device, epoch, seq), so a deleted outbox that
#                           restarts seq at 1 can never collide with old rows
#
# A test run (SCALP_TEST_MODE=1, pytest, or a test_*.py script) never touches
# ~/.scalp-app/state (DEF-105): it uses SCALP_MONITOR_OUTBOX if set, else a
# per-process file in the system temp folder.
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

KEEP_SENT_S = 7 * 86400
MAX_UNSENT = 20000
MAX_PAYLOAD_BYTES = 16000

_lock = threading.Lock()
_ready: Dict[str, bool] = {}
_dropped = [0]


def _test_run() -> bool:
    tm = os.environ.get("SCALP_TEST_MODE")
    if tm in ("1", "0"):
        return tm == "1"
    main = os.path.basename(sys.argv[0]) if getattr(sys, "argv", None) else ""
    return "pytest" in sys.modules or main.startswith("test_")


def db_path() -> str:
    env = os.environ.get("SCALP_MONITOR_OUTBOX")
    if env:
        return env
    if _test_run():
        return os.path.join(tempfile.gettempdir(), f"scalp-monitor-outbox-test-{os.getpid()}.db")
    return str(Path.home() / ".scalp-app" / "state" / "monitor_outbox.db")


def _conn() -> sqlite3.Connection:
    p = db_path()
    if not _ready.get(p):
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    c = sqlite3.connect(p, timeout=2.0)
    if not _ready.get(p):
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS outbox ("
                  " seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
                  " kind TEXT NOT NULL, payload TEXT NOT NULL, sent_at REAL)")
        c.execute("CREATE INDEX IF NOT EXISTS outbox_unsent ON outbox(sent_at, seq)")
        c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        c.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('epoch', ?)", (uuid.uuid4().hex,))
        c.commit()
        _ready[p] = True
    return c


def enqueue(kind: str, payload: dict) -> Optional[int]:
    """One INSERT. Never raises: a monitor fault must never reach the caller."""
    try:
        body = json.dumps(payload, default=str, separators=(",", ":"))
        if len(body) > MAX_PAYLOAD_BYTES:
            body = json.dumps({"truncated": True, "kind": kind,
                               "head": body[:MAX_PAYLOAD_BYTES - 200]})
        with _lock:
            c = _conn()
            try:
                cur = c.execute("INSERT INTO outbox (ts, kind, payload) VALUES (?,?,?)",
                                (time.time(), str(kind)[:40], body))
                c.commit()
                return int(cur.lastrowid)
            finally:
                c.close()
    except Exception:
        return None


def pending(limit: int = 200) -> List[dict]:
    with _lock:
        c = _conn()
        try:
            rows = c.execute("SELECT seq, ts, kind, payload FROM outbox WHERE sent_at IS NULL"
                             " ORDER BY seq LIMIT ?", (int(limit),)).fetchall()
        finally:
            c.close()
    out = []
    for seq, ts, kind, payload in rows:
        try:
            data = json.loads(payload)
        except Exception:
            data = {"unreadable": True}
        out.append({"seq": seq, "ts": ts, "kind": kind, "data": data})
    return out


def mark_sent(upto_seq: int) -> int:
    with _lock:
        c = _conn()
        try:
            n = c.execute("UPDATE outbox SET sent_at=? WHERE sent_at IS NULL AND seq<=?",
                          (time.time(), int(upto_seq))).rowcount
            c.commit()
            return n
        finally:
            c.close()


def unsent_count() -> int:
    with _lock:
        c = _conn()
        try:
            return int(c.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0])
        finally:
            c.close()


def prune(now: Optional[float] = None) -> int:
    """Sent rows older than KEEP_SENT_S go; unsent rows beyond MAX_UNSENT are
    dropped oldest first and counted in dropped_total()."""
    now = time.time() if now is None else now
    with _lock:
        c = _conn()
        try:
            n = c.execute("DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at < ?",
                          (now - KEEP_SENT_S,)).rowcount
            extra = int(c.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0]) - MAX_UNSENT
            if extra > 0:
                c.execute("DELETE FROM outbox WHERE seq IN (SELECT seq FROM outbox WHERE sent_at IS NULL"
                          " ORDER BY seq LIMIT ?)", (extra,))
                _dropped[0] += extra
                n += extra
            c.commit()
            return n
        finally:
            c.close()


def dropped_total() -> int:
    return _dropped[0]


def epoch() -> str:
    with _lock:
        c = _conn()
        try:
            return str(c.execute("SELECT v FROM meta WHERE k='epoch'").fetchone()[0])
        finally:
            c.close()


def _reset_for_tests() -> None:
    _ready.clear()
    _dropped[0] = 0
