# backend/app/db/scalp_v4_repo.py
#
# SCALP_V4 — TEST option-BUYING hedge strategy (derived from SCALP_V1).
#
# OWNERSHIP / ISOLATION:
#   This repo is the ONLY module that reads/writes scalp_v4_trades.
#   It NEVER touches trades / paper_trades. No other strategy reads this
#   table. Deleting SCALP_V4 later = DROP scalp_v4_trades + delete this file.
#
# ONE LOGICAL TRADE = TWO INSTRUMENTS:
#   signal_*  — the contract that fired the signal (e.g. 24500CE). TRACKED for
#               its own SL/TP; NEVER traded. Drives WHEN to exit.
#   hedge_*   — the contract actually BOUGHT (e.g. 24450PE). LONG. Protected by
#               an SL-only GTT at (hedge_fill - MAX_SL). This is the position.
#
# LIVE vs PAPER: single table, `paper` flag (0=live, 1=paper) — matching the
#   scalp_v2_groups convention.
#
# TWO-PHASE ENTRY (mirrors SCALP_V1 on_sell_signal → fill-confirm → GTT):
#   1. insert_v4_trade()     — provisional hedge_entry (protected limit), OPEN.
#   2. confirm_hedge_fill()  — upgrade to true fill; RECOMPUTE hedge_sl = fill - max_sl
#                              (SL is fill-relative, so cannot be final pre-fill).
#   3. link_hedge_gtt()      — store hedge_gtt_id once the SL-only GTT lands.
#   (PAPER skips 2/3's broker bits but still records the fill-equivalent entry.)
#
# SCHEMA GUARD: per the migration-runner weakness (partially-failed migrations
#   get marked complete), _ensure_schema() checks sqlite_master and creates the
#   table inline if missing. Inline DDL is kept identical to 017_create_scalp_v4.sql.

import time
from typing import Optional

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


# --------------------------------------------------
# SCHEMA GUARD (defensive — see module docstring)
# Inline DDL MUST stay identical to 017_create_scalp_v4.sql.
# --------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scalp_v4_trades (
    v4_trade_id        TEXT PRIMARY KEY,
    strategy_name      TEXT NOT NULL DEFAULT 'SCALP_V4',
    session_date       TEXT,
    paper              INTEGER NOT NULL DEFAULT 0,
    signal_symbol      TEXT NOT NULL,
    signal_token       INTEGER NOT NULL,
    signal_side        TEXT,
    signal_entry_price REAL,
    signal_sl          REAL,
    signal_tp          REAL,
    signal_candle_ts   INTEGER,
    hedge_symbol       TEXT NOT NULL,
    hedge_token        INTEGER NOT NULL,
    hedge_side         TEXT,
    hedge_direction    TEXT NOT NULL DEFAULT 'LONG',
    hedge_qty          INTEGER NOT NULL,
    hedge_entry_price  REAL,
    hedge_sl           REAL,
    hedge_order_id     TEXT,
    hedge_gtt_id       TEXT,
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
    "CREATE INDEX IF NOT EXISTS ix_scalp_v4_trades_state "
    "ON scalp_v4_trades (strategy_name, paper, state);",
    "CREATE INDEX IF NOT EXISTS ix_scalp_v4_trades_signal_token "
    "ON scalp_v4_trades (signal_token, state);",
    "CREATE INDEX IF NOT EXISTS ix_scalp_v4_trades_hedge_token "
    "ON scalp_v4_trades (hedge_token, state);",
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
            "WHERE type='table' AND name='scalp_v4_trades'"
        ).fetchone()
        if row is None:
            write_audit_log(
                "[DB][V4][SCHEMA_GUARD] scalp_v4_trades MISSING — creating inline "
                "(migration 017 may have been marked complete on partial failure)"
            )
            conn.execute(_CREATE_SQL)
            for idx in _INDEX_SQL:
                conn.execute(idx)
            conn.commit()
        _schema_checked = True
    except Exception as e:
        # Do not cache on failure — retry next call.
        write_audit_log(f"[DB][V4][SCHEMA_GUARD][ERROR] {e}")


