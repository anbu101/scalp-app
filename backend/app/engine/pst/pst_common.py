# backend/app/engine/pst/pst_common.py
#
# ── PST LIVE COMMON ── (Phase 1 — shared by both paper managers)
#
# Charges come from the BACKTEST's charges_model (not zerodha_charges_calc)
# deliberately: the paper P&L must be comparable line-for-line with backtest
# runs — same formula, same rounding. Risk limits are ENTRY-GATE ONLY in
# Phase 1 (D28): realized post-charge net per IST day / calendar month,
# blocking new entries once a limit is reached. The intrabar clamp arrives
# with Phase 2 (live-money hardening).

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from typing import Optional, Tuple

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)

LOT_SIZE = 65
IST = 5 * 3600 + 30 * 60


def canonical_db_path() -> str:
    """THE app database — ~/.scalp-app/data/app.db (DB_PATH), the same file
    get_conn() opens. 2026-07-14 incident: PST modules hardcoded
    APP_HOME/'app.db' (no data/ segment), creating a stray parallel DB —
    PST-internal reads worked, every get_conn() integration (paper page,
    summary card, history) silently saw no tables. NEVER construct this
    path by hand again; import it from here."""
    try:
        from app.db.sqlite import DB_PATH
        return str(DB_PATH)
    except Exception:
        import os
        return os.path.expanduser("~/.scalp-app/data/app.db")


def hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def ist_day_start(epoch: int) -> int:
    """Day-start epoch (backtest _day_start_epoch convention) of the IST
    calendar day containing `epoch`."""
    ist = epoch + IST
    return (ist - (ist % 86400)) - IST


def ist_month_key(epoch: int) -> str:
    return datetime.utcfromtimestamp(epoch + IST).strftime("%Y-%m")


# ── charges: the backtest's own formulas, for line-for-line parity ──
def leg_net(direction: str, entry: float, exit_px: float, lots: int
            ) -> Tuple[float, float, float]:
    """(gross, charges, net) — identical to the backtest runners' _leg_net."""
    qty = int(lots) * LOT_SIZE
    if direction == "SELL":
        gross = (float(entry) - float(exit_px)) * qty
        fn_name = "charges_for_short_trade"
    else:
        gross = (float(exit_px) - float(entry)) * qty
        fn_name = "charges_for_long_trade"
    charges = 0.0
    try:
        from app.backtest.charges import charges_model
        fn = getattr(charges_model, fn_name, None)
        if fn is not None:
            cr = fn(entry_price=float(entry), exit_price=float(exit_px), qty=qty)
            charges = float(getattr(cr, "total_charges", 0.0))
            gross = float(getattr(cr, "gross_pnl", gross))
    except Exception:
        charges = 0.0
    return gross, charges, gross - charges


# ── Phase-1 risk: entry-gate on realized net (V3 semantics, no clamp) ──
class RiskGate:
    def __init__(self, *, dml: float = 0, dmp: float = 0,
                 mml: float = 0, mmp: float = 0):
        self.dml, self.dmp = max(0.0, float(dml or 0)), max(0.0, float(dmp or 0))
        self.mml, self.mmp = max(0.0, float(mml or 0)), max(0.0, float(mmp or 0))
        self.enabled = any(v > 0 for v in (self.dml, self.dmp, self.mml, self.mmp))
        self._day_key: Optional[int] = None
        self._month_key: Optional[str] = None
        self.day_realized = 0.0
        self.month_realized = 0.0
        self.day_blocked = False
        self.month_blocked = False

    def roll(self, epoch: int) -> None:
        dk = ist_day_start(epoch)
        if dk != self._day_key:
            self._day_key = dk
            self.day_realized = 0.0
            self.day_blocked = False
        mk = ist_month_key(epoch)
        if mk != self._month_key:
            self._month_key = mk
            self.month_realized = 0.0
            self.month_blocked = False

    def on_close(self, net: float, epoch: int) -> None:
        self.roll(epoch)
        self.day_realized += net
        self.month_realized += net
        if not self.enabled:
            return
        if (self.dml and self.day_realized <= -self.dml) or \
           (self.dmp and self.day_realized >= self.dmp):
            if not self.day_blocked:
                write_audit_log(f"[PST][RISK] daily limit reached "
                                f"(realized {self.day_realized:.0f}) — entries blocked today")
            self.day_blocked = True
        if (self.mml and self.month_realized <= -self.mml) or \
           (self.mmp and self.month_realized >= self.mmp):
            if not self.month_blocked:
                write_audit_log(f"[PST][RISK] monthly limit reached "
                                f"(realized {self.month_realized:.0f}) — entries blocked this month")
            self.month_blocked = True

    def blocked(self, epoch: int) -> bool:
        self.roll(epoch)
        return self.enabled and (self.day_blocked or self.month_blocked)


