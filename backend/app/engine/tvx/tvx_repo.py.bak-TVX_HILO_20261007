# backend/app/engine/tvx/tvx_repo.py
#
# ── TVX_V1_20261007 ── TVX persistence. OWN tables only (tvx_trades,
# tvx_alerts, tvx_kv) — nothing here reads or writes paper_trades / trades,
# so no fleet path (NSE square-off, MTM guard, Trades page) ever sees an MCX
# row. "One position at a time" is enforced by the database, not just the
# engine: a partial UNIQUE index allows at most one OPEN row.

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS tvx_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT NOT NULL,                 -- OPEN | CLOSED
    side            TEXT NOT NULL,                 -- CE | PE
    symbol          TEXT NOT NULL,                 -- MCX tradingsymbol
    token           INTEGER,
    strike          REAL,
    expiry          TEXT,
    fut_symbol      TEXT,
    fut_px          REAL,                          -- ATM reference at entry
    alert_px        REAL,                          -- {{close}} on the alert
    entry_alert_id  INTEGER,
    entry_signal    TEXT,
    signal_bar      TEXT,
    entry_time      INTEGER NOT NULL,              -- epoch s
    entry_price     REAL NOT NULL,
    entry_src       TEXT,                          -- ASK | LTP
    entry_bid       REAL, entry_ask REAL, entry_ltp REAL,
    sl_points       REAL, sl_price REAL,
    lots            INTEGER, units_per_lot INTEGER, qty INTEGER,
    exit_time       INTEGER,
    exit_price      REAL,
    exit_src        TEXT,                          -- BID | LTP
    exit_bid        REAL, exit_ask REAL, exit_ltp REAL,
    exit_reason     TEXT,                          -- SL | T1 | SIGNAL | EOD | EOD_LATE | MANUAL
    exit_alert_id   INTEGER,
    pnl_points      REAL, gross REAL, charges REAL, net REAL, charges_json TEXT,
    mfe_points      REAL, mae_points REAL          -- best / worst LTP excursion while open
);
CREATE UNIQUE INDEX IF NOT EXISTS tvx_one_open ON tvx_trades(status) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS tvx_trades_exit ON tvx_trades(exit_time);

CREATE TABLE IF NOT EXISTS tvx_alerts (
    alert_id     INTEGER PRIMARY KEY,              -- relay id
    received_at  TEXT,
    sig          TEXT, ticker TEXT, tf TEXT, price REAL, bar_time TEXT,
    processed_at INTEGER,
    action       TEXT,                             -- what the engine did
    note         TEXT
);

