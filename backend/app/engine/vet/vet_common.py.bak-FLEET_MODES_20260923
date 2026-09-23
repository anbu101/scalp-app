# backend/app/engine/vet/vet_common.py
#
# ── VET_V1 TRADE STORE ── private table, TMA_V2 shape
# ============================================================================
# WHY A PRIVATE TABLE (LD4). A VET position can be TWO legs — a short option
# and its protective wing — that open and close together and are one economic
# position. The generic paper_trades row has no way to express that pairing,
# and writing two independent generic rows would double the trade count in
# every downstream view while halving the apparent win rate (a wing is almost
# always a small loser). vet_trades follows TMA_V2: ONE ROW PER LEG, both legs
# sharing a group_id, so live has a real row per real order while reporting
# still sums a position correctly by grouping.
#
# leg_role distinguishes them: MAIN (the leg the signal is about — bought in
# BUY mode, sold in SELL mode) and WING (the protective long, SELL only).
#
# NEVER SYNTHETIC. The backtest may PRICE a wing that had no market print;
# live cannot buy one. There is deliberately no synthetic flag in this schema
# — if a real wing is unavailable the entry does not happen, so no row is
# written. See wing_mode in the VET_V1 config defaults.
#
# STATUS values: OPEN | CLOSED | STALE. STALE marks a row the runtime could
# not reconcile (e.g. a restart found a DB position the broker does not have);
# it is never silently deleted, because a row that vanishes is a row nobody
# investigates.
# ============================================================================

from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, List, Optional

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:                                        # standalone tests
    def write_audit_log(msg: str) -> None:                 # type: ignore
        print(msg)

# ── DB PATH FIX 2026-09-01 ── the app's sqlite is wherever canonical_db_path()
# says (TMA2Repo does the same). A hardcoded path here sent two days of paper
# rows into a stray file the PaperTrades union never read: trades happened,
# nobody could see them. The expanduser fallback is for standalone tests ONLY.
try:
    from app.engine.pst.pst_common import canonical_db_path as _canon
except ImportError:                                        # standalone tests
    def _canon() -> str:                                   # type: ignore
        return os.path.expanduser("~/.scalp-app/scalp.db")
DEFAULT_DB = None

VET_SCHEMA = """
CREATE TABLE IF NOT EXISTS vet_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT    NOT NULL,
    leg_role        TEXT    NOT NULL DEFAULT 'MAIN',   -- MAIN | WING
    mode            TEXT    NOT NULL DEFAULT 'PAPER',  -- PAPER | LIVE
    direction       TEXT    NOT NULL,                  -- LONG | SHORT
    tradingsymbol   TEXT    NOT NULL,
    token           INTEGER,
    instrument_type TEXT,                              -- CE | PE
    strike          REAL,
    expiry          TEXT,
    qty             INTEGER NOT NULL,
    lots            INTEGER,
    lot_size        INTEGER,
    entry_ts        INTEGER NOT NULL,
    entry_price     REAL    NOT NULL,
    entry_order_id  TEXT,
    signal_bar_ts   INTEGER,                           -- the 5m bar that decided it
    condition       INTEGER,                           -- -1 | 0 | +1 at entry
    leg_action      TEXT,                              -- BUY | SELL (config at entry)
    exit_ts         INTEGER,
    exit_price      REAL,
    exit_reason     TEXT,                              -- FLIP|SIGNAL_EXIT|EXPIRY_EXIT|EOD|KILL
    exit_order_id   TEXT,
    status          TEXT    NOT NULL DEFAULT 'OPEN',
    pnl             REAL,
    charges         REAL,
    net_pnl         REAL,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_vet_trades_status ON vet_trades (status, entry_ts);
CREATE INDEX IF NOT EXISTS idx_vet_trades_group  ON vet_trades (group_id);
"""