# ── persistence: own tables, scalp_v3_trades conventions ────────────
PST_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS pst_sell_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL DEFAULT 'PAPER',
    leg_id TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL,
    instrument_type TEXT,
    strike REAL, expiry TEXT,
    direction TEXT NOT NULL DEFAULT 'SELL',
    qty INTEGER NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    sl REAL, tp REAL,
    spot_entry REAL, spot_sl REAL,
    exit_ts INTEGER, exit_price REAL, exit_reason TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    condition TEXT,
    ambiguous INTEGER NOT NULL DEFAULT 0,
    pnl REAL, charges REAL, net_pnl REAL,
    tp_order_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pst_sell_status ON pst_sell_trades(status, entry_ts);

CREATE TABLE IF NOT EXISTS pst_hedge_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL DEFAULT 'PAPER',
    leg_id TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL,
    instrument_type TEXT,
    strike REAL, expiry TEXT,
    direction TEXT NOT NULL DEFAULT 'BUY',
    qty INTEGER NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    sl REAL, tp REAL,
    sig_symbol TEXT, sig_entry REAL,
    spot_entry REAL, spot_sl REAL,
    exit_ts INTEGER, exit_price REAL, exit_reason TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    condition TEXT,
    ambiguous INTEGER NOT NULL DEFAULT 0,
    pnl REAL, charges REAL, net_pnl REAL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pst_hedge_status ON pst_hedge_trades(status, entry_ts);
"""


class PSTRepo:
    """Thin sqlite repo for both PST tables. WAL-friendly, short-lived
    connections, never raises to the caller (fail-soft with audit log —
    a DB hiccup must not kill the trading loop)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.ensure_schema()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def ensure_schema(self) -> None:
        try:
            with self._conn() as c:
                c.executescript(PST_MIGRATION_SQL)
        except Exception as e:
            write_audit_log(f"[PST][DB] ensure_schema failed: {e}")
        # column added after first release — idempotent top-up for old DBs
        self._ensure_column("pst_sell_trades", "tp_order_id", "TEXT")

    def _ensure_column(self, table: str, col: str, decl: str) -> None:
        """Idempotent ALTER for installs whose table predates a column."""
        try:
            with self._conn() as c:
                cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
                if col not in cols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception as e:
            write_audit_log(f"[PST][DB] ensure_column({table}.{col}) failed: {e}")

    def set_tp_order_id(self, table: str, leg_db_id: int, oid: str) -> None:
        try:
            with self._conn() as c:
                c.execute(f"UPDATE {table} SET tp_order_id=? WHERE id=?",
                          (oid, leg_db_id))
        except Exception as e:
            write_audit_log(f"[PST][DB] set_tp_order_id failed: {e}")

    def insert_leg(self, table: str, row: dict) -> Optional[int]:
        try:
            row = dict(row)
            row.setdefault("created_at", int(time.time()))
            cols = ", ".join(row.keys())
            ph = ", ".join("?" for _ in row)
            with self._conn() as c:
                cur = c.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})",
                                list(row.values()))
                return cur.lastrowid
        except Exception as e:
            write_audit_log(f"[PST][DB] insert_leg({table}) failed: {e}")
            return None

    def close_leg(self, table: str, leg_db_id: int, *, exit_ts: int,
                  exit_price: float, exit_reason: str, ambiguous: bool,
                  pnl: float, charges: float, net_pnl: float) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    f"""UPDATE {table} SET exit_ts=?, exit_price=?, exit_reason=?,
                        ambiguous=?, pnl=?, charges=?, net_pnl=?, status='CLOSED'
                        WHERE id=?""",
                    (exit_ts, round(exit_price, 2), exit_reason, int(ambiguous),
                     round(pnl, 2), round(charges, 2), round(net_pnl, 2), leg_db_id))
        except Exception as e:
            write_audit_log(f"[PST][DB] close_leg({table},{leg_db_id}) failed: {e}")

    def mark_stale(self, table: str, leg_db_id: int) -> None:
        """Boot hygiene: an OPEN row from a PREVIOUS session can't be priced
        honestly — mark STALE (no P&L) and alert upstream. Never invents
        an exit price."""
        try:
            with self._conn() as c:
                c.execute(f"""UPDATE {table} SET status='STALE',
                              exit_reason='STALE_RESTART' WHERE id=?""",
                          (leg_db_id,))
        except Exception as e:
            write_audit_log(f"[PST][DB] mark_stale({table},{leg_db_id}) failed: {e}")

    def open_legs(self, table: str):
        try:
            with self._conn() as c:
                return [dict(r) for r in
                        c.execute(f"SELECT * FROM {table} WHERE status='OPEN' ORDER BY id")]
        except Exception as e:
            write_audit_log(f"[PST][DB] open_legs({table}) failed: {e}")
            return []