def _session_date() -> str:
    return time.strftime("%Y-%m-%d")


# ==================================================
# INSERT (phase 1 — provisional hedge entry)
# ==================================================

def insert_v4_trade(
    *,
    v4_trade_id: str,
    paper: bool,
    signal_symbol: str,
    signal_token: int,
    signal_side: str,
    signal_entry_price: float,
    signal_sl: float,
    signal_tp: float,
    signal_candle_ts: int,
    hedge_symbol: str,
    hedge_token: int,
    hedge_side: str,
    hedge_qty: int,
    hedge_entry_price: float,   # provisional (protected limit); upgraded on fill
    hedge_sl: float,            # provisional (limit - max_sl); recomputed on fill
    hedge_order_id: Optional[str] = None,
):
    _ensure_schema()
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scalp_v4_trades (
                v4_trade_id, strategy_name, session_date, paper,
                signal_symbol, signal_token, signal_side,
                signal_entry_price, signal_sl, signal_tp, signal_candle_ts,
                hedge_symbol, hedge_token, hedge_side, hedge_direction,
                hedge_qty, hedge_entry_price, hedge_sl, hedge_order_id,
                state, entry_time, created_at, updated_at
            )
            VALUES (?, 'SCALP_V4', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'LONG',
                    ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (
                v4_trade_id, _session_date(), 1 if paper else 0,
                signal_symbol, signal_token, signal_side,
                signal_entry_price, signal_sl, signal_tp, signal_candle_ts,
                hedge_symbol, hedge_token, hedge_side,
                hedge_qty, hedge_entry_price, hedge_sl, hedge_order_id,
                int(time.time()), int(time.time()), int(time.time()),
            ),
        )
        conn.commit()
        write_audit_log(
            f"[DB][V4] OPEN id={v4_trade_id} paper={int(paper)} "
            f"signal={signal_symbol} hedge={hedge_symbol} "
            f"prov_entry={hedge_entry_price} prov_sl={hedge_sl} qty={hedge_qty}"
        )
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V4][FATAL] INSERT FAILED id={v4_trade_id} ERR={e}")
        raise


# ==================================================
# CONFIRM HEDGE FILL (phase 2 — true entry + fill-relative SL)
# Upgrades hedge_entry_price to the real fill AND recomputes hedge_sl.
# hedge_sl is fill-relative (fill - max_sl) per spec, so it can only be
# final once the fill is known.
# ==================================================

