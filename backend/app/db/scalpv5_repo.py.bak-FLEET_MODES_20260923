# backend/app/db/scalpv5_repo.py
#
# SCALP_V5 — TEST option-BUYING strategy on 3-minute candles.
#
# OWNERSHIP / ISOLATION:
#   This repo is the ONLY module that reads/writes scalpv5_trades.
#   It NEVER touches trades / paper_trades / scalp_v3_trades.
#   No other strategy reads this table. Deleting SCALP_V5 later = DROP
#   scalpv5_trades + delete this file.
#
# ONE LOGICAL TRADE = ONE INSTRUMENT:
#   V5 buys the signalling contract itself (LONG). There is NO hedge / no
#   signal-vs-traded split (unlike V3/V4). The row holds a single symbol with
#   its entry, sl, tp, qty, gtt, and the time-exit bookkeeping.
#
# LIVE vs PAPER: single table, `paper` flag (0=live, 1=paper) — matching the
#   scalp_v3_trades convention.
#
# TWO-PHASE ENTRY (mirrors SCALP_V1 / V3 fill-confirm):
#   1. insert_v5_trade()    — provisional entry (protected limit), OPEN. sl/tp
#                             are ABSOLUTE config points relative to the SIGNAL
#                             close (already computed by the engine), so unlike
#                             V3 they are NOT recomputed from the fill.
#   2. confirm_v5_fill()    — upgrade entry_price to the true fill for accurate
#                             P&L. sl/tp are left AS-IS (config-absolute, not
#                             fill-relative) — this is the deliberate divergence
#                             from V3's confirm_hedge_fill which recomputes SL.
#   3. link_v5_gtt()        — store gtt_id once the protective GTT lands (live).
#
# EXIT (no time-based exit):
#   The position holds until the held-symbol's 3m candle CLOSES BELOW
#   EMA20_HIGH (EMA_EXIT, candle-driven in the tick engine), or SL/TP/MTM/EOD
#   fires. There is no time_exit bookkeeping column — exits are decided live by
#   the engine, not pre-computed at insert.
#
# SCHEMA GUARD: per the migration-runner weakness (partially-failed migrations
#   get marked complete), _ensure_schema() checks sqlite_master and creates the
#   table inline if missing. Inline DDL is kept identical to 018_create_scalpv5.sql.

import time
from typing import Optional

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


