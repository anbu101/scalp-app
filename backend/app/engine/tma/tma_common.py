# backend/app/engine/tma/tma_common.py
#
# ── TMA LIVE COMMON ── repo + shared helpers (PST pst_common conventions)
# ============================================================================
# DB: the CANONICAL app database via app.db.sqlite.DB_PATH (2026-07-14
# stray-parallel-DB incident — NEVER hand-build the path). Table tma_trades
# is created by migration 021; ensure_schema() here is the fresh-install /
# dev-server top-up (idempotent, same SQL), mirroring PSTRepo doctrine.
#
# Charges: the BACKTEST's charges_model so live/paper P&L is line-for-line
# comparable with backtest runs (leg_net reused from pst_common — one
# formula, one rounding, everywhere).
# ============================================================================

from __future__ import annotations

import sqlite3
import time
from typing import List, Optional

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:  # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

try:
    from app.engine.pst.pst_common import (canonical_db_path, hm_to_min,
                                           ist_day_start, leg_net)
except ImportError:  # standalone tests
    from pst_common import (canonical_db_path, hm_to_min,   # type: ignore
                            ist_day_start, leg_net)

STRATEGY_ID = "TMA_V1"
TABLE = "tma_trades"
LOT_SIZE = 65          # NIFTY — from Settings quantity.lot_size at runtime
IST = 5 * 3600 + 30 * 60

_TMA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tma_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'PAPER',
    direction       TEXT NOT NULL,
    tradingsymbol   TEXT NOT NULL,
    token           INTEGER,
    instrument_type TEXT,
    trend_side      TEXT,
    strike          REAL,
    expiry          TEXT,
    qty             INTEGER NOT NULL,
    entry_ts        INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    sl              REAL,
    tp              REAL,
    entry_order_id  TEXT,
    sell_gtt_id     TEXT,
    exit_ts         INTEGER,
    exit_price      REAL,
    exit_reason     TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    condition       TEXT DEFAULT 'C1',
    ambiguous       INTEGER NOT NULL DEFAULT 0,
    pnl             REAL,
    charges         REAL,
    net_pnl         REAL,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_tma_trades_status ON tma_trades (status, entry_ts);
CREATE INDEX IF NOT EXISTS idx_tma_trades_group  ON tma_trades (group_id);
"""


class TMARepo:
    """Thin sqlite repo for tma_trades. WAL-friendly, short-lived
    connections, never raises to the caller (fail-soft with audit log —
    a DB hiccup must not kill the trading loop). PSTRepo doctrine."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or canonical_db_path()
        self.ensure_schema()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def ensure_schema(self) -> None:
        try:
            with self._conn() as c:
                c.executescript(_TMA_SCHEMA_SQL)
        except Exception as e:
            write_audit_log(f"[TMA][DB] ensure_schema failed: {e}")

    def insert_leg(self, row: dict) -> Optional[int]:
        try:
            row = dict(row)
            row.setdefault("created_at", int(time.time()))
            cols = ", ".join(row.keys())
            ph = ", ".join("?" for _ in row)
            with self._conn() as c:
                cur = c.execute(f"INSERT INTO {TABLE} ({cols}) VALUES ({ph})",
                                list(row.values()))
                return cur.lastrowid
        except Exception as e:
            write_audit_log(f"[TMA][DB] insert_leg failed: {e}")
            return None

    def close_leg(self, leg_db_id: int, *, exit_ts: int, exit_price: float,
                  exit_reason: str, ambiguous: bool, pnl: float,
                  charges: float, net_pnl: float) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    f"""UPDATE {TABLE} SET exit_ts=?, exit_price=?, exit_reason=?,
                        ambiguous=?, pnl=?, charges=?, net_pnl=?, status='CLOSED'
                        WHERE id=?""",
                    (exit_ts, round(exit_price, 2), exit_reason, int(ambiguous),
                     round(pnl, 2), round(charges, 2), round(net_pnl, 2),
                     leg_db_id))
        except Exception as e:
            write_audit_log(f"[TMA][DB] close_leg({leg_db_id}) failed: {e}")

    def update_leg(self, leg_db_id: int, **fields) -> None:
        if not fields:
            return
        try:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._conn() as c:
                c.execute(f"UPDATE {TABLE} SET {sets} WHERE id=?",
                          list(fields.values()) + [leg_db_id])
        except Exception as e:
            write_audit_log(f"[TMA][DB] update_leg({leg_db_id}) failed: {e}")

    def mark_stale(self, leg_db_id: int) -> None:
        """Boot hygiene (INTRADAY mode): an OPEN row from a previous session
        can't be priced honestly — mark STALE, never invent an exit."""
        try:
            with self._conn() as c:
                c.execute(f"""UPDATE {TABLE} SET status='STALE',
                              exit_reason='STALE_RESTART' WHERE id=?""",
                          (leg_db_id,))
        except Exception as e:
            write_audit_log(f"[TMA][DB] mark_stale({leg_db_id}) failed: {e}")

    def open_legs(self) -> List[dict]:
        try:
            with self._conn() as c:
                return [dict(r) for r in c.execute(
                    f"SELECT * FROM {TABLE} WHERE status='OPEN' ORDER BY id")]
        except Exception as e:
            write_audit_log(f"[TMA][DB] open_legs failed: {e}")
            return []