def confirm_hedge_fill(
    *,
    v4_trade_id: str,
    fill_price: float,
    max_sl_points: float,
):
    _ensure_schema()
    conn = get_conn()
    try:
        new_sl = round(fill_price - max_sl_points, 2)
        cur = conn.execute(
            """
            UPDATE scalp_v4_trades
            SET hedge_entry_price = ?,
                hedge_sl          = ?,
                updated_at        = ?
            WHERE v4_trade_id = ? AND state = 'OPEN'
            """,
            (fill_price, new_sl, int(time.time()), v4_trade_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            write_audit_log(
                f"[DB][V4][SKIP] FILL_CONFIRM IGNORED id={v4_trade_id} "
                f"(row not OPEN)"
            )
        else:
            write_audit_log(
                f"[DB][V4] FILL_CONFIRMED id={v4_trade_id} "
                f"entry={fill_price} sl={new_sl} (=fill-{max_sl_points})"
            )
        return new_sl
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V4][ERROR] FILL_CONFIRM FAILED id={v4_trade_id} ERR={e}")
        raise


# ==================================================
# LINK HEDGE GTT (live only — SL-only GTT id)
# ==================================================

def link_hedge_gtt(*, v4_trade_id: str, gtt_id: str):
    _ensure_schema()
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE scalp_v4_trades
            SET hedge_gtt_id = ?, updated_at = ?
            WHERE v4_trade_id = ? AND state = 'OPEN'
            """,
            (gtt_id, int(time.time()), v4_trade_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            write_audit_log(
                f"[DB][V4][SKIP] GTT_LINK IGNORED id={v4_trade_id} (row not OPEN)"
            )
        else:
            write_audit_log(f"[DB][V4] GTT LINKED id={v4_trade_id} gtt_id={gtt_id}")
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V4][ERROR] GTT_LINK FAILED id={v4_trade_id} ERR={e}")
        raise


# ==================================================
# CLOSE (atomic; LONG P&L = (exit - hedge_entry) * qty)
# Single guarded UPDATE; loud SKIP on 0 rows so a stale/half-open
# row never silently holds the global single-trade gate.
# ==================================================

def close_v4_trade(
    *,
    v4_trade_id: str,
    exit_price: Optional[float],
    exit_order_id: Optional[str],
    exit_reason: str,   # SIG_SL|SIG_TP|HEDGE_SL|EOD|MANUAL|BROKER_EXIT
):
    _ensure_schema()
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT hedge_entry_price, hedge_qty
            FROM scalp_v4_trades
            WHERE v4_trade_id = ? AND state = 'OPEN'
            """,
            (v4_trade_id,),
        ).fetchone()

        if not row:
            diag = conn.execute(
                "SELECT state, exit_price FROM scalp_v4_trades WHERE v4_trade_id = ?",
                (v4_trade_id,),
            ).fetchone()
            if diag is None:
                write_audit_log(
                    f"[DB][V4][SKIP] CLOSE IGNORED id={v4_trade_id} "
                    f"(row MISSING — not found)"
                )
            else:
                write_audit_log(
                    f"[DB][V4][SKIP] CLOSE IGNORED id={v4_trade_id} "
                    f"(state={diag['state']} exit_price={diag['exit_price']})"
                )
            return

        hedge_entry, hedge_qty = row["hedge_entry_price"], row["hedge_qty"]

        realized = None
        if exit_price is not None and hedge_entry is not None and hedge_qty:
            realized = (float(exit_price) - float(hedge_entry)) * int(hedge_qty)

        conn.execute(
            """
            UPDATE scalp_v4_trades
            SET exit_time     = ?,
                exit_price    = ?,
                exit_order_id = ?,
                exit_reason   = ?,
                realized_pnl  = ?,
                state         = 'CLOSED',
                updated_at    = ?
            WHERE v4_trade_id = ? AND state = 'OPEN'
            """,
            (
                int(time.time()), exit_price, exit_order_id, exit_reason,
                realized, int(time.time()), v4_trade_id,
            ),
        )
        conn.commit()
        write_audit_log(
            f"[DB][V4] CLOSED id={v4_trade_id} reason={exit_reason} "
            f"exit={exit_price} pnl={realized}"
        )
    except Exception as e:
        conn.rollback()
        write_audit_log(f"[DB][V4][ERROR] CLOSE FAILED id={v4_trade_id} ERR={e}")
        raise


# ==================================================
# READ: the single OPEN trade (global single-trade gate SoT)
# ==================================================

def get_open_v4_trade(*, paper: Optional[bool] = None):
    """
    Returns the one OPEN V4 trade as a dict, or None.
    paper=None → any mode; paper=True/False → filter to that mode.
    The global single-trade gate calls this; there should be at most one OPEN.
    """
    _ensure_schema()
    conn = get_conn()
    try:
        if paper is None:
            cur = conn.execute(
                "SELECT * FROM scalp_v4_trades WHERE state = 'OPEN' LIMIT 1"
            )
        else:
            cur = conn.execute(
                "SELECT * FROM scalp_v4_trades WHERE state = 'OPEN' AND paper = ? LIMIT 1",
                (1 if paper else 0,),
            )
        r = cur.fetchone()
        if not r:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, r))
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] GET_OPEN FAILED ERR={e}")
        return None


def get_open_v4_trade_by_signal_token(signal_token: int):
    """Watcher map: resolve an incoming signal-token tick to its open V4 trade."""
    _ensure_schema()
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM scalp_v4_trades "
            "WHERE signal_token = ? AND state = 'OPEN' LIMIT 1",
            (signal_token,),
        )
        r = cur.fetchone()
        if not r:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, r))
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] GET_BY_SIGNAL_TOKEN FAILED ERR={e}")
        return None