class VetRepo:
    """Thin, synchronous store. Every method is safe to call twice."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _canon()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def ensure_schema(self) -> None:
        with self._conn() as c:
            c.executescript(VET_SCHEMA)

    # ── writes ──────────────────────────────────────────────────────────
    def insert_leg(self, row: Dict) -> Optional[int]:
        cols = ("group_id", "leg_role", "mode", "direction", "tradingsymbol",
                "token", "instrument_type", "strike", "expiry", "qty", "lots",
                "lot_size", "entry_ts", "entry_price", "entry_order_id",
                "signal_bar_ts", "condition", "leg_action")
        vals = [row.get(k) for k in cols]
        try:
            with self._conn() as c:
                cur = c.execute(
                    f"INSERT INTO vet_trades ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})", vals)
                return int(cur.lastrowid)
        except Exception as e:
            write_audit_log(f"[VET][DB] insert_leg FAILED {row.get('tradingsymbol')}: {e}")
            return None

    def close_leg(self, leg_id: int, *, exit_ts: int, exit_price: float,
                  exit_reason: str, pnl: Optional[float] = None,
                  charges: Optional[float] = None,
                  net_pnl: Optional[float] = None,
                  exit_order_id: Optional[str] = None) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    "UPDATE vet_trades SET exit_ts=?, exit_price=?, "
                    "exit_reason=?, pnl=?, charges=?, net_pnl=?, "
                    "exit_order_id=?, status='CLOSED' "
                    "WHERE id=? AND status='OPEN'",
                    (int(exit_ts), float(exit_price), str(exit_reason),
                     pnl, charges, net_pnl, exit_order_id, int(leg_id)))
        except Exception as e:
            write_audit_log(f"[VET][DB] close_leg FAILED id={leg_id}: {e}")

    def mark_stale(self, leg_id: int, note: str = "") -> None:
        """Reconciliation could not match this row to reality. Kept, not
        deleted — a vanished row is a row nobody investigates."""
        try:
            with self._conn() as c:
                c.execute("UPDATE vet_trades SET status='STALE', "
                          "exit_reason=COALESCE(exit_reason, ?) WHERE id=?",
                          (f"STALE:{note}"[:60], int(leg_id)))
            write_audit_log(f"[VET][DB] leg {leg_id} marked STALE {note}")
        except Exception as e:
            write_audit_log(f"[VET][DB] mark_stale FAILED id={leg_id}: {e}")

    # ── reads ───────────────────────────────────────────────────────────
    def open_legs(self, mode: Optional[str] = None) -> List[Dict]:
        q = "SELECT * FROM vet_trades WHERE status='OPEN'"
        args: List = []
        if mode:
            q += " AND mode=?"
            args.append(mode)
        q += " ORDER BY entry_ts"
        try:
            with self._conn() as c:
                return [dict(r) for r in c.execute(q, args).fetchall()]
        except Exception as e:
            write_audit_log(f"[VET][DB] open_legs FAILED: {e}")
            return []

    def open_group(self, mode: Optional[str] = None) -> Optional[Dict]:
        """The single open position as {group_id, main, wing} — VET holds ONE
        position at a time, so more than one open group is a bug worth
        shouting about rather than quietly picking the newest."""
        legs = self.open_legs(mode)
        if not legs:
            return None
        groups = {}
        for leg in legs:
            groups.setdefault(leg["group_id"], {})[leg["leg_role"]] = leg
        if len(groups) > 1:
            write_audit_log(f"[VET][DB] WARNING {len(groups)} open groups — "
                            f"expected 1: {list(groups)}")
        gid = legs[-1]["group_id"]
        g = groups[gid]
        return {"group_id": gid, "main": g.get("MAIN"), "wing": g.get("WING")}

    def trades_on(self, day_start_ts: int, day_end_ts: int,
                  mode: Optional[str] = None) -> List[Dict]:
        q = ("SELECT * FROM vet_trades WHERE entry_ts>=? AND entry_ts<? ")
        args: List = [int(day_start_ts), int(day_end_ts)]
        if mode:
            q += "AND mode=? "
            args.append(mode)
        q += "ORDER BY entry_ts"
        try:
            with self._conn() as c:
                return [dict(r) for r in c.execute(q, args).fetchall()]
        except Exception as e:
            write_audit_log(f"[VET][DB] trades_on FAILED: {e}")
            return []

    def day_entry_count(self, day_start_ts: int, day_end_ts: int,
                        mode: Optional[str] = None) -> int:
        """MAIN legs only — the wing is not an independent trade."""
        return len([r for r in self.trades_on(day_start_ts, day_end_ts, mode)
                    if r.get("leg_role") == "MAIN"])


def now_ts() -> int:
    return int(time.time())