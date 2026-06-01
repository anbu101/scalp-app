# backend/app/risk/strategy_max_loss_guard.py
#
# Per-strategy DAILY risk limits — Max Loss and Max Profit.
# ============================================================================
# User-configurable per strategy via Settings (config keys max_loss /
# max_profit, in rupees). Block-only enforcement: when a limit is hit, NO new
# entries are taken for that strategy for the rest of the day. Open positions
# are NOT force-closed (they run to their own GTT/SL/EOD).
#
# CORRECTNESS (why this replaces the old guard):
#   - DAILY scope: only today's CLOSED trades count (old version summed ALL
#     history → strategy would be permanently locked after a bad stretch).
#   - MODE-aware: PAPER strategies measure paper_trades.net_pnl; LIVE
#     strategies measure realised P&L from the trades table. (Old version read
#     only the live `trades` table → never tripped for PAPER strategies.)
#   - Direction-aware for live SHORT trades; paper net_pnl is already signed.
#
# These P&L queries mirror the ones used by the Telegram daily summary, so the
# numbers agree with what the dashboard / Telegram already report.
#
# Fail-safe: if P&L cannot be determined, block (fail closed).
# ============================================================================

from datetime import datetime, date
from typing import Optional

from app.db.sqlite import get_conn
from app.config.strategy_loader import load_strategy_config
from app.event_bus.audit_logger import write_audit_log


# Result reasons
REASON_OK         = None
REASON_MAX_LOSS   = "MAX_LOSS_HIT"
REASON_MAX_PROFIT = "MAX_PROFIT_HIT"
REASON_ERROR      = "RISK_CHECK_ERROR"


def _today_midnight_ts() -> int:
    today = date.today()
    return int(datetime(today.year, today.month, today.day, 0, 0, 0).timestamp())


# ---------------------------------------------------------------------------
# Today's realised P&L, mode-aware
# ---------------------------------------------------------------------------

def _today_live_pnl(strategy_id: str) -> Optional[float]:
    """Realised P&L from today's CLOSED live trades (direction-aware)."""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT entry_price, exit_price, qty,
                   COALESCE(trade_direction, 'LONG') AS dir
            FROM trades
            WHERE strategy_id = ?
              AND state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (strategy_id, _today_midnight_ts()),
        ).fetchall()
        total = 0.0
        for entry_price, exit_price, qty, direction in rows:
            if entry_price is None or exit_price is None or qty is None:
                continue
            if direction == "SHORT":
                total += (entry_price - exit_price) * qty
            else:
                total += (exit_price - entry_price) * qty
        return float(total)
    except Exception as e:
        write_audit_log(f"[RISK][ERROR] live pnl fetch failed STRATEGY={strategy_id} ERR={e}")
        return None


def _today_paper_pnl(strategy_id: str) -> Optional[float]:
    """Realised P&L from today's CLOSED paper trades (net_pnl is already signed
    + charge-deducted by close_paper_trade)."""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT net_pnl
            FROM paper_trades
            WHERE strategy_name = ?
              AND state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl    IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') = date('now', 'localtime')
            """,
            (strategy_id,),
        ).fetchall()
        total = 0.0
        for (net_pnl,) in rows:
            total += float(net_pnl or 0.0)
        return float(total)
    except Exception as e:
        write_audit_log(f"[RISK][ERROR] paper pnl fetch failed STRATEGY={strategy_id} ERR={e}")
        return None


def _strategy_mode(strategy_id: str) -> str:
    try:
        return load_strategy_config(strategy_id).get("trade_execution_mode", "LIVE")
    except Exception:
        return "LIVE"


def today_realised_pnl(strategy_id: str) -> Optional[float]:
    """Mode-aware today's realised P&L. None on error (caller fails closed)."""
    mode = _strategy_mode(strategy_id)
    if mode == "PAPER":
        return _today_paper_pnl(strategy_id)
    return _today_live_pnl(strategy_id)


# ---------------------------------------------------------------------------
# Limits from config
# ---------------------------------------------------------------------------

def _limits(strategy_id: str):
    """Returns (max_loss, max_profit) as positive rupee magnitudes; 0 = disabled."""
    try:
        cfg = load_strategy_config(strategy_id)
        ml = float(cfg.get("max_loss", 0) or 0)
        mp = float(cfg.get("max_profit", 0) or 0)
        return abs(ml), abs(mp)
    except Exception as e:
        write_audit_log(f"[RISK][ERROR] limit fetch failed STRATEGY={strategy_id} ERR={e}")
        # Fail closed: signal error via sentinel (None, None)
        return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_strategy_risk(strategy_id: str) -> str:
    """
    Returns a reason string if NEW ENTRIES should be blocked, else None.
      REASON_MAX_LOSS / REASON_MAX_PROFIT / REASON_ERROR  -> block
      None (REASON_OK)                                    -> allowed
    Fail-safe: on any inability to determine P&L or limits, blocks (fail closed).
    """
    max_loss, max_profit = _limits(strategy_id)
    if max_loss is None:           # error reading config
        return REASON_ERROR

    # Both disabled → nothing to enforce, allow.
    if max_loss <= 0 and max_profit <= 0:
        return REASON_OK

    pnl = today_realised_pnl(strategy_id)
    if pnl is None:                # P&L unavailable → fail closed
        write_audit_log(f"[RISK][WARN] pnl unavailable STRATEGY={strategy_id} — blocking")
        return REASON_ERROR

    if max_loss > 0 and pnl <= -max_loss:
        write_audit_log(f"[RISK][BLOCK] MAX_LOSS STRATEGY={strategy_id} pnl={pnl:.2f} limit=-{max_loss:.2f}")
        return REASON_MAX_LOSS

    if max_profit > 0 and pnl >= max_profit:
        write_audit_log(f"[RISK][BLOCK] MAX_PROFIT STRATEGY={strategy_id} pnl={pnl:.2f} limit={max_profit:.2f}")
        return REASON_MAX_PROFIT

    return REASON_OK


# ---------------------------------------------------------------------------
# Back-compat shim
# ---------------------------------------------------------------------------

def check_strategy_max_loss(strategy_id: str) -> bool:
    """
    Backward-compatible boolean: True = block (limit hit or error).
    Now also covers Max Profit and is daily + mode-aware.
    Existing callers (SCALP_V1 on_buy_signal/on_sell_signal) keep working.
    """
    return evaluate_strategy_risk(strategy_id) is not REASON_OK