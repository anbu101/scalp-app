# backend/app/trading/shadow_book.py
#
# ── FLEET_MODES_20260923 ── the PAPER twin behind PAPER_LIVE.
#
# When a strategy is in PAPER_LIVE its engine runs the LIVE path unchanged.
# This module attaches at the persistence chokepoints — every store's
# insert-live-row / close-live-row — and mirrors each LIVE position as an
# ordinary `paper_trades` row (trade_mode='PAPER', id 'SHD:…'). Being an
# ordinary paper row it shows on the Paper Trades page, the ClosedRecent
# card and Analytics with no UI work, and for SCALP_V1 it is even managed by
# the DB-driven paper SL/TP logic (true paper parity there).
#
# Lifecycle is tied to the LIVE row (same lots, same symbol, same qty):
#   * twin opened when the live row is booked (provisional / fill price —
#     whatever the store books at that instant);
#   * twin closed when the live row closes, at the price the caller passes;
#   * live row voided (exit_price None: rejected / unfilled entry) → the twin
#     is DELETED: paper would never have held it.
# A live_ref ↔ shadow_id link is kept in `shadow_links` so a restart finds
# the twin again. Nothing here ever raises into an engine; every failure is
# an audit line. Nothing here places or cancels a broker order.

from __future__ import annotations

import time
import uuid
from typing import Optional

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log

SHADOW_PREFIX = "SHD:"
_schema_ok = False


def is_shadow_id(pid) -> bool:
    return str(pid or "").startswith(SHADOW_PREFIX)


def _ensure_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_links (
            live_ref    TEXT PRIMARY KEY,
            shadow_id   TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            symbol      TEXT,
            created_at  INTEGER
        )
        """
    )
    conn.commit()
    _schema_ok = True


def link_for(live_ref: str) -> Optional[str]:
    try:
        _ensure_schema()
        row = get_conn().execute(
            "SELECT shadow_id FROM shadow_links WHERE live_ref = ?",
            (str(live_ref),)).fetchone()
        return row[0] if row else None
    except Exception as e:
        write_audit_log(f"[SHADOW][LINK_READ_ERR] {live_ref}: {e!r}")
        return None


def open_links(strategy_id: str) -> list:
    try:
        _ensure_schema()
        rows = get_conn().execute(
            "SELECT live_ref, shadow_id, symbol FROM shadow_links "
            "WHERE strategy_id = ?", (strategy_id,)).fetchall()
        return [dict(r) if hasattr(r, "keys") else
                {"live_ref": r[0], "shadow_id": r[1], "symbol": r[2]}
                for r in rows]
    except Exception as e:
        write_audit_log(f"[SHADOW][LINKS_ERR] {strategy_id}: {e!r}")
        return []


def mirror_open(*, live_ref: str, strategy_id: str, symbol: str, side: str,
                entry_price, qty: int, token: int = 0,
                sl_price=0.0, tp_price=0.0, trade_direction: str = "LONG",
                candle_ts: Optional[int] = None, lots: Optional[int] = None,
                lot_size: Optional[int] = None, group_id=None,
                trade_class=None) -> Optional[str]:
    """Book the paper twin of a LIVE row. No-op unless the strategy is in
    PAPER_LIVE (clean read). Returns the shadow id, or None."""
    try:
        if is_shadow_id(live_ref):
            return None                                   # never twin a twin
        from app.risk.execution_modes import shadow_active, lot_size_for
        if not shadow_active(strategy_id):
            return None
        if entry_price is None or float(entry_price) <= 0 or int(qty or 0) <= 0:
            write_audit_log(f"[SHADOW][{strategy_id}][SKIP] {symbol} "
                            f"entry={entry_price} qty={qty} — not mirrored")
            return None
        if not side:                                      # trades table has no side
            _u = str(symbol or "").upper()
            side = "CE" if _u.endswith("CE") else "PE" if _u.endswith("PE") else ""
        _ensure_schema()
        existing = link_for(live_ref)
        if existing:
            return existing
        ls = int(lot_size or lot_size_for(strategy_id))
        lt = int(lots if lots is not None else max(1, int(qty) // max(1, ls)))
        sid = SHADOW_PREFIX + uuid.uuid4().hex
        from app.db.paper_trades_repo import insert_paper_trade
        insert_paper_trade(
            paper_trade_id=sid, strategy_name=strategy_id, trade_mode="PAPER",
            symbol=symbol, token=int(token or 0), side=str(side or ""),
            entry_price=float(entry_price),
            candle_ts=int(candle_ts if candle_ts is not None else time.time()),
            sl_price=float(sl_price or 0.0), tp_price=float(tp_price or 0.0),
            rr=0.0, lots=lt, lot_size=ls, qty=int(qty),
            trade_direction=("SHORT" if str(trade_direction).upper() == "SHORT"
                             else "LONG"),
            group_id=group_id, trade_class=trade_class)
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO shadow_links "
            "(live_ref, shadow_id, strategy_id, symbol, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(live_ref), sid, strategy_id, symbol, int(time.time())))
        conn.commit()
        write_audit_log(f"[SHADOW][{strategy_id}][OPEN] {symbol} "
                        f"{trade_direction} qty={qty} @ {entry_price} "
                        f"twin={sid[:12]} of {str(live_ref)[:24]}")
        return sid
    except Exception as e:
        write_audit_log(f"[SHADOW][{strategy_id}][OPEN_ERR] {symbol}: {e!r}")
        return None


def _unlink(live_ref: str) -> None:
    try:
        conn = get_conn()
        conn.execute("DELETE FROM shadow_links WHERE live_ref = ?",
                     (str(live_ref),))
        conn.commit()
    except Exception as e:
        write_audit_log(f"[SHADOW][UNLINK_ERR] {live_ref}: {e!r}")


def mirror_close(*, live_ref: str, exit_price, exit_reason: str,
                 trade_direction: Optional[str] = None) -> bool:
    """Close the twin of a LIVE row. exit_price None = the live entry was
    voided (rejected / unfilled) → the twin is deleted. Idempotent: a twin
    already closed by paper logic (SCALP_V1) is a logged no-op."""
    try:
        if is_shadow_id(live_ref):
            return False
        sid = link_for(live_ref)
        if not sid:
            return False
        conn = get_conn()
        if exit_price is None or float(exit_price) <= 0:
            conn.execute("DELETE FROM paper_trades WHERE paper_trade_id = ? "
                         "AND state = 'OPEN'", (sid,))
            conn.commit()
            _unlink(live_ref)
            write_audit_log(f"[SHADOW][VOID] twin {sid[:12]} of "
                            f"{str(live_ref)[:24]} removed ({exit_reason}: "
                            f"live entry never became a position)")
            return True
        from app.db.paper_trades_repo import close_paper_trade
        close_paper_trade(paper_trade_id=sid, exit_price=float(exit_price),
                          exit_reason=str(exit_reason or "CLOSED"),
                          trade_direction=trade_direction)
        _unlink(live_ref)
        write_audit_log(f"[SHADOW][CLOSE] twin {sid[:12]} of "
                        f"{str(live_ref)[:24]} @ {exit_price} {exit_reason}")
        return True
    except Exception as e:
        write_audit_log(f"[SHADOW][CLOSE_ERR] {live_ref}: {e!r}")
        return False