def get_v4_trade_by_id(v4_trade_id: str):
    _ensure_schema()
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM scalp_v4_trades WHERE v4_trade_id = ?",
            (v4_trade_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, r))
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] GET_BY_ID FAILED id={v4_trade_id} ERR={e}")
        return None


def get_all_open_v4_trades(*, paper: Optional[bool] = None):
    """EOD square-off / reconcile: all OPEN rows (should be ≤1 with global gate)."""
    _ensure_schema()
    conn = get_conn()
    try:
        if paper is None:
            cur = conn.execute("SELECT * FROM scalp_v4_trades WHERE state = 'OPEN'")
        else:
            cur = conn.execute(
                "SELECT * FROM scalp_v4_trades WHERE state = 'OPEN' AND paper = ?",
                (1 if paper else 0,),
            )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] GET_ALL_OPEN FAILED ERR={e}")
        return []


def get_total_pnl_v4(*, paper: Optional[bool] = None) -> float:
    """Realized P&L across CLOSED V4 trades (LONG hedge)."""
    _ensure_schema()
    conn = get_conn()
    try:
        if paper is None:
            rows = conn.execute(
                "SELECT realized_pnl FROM scalp_v4_trades "
                "WHERE state = 'CLOSED' AND realized_pnl IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT realized_pnl FROM scalp_v4_trades "
                "WHERE state = 'CLOSED' AND realized_pnl IS NOT NULL AND paper = ?",
                (1 if paper else 0,),
            ).fetchall()
        return float(sum(r[0] for r in rows))
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] PNL_FETCH FAILED ERR={e}")
        return 0.0


# ==================================================
# READ: today's CLOSED PAPER trades (for the EOD paper summary)
#
# Mirrors get_total_pnl_v4's filters (state=CLOSED, realized_pnl IS NOT NULL so
# dead/cancelled/stale rows that never opened a position are excluded) but
# returns ROWS for per-trade stats. "Today" matches the existing paper-summary
# convention: entry_time in localtime == today. realized_pnl is GROSS
# ((exit - entry) * qty) — V4 records no charges, so the summary is gross.
# ==================================================