# --------------------------------------------------
# SCHEMA GUARD (defensive — see module docstring)
# Inline DDL MUST stay identical to 018_create_scalpv5.sql.
# --------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scalpv5_trades (
    v5_trade_id        TEXT PRIMARY KEY,
    strategy_name      TEXT NOT NULL DEFAULT 'SCALP_V5',
    session_date       TEXT,
    paper              INTEGER NOT NULL DEFAULT 0,
    symbol             TEXT NOT NULL,
    token              INTEGER NOT NULL,
    side               TEXT,
    direction          TEXT NOT NULL DEFAULT 'LONG',
    qty                INTEGER NOT NULL,
    entry_price        REAL,
    sl_price           REAL,
    tp_price           REAL,
    entry_candle_ts    INTEGER,
    order_id           TEXT,
    gtt_id             TEXT,
    state              TEXT NOT NULL DEFAULT 'OPEN',
    exit_price         REAL,
    exit_order_id      TEXT,
    exit_reason        TEXT,
    realized_pnl       REAL,
    entry_time         INTEGER DEFAULT (strftime('%s','now')),
    exit_time          INTEGER,
    created_at         INTEGER DEFAULT (strftime('%s','now')),
    updated_at         INTEGER DEFAULT (strftime('%s','now'))
);
"""

_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_scalpv5_trades_state "
    "ON scalpv5_trades (strategy_name, paper, state);",
    "CREATE INDEX IF NOT EXISTS ix_scalpv5_trades_token "
    "ON scalpv5_trades (token, state);",
]

_schema_checked = False


def _ensure_schema():
    """
    Defensive table guard. The migration runner can mark a partially-failed
    migration complete, so we verify the table exists via sqlite_master and
    create it inline if not. Cheap (cached after first success).
    """
    global _schema_checked
    if _schema_checked:
        return
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='scalpv5_trades'"
        ).fetchone()
        if row is None:
            write_audit_log(
                "[DB][V5][SCHEMA_GUARD] scalpv5_trades MISSING — creating inline "
                "(migration 018 may have been marked complete on partial failure)"
            )
            conn.execute(_CREATE_SQL)
            for idx in _INDEX_SQL:
                conn.execute(idx)
            conn.commit()
        _schema_checked = True
    except Exception as e:
        # Do not cache on failure — retry next call.
        write_audit_log(f"[DB][V5][SCHEMA_GUARD][ERROR] {e}")


def _session_date() -> str:
    return time.strftime("%Y-%m-%d")


# ==================================================
# INSERT (phase 1 — provisional entry)
# ==================================================

def insert_v5_trade(
    *,
    v5_trade_id: str,
    paper: bool,
    symbol: str,
    token: int,
    side: str,
    qty: int,
    entry_price: float,        # provisional (protected limit); upgraded on fill
    sl_price: Optional[float], # config-absolute (entry - sl_points) or None
    tp_price: Optional[float], # config-absolute (entry + tp_points) or None
    entry_candle_ts: int,
    order_id: Optional[str] = None,
):
    _ensure_schema()
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scalpv5_trades (
                v5_trade_id, strategy_name, session_date, paper,
                symbol, token, side, direction, qty,
                entry_price, sl_price, tp_price, entry_candle_ts,
                order_id, state, entry_time, created_at, updated_at
            )
            VALUES (?, 'SCALP_V5', ?, ?, ?, ?, ?, 'LONG', ?,
                    ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (
                v5_trade_id, _session_date(), 1 if paper else 0,
                symbol, token, side, qty,
                entry_price, sl_price, tp_price, entry_candle_ts,
                order_id,
                int(time.time()), int(time.time()), int(time.time()),
            ),
        )
        conn.commit()
        write_audit_log(
            f"[DB][V5] OPEN id={v5_trade_id} paper={int(paper)} "
            f"symbol={symbol} prov_entry={entry_price} sl={sl_price} tp={tp_price} "
            f"qty={qty}"
        )
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V5][FATAL] INSERT FAILED id={v5_trade_id} ERR={e}")
        raise


# ==================================================
# CONFIRM FILL (phase 2 — true entry price ONLY)
# Upgrades entry_price to the real fill. sl/tp are config-absolute and are
# NOT recomputed (deliberate divergence from V3's fill-relative hedge SL).
# ==================================================

def confirm_v5_fill(*, v5_trade_id: str, fill_price: float):
    _ensure_schema()
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE scalpv5_trades
            SET entry_price = ?, updated_at = ?
            WHERE v5_trade_id = ? AND state = 'OPEN'
            """,
            (fill_price, int(time.time()), v5_trade_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            write_audit_log(
                f"[DB][V5][SKIP] FILL_CONFIRM IGNORED id={v5_trade_id} (row not OPEN)"
            )
        else:
            write_audit_log(
                f"[DB][V5] FILL_CONFIRMED id={v5_trade_id} entry={fill_price}"
            )
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V5][ERROR] FILL_CONFIRM FAILED id={v5_trade_id} ERR={e}")
        raise


# ==================================================
# LINK GTT (live only — OCO or SL-only GTT id)
# ==================================================

