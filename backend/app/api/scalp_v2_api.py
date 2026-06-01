# backend/app/api/scalp_v2_api.py
#
# SCALP_V2 runtime state for the dashboard panel (v2.0 — 3-leg model).
# ============================================================================
# GET /api/scalp_v2/state
#
# Returns:
#   - surveillance: the selected CE/PE contracts under watch (single premium
#     range, like SCALP_V1) with live premium + in-range flag.
#   - group: the active 3-leg group if any (L1 signal / L2 +1 / L3 -1) with
#     per-leg entry/sl/tp/state/pnl, plus rolled-up realized P&L.
#
# READ-ONLY. Touches no other strategy. Returns a well-formed empty payload if
# the selection loop / group manager hasn't started.
# ============================================================================

from fastapi import APIRouter
from typing import Optional

from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import load_selection
from app.marketdata.ltp_store import LTPStore

router = APIRouter()

STRATEGY_ID = "SCALP_V2"
LEG_ORDER = ["L1", "L2", "L3"]


def _safe_get_group_manager():
    try:
        from app.engine.scalp_v2.scalp_v2_selection_loop import get_group_manager
        return get_group_manager()
    except Exception:
        return None


def _premium_range():
    try:
        p = load_strategy_config(STRATEGY_ID).get("option_premium", {})
        return float(p.get("min", 0)), float(p.get("max", 0))
    except Exception:
        return 0.0, 0.0


def _live_premium(symbol: str):
    """Fresh LTPStore tick for display; None if unavailable (UI shows '—')."""
    try:
        result = LTPStore.get_with_timestamp(symbol)
        if result is not None:
            ltp, ts = result
            if ltp and ltp > 0:
                return float(ltp)
    except Exception:
        pass
    return None


def _serialize_leg(leg):
    if leg is None:
        return None
    return {
        "leg":         leg.trade_class,     # "L1" | "L2" | "L3"
        "symbol":      leg.symbol,
        "qty":         leg.qty,
        "entry_price": leg.entry_price,
        "sl":          leg.sl,
        "tp":          leg.tp,
        "is_master":   leg.is_master,       # L1 (signal leg)
        "open":        leg.open,
        "exit_price":  leg.exit_price,
        "exit_reason": leg.exit_reason,
        "realized_pnl": leg.realized_pnl(),
    }


def _surveillance():
    """The selected CE/PE contracts under watch, with live premium + in-range."""
    lo, hi = _premium_range()
    watched = []
    try:
        sel = load_selection(STRATEGY_ID)
    except Exception:
        sel = {"CE": [], "PE": []}

    for side in ("CE", "PE"):
        for o in sel.get(side, []):
            sym = o.get("symbol") or o.get("tradingsymbol")
            if not sym:
                continue
            prem = _live_premium(sym)
            in_range = (prem is not None and lo <= prem <= hi)
            watched.append({
                "symbol":   sym,
                "side":     side,
                "premium":  prem,
                "in_band":  in_range,   # kept key name for panel compatibility
            })
    return watched


@router.get("/api/scalp_v2/state")
def scalp_v2_state():
    gm = _safe_get_group_manager()
    lo, hi = _premium_range()

    if gm is None:
        return {
            "available": False,
            "mode": None,
            "premium_range": {"min": lo, "max": hi},
            "group": None,
            "watched": [],
        }

    try:
        mode = "PAPER" if gm._is_paper() else "LIVE"
    except Exception:
        mode = None

    # ---- active group ----
    group_json = None
    try:
        g = gm.current_group()
    except Exception:
        g = None

    if g is not None:
        legs = {role: _serialize_leg(g.legs.get(role)) for role in LEG_ORDER}
        realized = 0.0
        any_realized = False
        for role in LEG_ORDER:
            lg = g.legs.get(role)
            if lg is not None and lg.realized_pnl() is not None:
                realized += lg.realized_pnl()
                any_realized = True
        group_json = {
            "group_id":          g.group_id,
            "status":            g.status,
            "direction":         g.direction,
            "signal_instrument": g.master_instrument,
            "sl_pct":            g.sl_pct,
            "tp_pct":            g.tp_pct,
            "paper":             g.paper,
            "exit_reason":       g.exit_reason,
            "realized_pnl":      realized if any_realized else None,
            "legs":              legs,
        }

    return {
        "available": True,
        "mode": mode,
        "premium_range": {"min": lo, "max": hi},
        "group": group_json,
        "watched": _surveillance(),
    }