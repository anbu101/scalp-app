# backend/app/engine/lvb/lvb_repo.py
#
# ── LVB_V1_20261023 ── persistence for "Tripwire" conditions.
#
# ONE table, lvb_conditions: a condition row also carries the one trade it
# may produce (symbol, fill, SL, spot target, exit). The trade itself is ALSO
# booked in paper_trades (strategy_name='LVB_V1') so the Trades page,
# Dashboard, Analytics, EOD card and the PAPER_LIVE shadow book see it with
# no extra work; paper_trade_id links the two.
#
# status: ARMED → TRIGGERED (entry filled; open until exit_ts is set)
#         ARMED → EXPIRED  (its EOD minute passed, or its day ended)
#         ARMED → CANCELLED (deleted on the page)
# A partial UNIQUE index makes "one trade per condition" a database fact.
#
# REMOVAL: drop this table (DROP TABLE lvb_conditions) — nothing else reads it.

from __future__ import annotations

import time
from typing import Callable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS lvb_conditions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day             TEXT NOT NULL,                 -- YYYY-MM-DD (IST) the condition is valid for
    instrument      TEXT NOT NULL,                 -- NIFTY | SENSEX
    upper           REAL,
    lower           REAL,
    prem_min        REAL NOT NULL,
    prem_max        REAL NOT NULL,
    sl_pct          REAL NOT NULL,
    tp_pts          REAL NOT NULL,
    eod             TEXT NOT NULL,                 -- HH:MM
    lots            INTEGER NOT NULL,
    status          TEXT NOT NULL,                 -- ARMED | TRIGGERED | EXPIRED | CANCELLED
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER,
    note            TEXT,                          -- last engine note (skipped breakout etc.)
    -- the trade (TRIGGERED only)
    side            TEXT,                          -- CE | PE
    trigger_bar_ts  INTEGER,                       -- 5m bar start epoch
    trigger_close   REAL,
    mode            TEXT,                          -- PAPER | LIVE (the book it was taken in)
    symbol          TEXT,
    token           INTEGER,
    exchange        TEXT,
    expiry          TEXT,
    strike          REAL,
    lot_size        INTEGER,
    qty             INTEGER,
    entry_ts        INTEGER,
    entry_px        REAL,
    entry_spot      REAL,
    sl_px           REAL,
    tp_spot         REAL,
    paper_trade_id  TEXT,
    exit_ts         INTEGER,
    exit_px         REAL,
    exit_spot       REAL,
    exit_reason     TEXT,                          -- SL | TP | EOD | MANUAL | KILL | BROKER_FLAT | STALE
    mark            REAL,                          -- last polled option LTP (panel)
    mark_spot       REAL
);
CREATE INDEX IF NOT EXISTS lvb_conditions_day ON lvb_conditions(day, status);
"""

COLS = ("id", "day", "instrument", "upper", "lower", "prem_min", "prem_max", "sl_pct", "tp_pts", "eod",
        "lots", "status", "created_at", "updated_at", "note", "side", "trigger_bar_ts", "trigger_close",
        "mode", "symbol", "token", "exchange", "expiry", "strike", "lot_size", "qty", "entry_ts",
        "entry_px", "entry_spot", "sl_px", "tp_spot", "paper_trade_id", "exit_ts", "exit_px",
        "exit_spot", "exit_reason", "mark", "mark_spot")
EDITABLE = ("upper", "lower", "prem_min", "prem_max", "sl_pct", "tp_pts", "eod", "lots", "instrument")


class LvbRepo:
    def __init__(self, conn_fn: Optional[Callable] = None, clock: Callable[[], float] = time.time):
        if conn_fn is None:
            from app.db.sqlite import get_conn as conn_fn          # thread-local, autocommit
        self._conn_fn = conn_fn
        self.clock = clock
        self._ready = False

    def conn(self):
        c = self._conn_fn()
        if not self._ready:
            c.executescript(SCHEMA)
            self._ready = True
        return c

    @staticmethod
    def _row(cur, r) -> dict:
        return dict(zip([d[0] for d in cur.description], tuple(r)))

    def _all(self, sql: str, args=()) -> List[dict]:
        cur = self.conn().execute(sql, args)
        return [self._row(cur, r) for r in cur.fetchall()]

    # ── reads ──
    def get(self, cid: int) -> Optional[dict]:
        rows = self._all("SELECT * FROM lvb_conditions WHERE id=?", (int(cid),))
        return rows[0] if rows else None

    def for_day(self, day: str) -> List[dict]:
        return self._all("SELECT * FROM lvb_conditions WHERE day=? ORDER BY id", (day,))

    def armed(self, day: str) -> List[dict]:
        return self._all("SELECT * FROM lvb_conditions WHERE day=? AND status='ARMED' ORDER BY id", (day,))

    def open_trades(self) -> List[dict]:
        """Every TRIGGERED row without an exit (any day — the engine closes a
        prior day's as STALE)."""
        return self._all("SELECT * FROM lvb_conditions WHERE status='TRIGGERED' AND exit_ts IS NULL ORDER BY id")

    def recent_closed(self, limit: int = 30) -> List[dict]:
        return self._all("SELECT * FROM lvb_conditions WHERE status='TRIGGERED' AND exit_ts IS NOT NULL "
                         "ORDER BY exit_ts DESC LIMIT ?", (int(limit),))

    # ── writes ──
    def create(self, day: str, c: dict) -> int:
        now = int(self.clock())
        cur = self.conn().execute(
            "INSERT INTO lvb_conditions (day, instrument, upper, lower, prem_min, prem_max, sl_pct, tp_pts, eod,"
            " lots, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?, 'ARMED', ?, ?)",
            (day, c["instrument"], c.get("upper"), c.get("lower"), c["prem_min"], c["prem_max"], c["sl_pct"],
             c["tp_pts"], c["eod"], int(c["lots"]), now, now))
        return int(cur.lastrowid)

    def update_armed(self, cid: int, c: dict) -> bool:
        """Edit an ARMED condition. created_at is RESET: a changed level is a
        new alert, so bars that closed before the edit are never judged
        against it (fresh-cross rule)."""
        now = int(self.clock())
        sets = ", ".join(f"{k}=?" for k in EDITABLE)
        cur = self.conn().execute(
            f"UPDATE lvb_conditions SET {sets}, created_at=?, updated_at=?, note=NULL WHERE id=? AND status='ARMED'",
            tuple(c.get(k) for k in EDITABLE) + (now, now, int(cid)))
        return cur.rowcount == 1

    def set_status(self, cid: int, status: str, note: Optional[str] = None, only_if: str = "ARMED") -> bool:
        cur = self.conn().execute(
            "UPDATE lvb_conditions SET status=?, note=COALESCE(?, note), updated_at=? WHERE id=? AND status=?",
            (status, note, int(self.clock()), int(cid), only_if))
        return cur.rowcount == 1

    def note(self, cid: int, text: str) -> None:
        self.conn().execute("UPDATE lvb_conditions SET note=?, updated_at=? WHERE id=?",
                            (text, int(self.clock()), int(cid)))

    def mark_triggered(self, cid: int, t: dict) -> bool:
        """ARMED → TRIGGERED with the filled trade. Guarded on ARMED so a
        condition can never carry two trades."""
        cur = self.conn().execute(
            "UPDATE lvb_conditions SET status='TRIGGERED', side=?, trigger_bar_ts=?, trigger_close=?, mode=?,"
            " symbol=?, token=?, exchange=?, expiry=?, strike=?, lot_size=?, qty=?, entry_ts=?, entry_px=?,"
            " entry_spot=?, sl_px=?, tp_spot=?, paper_trade_id=?, mark=?, mark_spot=?, note=?, updated_at=?"
            " WHERE id=? AND status='ARMED'",
            (t["side"], t.get("trigger_bar_ts"), t.get("trigger_close"), t["mode"], t["symbol"], t.get("token"),
             t.get("exchange"), str(t.get("expiry") or "") or None, t.get("strike"), t.get("lot_size"), t["qty"],
             t["entry_ts"], t["entry_px"], t.get("entry_spot"), t.get("sl_px"), t.get("tp_spot"),
             t.get("paper_trade_id"), t["entry_px"], t.get("entry_spot"), t.get("note"), int(self.clock()),
             int(cid)))
        return cur.rowcount == 1

    def mark_exit(self, cid: int, exit_px: float, reason: str, exit_spot: Optional[float]) -> None:
        now = int(self.clock())
        self.conn().execute(
            "UPDATE lvb_conditions SET exit_ts=?, exit_px=?, exit_reason=?, exit_spot=?, mark=?, updated_at=?"
            " WHERE id=? AND exit_ts IS NULL", (now, float(exit_px), reason, exit_spot, float(exit_px), now, int(cid)))

    def set_marks(self, cid: int, mark: Optional[float], mark_spot: Optional[float]) -> None:
        self.conn().execute("UPDATE lvb_conditions SET mark=COALESCE(?, mark), mark_spot=COALESCE(?, mark_spot)"
                            " WHERE id=?", (mark, mark_spot, int(cid)))

    def expire_before(self, day: str) -> int:
        """ARMED conditions of earlier days → EXPIRED (valid for their day only)."""
        cur = self.conn().execute(
            "UPDATE lvb_conditions SET status='EXPIRED', note=COALESCE(note, 'day ended'), updated_at=?"
            " WHERE status='ARMED' AND day < ?", (int(self.clock()), day))
        return cur.rowcount