CREATE TABLE IF NOT EXISTS tvx_kv (k TEXT PRIMARY KEY, v TEXT);
"""

TRADE_COLS = (
    "side symbol token strike expiry fut_symbol fut_px alert_px entry_alert_id "
    "entry_signal signal_bar entry_time entry_price entry_src entry_bid entry_ask "
    "entry_ltp sl_points sl_price lots units_per_lot qty"
).split()


class TvxRepo:
    def __init__(self, db_path=None):
        if db_path is None:
            from app.utils.app_paths import DB_PATH, ensure_app_dirs
            ensure_app_dirs()
            db_path = DB_PATH
        self.path = str(Path(db_path))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)

    # ── helpers ──
    def _one(self, sql, args=()):
        with self._lock:
            r = self._conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _run(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args)

    # ── kv ──
    def kv_get(self, k: str) -> Optional[str]:
        r = self._one("SELECT v FROM tvx_kv WHERE k = ?", (k,))
        return r["v"] if r else None

    def kv_set(self, k: str, v: Optional[str]) -> None:
        if v is None:
            self._run("DELETE FROM tvx_kv WHERE k = ?", (k,))
        else:
            self._run("INSERT INTO tvx_kv(k, v) VALUES(?, ?) "
                      "ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, str(v)))

    def get_cursor(self) -> Optional[int]:
        v = self.kv_get("cursor")
        return int(v) if v is not None else None

    def set_cursor(self, alert_id: int) -> None:
        self.kv_set("cursor", str(int(alert_id)))

    def get_pending_exit(self) -> Optional[Dict]:
        v = self.kv_get("pending_exit")
        return json.loads(v) if v else None

    def set_pending_exit(self, trade_id: Optional[int], reason: str = "",
                         alert_id: Optional[int] = None) -> None:
        self.kv_set("pending_exit", None if trade_id is None else json.dumps(
            {"trade_id": trade_id, "reason": reason, "alert_id": alert_id}))

    # ── alerts ──
    def alert_seen(self, alert_id: int) -> bool:
        return self._one("SELECT 1 AS x FROM tvx_alerts WHERE alert_id = ?", (alert_id,)) is not None

    def record_alert(self, a: Dict, processed_at: int, action: str, note: str = "") -> None:
        self._run(
            "INSERT INTO tvx_alerts(alert_id, received_at, sig, ticker, tf, price, bar_time, "
            "processed_at, action, note) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(alert_id) DO UPDATE SET action = excluded.action, note = excluded.note, "
            "processed_at = excluded.processed_at",
            (int(a["id"]), a.get("received_at"), a.get("sig"), a.get("ticker"), a.get("tf"),
             a.get("price"), a.get("bar_time"), processed_at, action, note[:500]))

    def recent_alerts(self, limit: int = 40) -> List[Dict]:
        return self._all("SELECT * FROM tvx_alerts ORDER BY alert_id DESC LIMIT ?", (limit,))

    # ── trades ──
    def open_position(self) -> Optional[Dict]:
        return self._one("SELECT * FROM tvx_trades WHERE status = 'OPEN'")

    def open_trade(self, row: Dict) -> int:
        vals = [row.get(c) for c in TRADE_COLS]
        cur = self._run(
            f"INSERT INTO tvx_trades(status, {', '.join(TRADE_COLS)}, mfe_points, mae_points) "
            f"VALUES('OPEN', {', '.join('?' * len(TRADE_COLS))}, 0, 0)", vals)
        return int(cur.lastrowid)

    def update_excursion(self, trade_id: int, mfe: float, mae: float) -> None:
        self._run("UPDATE tvx_trades SET mfe_points = ?, mae_points = ? WHERE id = ? AND status='OPEN'",
                  (mfe, mae, trade_id))

    def close_trade(self, trade_id: int, **f) -> bool:
        cur = self._run(
            "UPDATE tvx_trades SET status='CLOSED', exit_time=?, exit_price=?, exit_src=?, "
            "exit_bid=?, exit_ask=?, exit_ltp=?, exit_reason=?, exit_alert_id=?, pnl_points=?, "
            "gross=?, charges=?, net=?, charges_json=? WHERE id=? AND status='OPEN'",
            (f["exit_time"], f["exit_price"], f["exit_src"], f.get("exit_bid"), f.get("exit_ask"),
             f.get("exit_ltp"), f["exit_reason"], f.get("exit_alert_id"), f["pnl_points"],
             f["gross"], f["charges"], f["net"], json.dumps(f.get("charges_detail") or {}),
             trade_id))
        return cur.rowcount == 1

    def closed_trades(self, limit: int = 200) -> List[Dict]:
        return self._all("SELECT * FROM tvx_trades WHERE status='CLOSED' "
                         "ORDER BY exit_time DESC, id DESC LIMIT ?", (limit,))

    def closed_between(self, t0: int, t1: int) -> List[Dict]:
        return self._all("SELECT * FROM tvx_trades WHERE status='CLOSED' AND exit_time >= ? "
                         "AND exit_time < ? ORDER BY exit_time", (t0, t1))

    def all_closed(self) -> List[Dict]:
        return self._all("SELECT * FROM tvx_trades WHERE status='CLOSED' ORDER BY exit_time, id")