def get_closed_paper_v4_trades_today() -> list:
    """
    Return today's CLOSED PAPER V4 trades as dicts with the fields the EOD
    paper summary needs: realized_pnl, exit_reason, signal_side, hedge_symbol,
    hedge_side. Excludes rows with NULL realized_pnl (dead entries / cancels /
    stale-reconcile) so they don't pollute win/loss/best/worst.
    """
    _ensure_schema()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT realized_pnl, exit_reason, signal_side, hedge_symbol, hedge_side
            FROM scalp_v4_trades
            WHERE state = 'CLOSED'
              AND paper = 1
              AND realized_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """
        ).fetchall()
        cols = ["realized_pnl", "exit_reason", "signal_side", "hedge_symbol", "hedge_side"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] CLOSED_PAPER_TODAY FAILED ERR={e}")
        return []

def get_closed_live_v4_trades_today() -> list:
    """
    Live mirror of get_closed_paper_v4_trades_today (paper = 0). Returns today's
    CLOSED LIVE V4 trades with the fields the daily-summary live section needs.
    realized_pnl is GROSS ((exit - hedge_entry) * qty); V4 models no charges.
    Excludes NULL realized_pnl rows (dead/cancel/stale) so they don't count.
    """
    _ensure_schema()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT realized_pnl, exit_reason, signal_side, hedge_symbol, hedge_side
            FROM scalp_v4_trades
            WHERE state = 'CLOSED'
              AND paper = 0
              AND realized_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """
        ).fetchall()
        cols = ["realized_pnl", "exit_reason", "signal_side", "hedge_symbol", "hedge_side"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        write_audit_log(f"[DB][V4][ERROR] CLOSED_LIVE_TODAY FAILED ERR={e}")
        return []

# ==================================================
# STARTUP RECONCILE — clear stale OPEN trades
# Mirrors paper_trades_repo.reconcile_stale_open_trades:
# dry_run=True logs only; dry_run=False force-closes at hedge_entry
# (P&L 0) tagged STALE_RECONCILE so the global gate is never held
# overnight by a row from a prior session.
# ==================================================

def reconcile_stale_open_v4_trades(*, dry_run: bool = True) -> int:
    _ensure_schema()
    conn = get_conn()
    rows = conn.execute(
        "SELECT v4_trade_id, signal_symbol, hedge_symbol, hedge_entry_price, "
        "state, entry_time FROM scalp_v4_trades WHERE state = 'OPEN' "
        "ORDER BY entry_time DESC"
    ).fetchall()

    if not rows:
        write_audit_log("[RECONCILE][V4][STALE] no stale OPEN trades at startup")
        return 0

    for r in rows:
        write_audit_log(
            f"[RECONCILE][V4][STALE] STALE_OPEN id={r['v4_trade_id']} "
            f"signal={r['signal_symbol']} hedge={r['hedge_symbol']} "
            f"state={r['state']} entry_time={r['entry_time']} dry_run={dry_run}"
        )

    if dry_run:
        write_audit_log(
            f"[RECONCILE][V4][STALE] dry_run=True, {len(rows)} row(s) "
            f"LEFT UNTOUCHED (diagnostic only)"
        )
        return len(rows)

    closed = 0
    for r in rows:
        try:
            close_v4_trade(
                v4_trade_id=r["v4_trade_id"],
                exit_price=float(r["hedge_entry_price"]) if r["hedge_entry_price"] else None,
                exit_order_id=None,
                exit_reason="STALE_RECONCILE",
            )
            closed += 1
        except Exception as e:
            write_audit_log(
                f"[RECONCILE][V4][STALE][ERROR] id={r['v4_trade_id']} ERR={e}"
            )

    write_audit_log(
        f"[RECONCILE][V4][STALE] force-closed {closed}/{len(rows)} stale OPEN trade(s)"
    )
    return closed

# ════════════════════════════════════════════════════════════════════
#  ADDITIVE — append to backend/app/db/scalp_v4_repo.py
#
#  The EOD summary card shows V4 as NET. V4 stores only gross realized_pnl,
#  so the card must recompute charges per row — which needs the price/qty
#  columns the existing get_closed_*_v4_trades_today() readers do NOT expose.
#
#  This is a SEPARATE reader so the existing readers (consumed by the live/
#  paper Telegram summaries) stay byte-for-byte unchanged. Same filters as
#  get_closed_*_v4_trades_today (state=CLOSED, realized_pnl IS NOT NULL,
#  entry_time today) so the row set matches exactly.
# ════════════════════════════════════════════════════════════════════

def get_closed_v4_trades_today_with_prices(*, paper: bool) -> list:
    """
    Today's CLOSED V4 trades (one mode) with the columns needed to compute
    NET P&L via zerodha charges: hedge_entry_price, exit_price, hedge_qty,
    plus exit_reason / hedge_symbol for win/loss + CE/PE if ever needed.

    V4 is always LONG (the hedge is bought), so callers pass direction=LONG
    to calculate_option_charges. realized_pnl is the stored GROSS value and is
    returned too, so the caller can sanity-check gross-vs-recomputed.

    Excludes NULL realized_pnl rows (dead/cancel/stale) — identical to the
    existing today-readers — so they never count toward the card.
    """
    _ensure_schema()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT hedge_entry_price, exit_price, hedge_qty,
                   realized_pnl, exit_reason, hedge_symbol
            FROM scalp_v4_trades
            WHERE state = 'CLOSED'
              AND paper = ?
              AND realized_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """,
            (1 if paper else 0,),
        ).fetchall()
        cols = ["hedge_entry_price", "exit_price", "hedge_qty",
                "realized_pnl", "exit_reason", "hedge_symbol"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        write_audit_log(
            f"[DB][V4][ERROR] CLOSED_TODAY_WITH_PRICES paper={int(paper)} ERR={e}"
        )
        return []