def link_v5_gtt(*, v5_trade_id: str, gtt_id: str):
    _ensure_schema()
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE scalpv5_trades
            SET gtt_id = ?, updated_at = ?
            WHERE v5_trade_id = ? AND state = 'OPEN'
            """,
            (gtt_id, int(time.time()), v5_trade_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            write_audit_log(
                f"[DB][V5][SKIP] GTT_LINK IGNORED id={v5_trade_id} (row not OPEN)"
            )
        else:
            write_audit_log(f"[DB][V5] GTT LINKED id={v5_trade_id} gtt_id={gtt_id}")
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V5][ERROR] GTT_LINK FAILED id={v5_trade_id} ERR={e}")
        raise


# ==================================================
# CLOSE (atomic; LONG P&L = (exit - entry) * qty)
# Single guarded UPDATE; loud SKIP on 0 rows so a stale/half-open
# row never silently holds the global single-trade gate.
# ==================================================

def close_v5_trade(
    *,
    v5_trade_id: str,
    exit_price: Optional[float],
    exit_order_id: Optional[str],
    exit_reason: str,   # EMA_EXIT|SL|TP|MAX_LOSS|MAX_PROFIT|EOD|MANUAL|BROKER_EXIT|ENTRY_TIMEOUT|STALE_RECONCILE
):
    _ensure_schema()
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT entry_price, qty
            FROM scalpv5_trades
            WHERE v5_trade_id = ? AND state = 'OPEN'
            """,
            (v5_trade_id,),
        ).fetchone()

        if not row:
            diag = conn.execute(
                "SELECT state, exit_price FROM scalpv5_trades WHERE v5_trade_id = ?",
                (v5_trade_id,),
            ).fetchone()
            if diag is None:
                write_audit_log(
                    f"[DB][V5][SKIP] CLOSE IGNORED id={v5_trade_id} (row MISSING)"
                )
            else:
                write_audit_log(
                    f"[DB][V5][SKIP] CLOSE IGNORED id={v5_trade_id} "
                    f"(state={diag['state']} exit_price={diag['exit_price']})"
                )
            return

        entry, qty = row["entry_price"], row["qty"]

        realized = None
        if exit_price is not None and entry is not None and qty:
            realized = (float(exit_price) - float(entry)) * int(qty)

        conn.execute(
            """
            UPDATE scalpv5_trades
            SET exit_time     = ?,
                exit_price    = ?,
                exit_order_id = ?,
                exit_reason   = ?,
                realized_pnl  = ?,
                state         = 'CLOSED',
                updated_at    = ?
            WHERE v5_trade_id = ? AND state = 'OPEN'
            """,
            (
                int(time.time()), exit_price, exit_order_id, exit_reason,
                realized, int(time.time()), v5_trade_id,
            ),
        )
        conn.commit()
        write_audit_log(
            f"[DB][V5] CLOSED id={v5_trade_id} reason={exit_reason} "
            f"exit={exit_price} pnl={realized}"
        )
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V5][ERROR] CLOSE FAILED id={v5_trade_id} ERR={e}")
        raise


# ==================================================
# READ: the single OPEN trade (global single-trade gate SoT)
# ==================================================

def get_open_v5_trade(*, paper: Optional[bool] = None):
    """
    Returns the one OPEN V5 trade as a dict, or None.
    paper=None → any mode; paper=True/False → filter to that mode.
    The global single-trade gate calls this; there should be at most one OPEN.
    """
    _ensure_schema()
    conn = get_conn()
    try:
        if paper is None:
            cur = conn.execute(
                "SELECT * FROM scalpv5_trades WHERE state = 'OPEN' LIMIT 1"
            )
        else:
            cur = conn.execute(
                "SELECT * FROM scalpv5_trades WHERE state = 'OPEN' AND paper = ? LIMIT 1",
                (1 if paper else 0,),
            )
        r = cur.fetchone()
        if not r:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, r))
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] GET_OPEN FAILED ERR={e}")
        return None


def get_v5_trade_by_id(v5_trade_id: str):
    _ensure_schema()
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM scalpv5_trades WHERE v5_trade_id = ?",
            (v5_trade_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, r))
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] GET_BY_ID FAILED id={v5_trade_id} ERR={e}")
        return None


def get_all_open_v5_trades(*, paper: Optional[bool] = None):
    """EOD square-off / reconcile: all OPEN rows (should be ≤1 with global gate)."""
    _ensure_schema()
    conn = get_conn()
    try:
        if paper is None:
            cur = conn.execute("SELECT * FROM scalpv5_trades WHERE state = 'OPEN'")
        else:
            cur = conn.execute(
                "SELECT * FROM scalpv5_trades WHERE state = 'OPEN' AND paper = ?",
                (1 if paper else 0,),
            )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] GET_ALL_OPEN FAILED ERR={e}")
        return []


# ==================================================
# READ: TODAY's realised P&L (for the self-contained V5 MTM guard)
# Mode-aware via the `paper` flag. Today = entry_time localtime == today,
# matching the existing today-reader convention. realized_pnl is GROSS
# ((exit - entry) * qty); V5 records no charges, so MTM is gross.
# ==================================================

