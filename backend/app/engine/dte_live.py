# backend/app/engine/dte_live.py
#
# ── DTE_LOT_MULT_LIVE_20260911 ── live per-DTE lot multiplier.
# Same key/semantics as the backtest knob (app.backtest.engine.dte_lots):
#   dte_lot_mult = {dte: mult}; absent → ×1; 0 → skip the day;
#   lots_today = max(1, round(lots × mult)).
# DTE = trading SESSIONS from today to expected_expiry_for_day(today),
# counted with is_trading_day (weekday + NSE holiday list). A Tuesday
# holiday moves the real expiry to Monday; counting sessions to the
# expected Tuesday makes that Monday 0DTE with no special casing.
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple

from app.event_bus.audit_logger import write_audit_log


def live_dte(day: date) -> Optional[int]:
    """Trading sessions in (day, expected_expiry]. 0 on expiry day.
    None if the calendar cannot be evaluated (caller fails open)."""
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
        from app.utils.market_hours import is_trading_day
        exp = expected_expiry_for_day(day)
        n = 0
        d = day + timedelta(days=1)
        while d <= exp:
            if is_trading_day(d):
                n += 1
            d += timedelta(days=1)
        return n
    except Exception as e:
        write_audit_log(f"[DTE][LIVE] calendar failed ({e!r}) — DTE unknown")
        return None


def resolve_day_lots(cfg: dict, base_lots: int, day: Optional[date] = None,
                     tag_prefix: str = "") -> Tuple[int, Optional[int], str]:
    """→ (lots_today, dte, tag) with tag in {"base","scaled","skip","unknown"}.
    Never raises; unknown/failed → base lots (fail-open to the sealed config)."""
    try:
        from app.backtest.engine.dte_lots import parse_dte_lot_mult, lots_for_day
        mult = parse_dte_lot_mult((cfg or {}).get("dte_lot_mult"))
        if not mult:
            return int(base_lots or 1), None, "base"
        dte = live_dte(day or date.today())
        lots, tag = lots_for_day(int(base_lots or 1), mult, dte)
        write_audit_log(f"[DTE][LIVE]{tag_prefix} today {dte if dte is not None else '?'}DTE "
                        f"→ {tag} (lots {lots}, base {base_lots}, map {mult})")
        return lots, dte, tag
    except Exception as e:
        write_audit_log(f"[DTE][LIVE]{tag_prefix} resolve failed ({e!r}) — base lots")
        return int(base_lots or 1), None, "unknown"