def get_total_pnl_v5_today(*, paper: bool) -> float:
    _ensure_schema()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT realized_pnl
            FROM scalpv5_trades
            WHERE state = 'CLOSED'
              AND paper = ?
              AND realized_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """,
            (1 if paper else 0,),
        ).fetchall()
        return float(sum(r[0] for r in rows))
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] PNL_TODAY FAILED ERR={e}")
        return 0.0


def get_total_pnl_v5(*, paper: Optional[bool] = None) -> float:
    """Realized P&L across ALL CLOSED V5 trades (LONG). Not today-scoped."""
    _ensure_schema()
    conn = get_conn()
    try:
        if paper is None:
            rows = conn.execute(
                "SELECT realized_pnl FROM scalpv5_trades "
                "WHERE state = 'CLOSED' AND realized_pnl IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT realized_pnl FROM scalpv5_trades "
                "WHERE state = 'CLOSED' AND realized_pnl IS NOT NULL AND paper = ?",
                (1 if paper else 0,),
            ).fetchall()
        return float(sum(r[0] for r in rows))
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] PNL_FETCH FAILED ERR={e}")
        return 0.0


# ==================================================
# READ: today's CLOSED trades (for the EOD summary), mode-aware
# Excludes NULL realized_pnl (dead/cancel/stale) so they don't pollute stats.
# ==================================================

def get_closed_v5_trades_today(*, paper: bool) -> list:
    _ensure_schema()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT realized_pnl, exit_reason, side, symbol
            FROM scalpv5_trades
            WHERE state = 'CLOSED'
              AND paper = ?
              AND realized_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """,
            (1 if paper else 0,),
        ).fetchall()
        cols = ["realized_pnl", "exit_reason", "side", "symbol"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] CLOSED_TODAY FAILED ERR={e}")
        return []

# ==================================================
# READ: today's CLOSED trades WITH PRICES (for the EOD net-P&L card)
# Mirrors scalp_v3_repo's *_with_prices reader. Returns the raw
# entry/exit/qty so the card data source can compute NET (charges applied),
# keeping V5 consistent with V3/V4 on the net-P&L card. Mode-aware.
# V5 is single-instrument LONG, so entry_price/exit_price/qty are the position
# itself (no hedge_* columns).
# ==================================================

def get_closed_v5_trades_today_with_prices(*, paper: bool) -> list:
    _ensure_schema()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT entry_price, exit_price, qty, side, symbol,
                   exit_reason, state
            FROM scalpv5_trades
            WHERE state = 'CLOSED'
              AND paper = ?
              AND exit_price IS NOT NULL
              AND entry_price IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """,
            (1 if paper else 0,),
        ).fetchall()
        cols = ["entry_price", "exit_price", "qty", "side", "symbol",
                "exit_reason", "state"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        write_audit_log(f"[DB][V5][ERROR] CLOSED_TODAY_WITH_PRICES FAILED ERR={e}")
        return []
    
# ==================================================
# STARTUP RECONCILE — clear stale OPEN trades
# Mirrors scalp_v3_repo.reconcile_stale_open_v3_trades: dry_run=True logs only;
# dry_run=False force-closes at entry_price (P&L 0) tagged STALE_RECONCILE so
# the global gate is never held overnight by a row from a prior session.
# ==================================================

def reconcile_stale_open_v5_trades(*, dry_run: bool = True) -> int:
    _ensure_schema()
    conn = get_conn()
    rows = conn.execute(
        "SELECT v5_trade_id, symbol, entry_price, state, entry_time "
        "FROM scalpv5_trades WHERE state = 'OPEN' ORDER BY entry_time DESC"
    ).fetchall()

    if not rows:
        write_audit_log("[RECONCILE][V5][STALE] no stale OPEN trades at startup")
        return 0

    for r in rows:
        write_audit_log(
            f"[RECONCILE][V5][STALE] STALE_OPEN id={r['v5_trade_id']} "
            f"symbol={r['symbol']} state={r['state']} "
            f"entry_time={r['entry_time']} dry_run={dry_run}"
        )

    if dry_run:
        write_audit_log(
            f"[RECONCILE][V5][STALE] dry_run=True, {len(rows)} row(s) "
            f"LEFT UNTOUCHED (diagnostic only)"
        )
        return len(rows)

    closed = 0
    for r in rows:
        try:
            close_v5_trade(
                v5_trade_id=r["v5_trade_id"],
                exit_price=float(r["entry_price"]) if r["entry_price"] else None,
                exit_order_id=None,
                exit_reason="STALE_RECONCILE",
            )
            closed += 1
        except Exception as e:
            write_audit_log(
                f"[RECONCILE][V5][STALE][ERROR] id={r['v5_trade_id']} ERR={e}"
            )

    write_audit_log(
        f"[RECONCILE][V5][STALE] force-closed {closed}/{len(rows)} stale OPEN trade(s)"
    )
    return closed