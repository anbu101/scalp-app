# backend/app/api/scalp_v2_api.py
#
# SCALP_V2 runtime state for the dashboard panel.
# ============================================================================
# Exposes ONE endpoint the ScalpV2Panel polls:
#
#   GET /api/scalp_v2/state?side=CE        (side optional; defaults to both)
#
# It merges TWO sources:
#   1. SURVEILLANCE — the contracts under watch per class A/B/C, from the
#      group manager's selection_provider (the same list the engine elects
#      from). Each contract is annotated with live premium (LTPStore→REST)
#      and whether it is currently in-band (an eligible entry candidate).
#   2. LIVE GROUP — if a group is active, its master class, status, and per-leg
#      entry/sl/tp/state/pnl from current_group().
#
# This is ADDITIVE and ISOLATED: it only READS group-manager state via the
# already-exposed get_group_manager() / selection_provider / candidate_provider.
# It never mutates anything and touches no other strategy.
#
# If the selection loop hasn't started (no group manager yet), it returns a
# well-formed empty payload so the panel renders its idle state cleanly.
# ============================================================================

from fastapi import APIRouter, Query
from typing import Optional

from app.event_bus.audit_logger import write_audit_log

router = APIRouter()

CLASSES = ["A", "B", "C"]


def _safe_get_group_manager():
    """Import lazily — selection loop may not have started yet."""
    try:
        from app.engine.scalp_v2.scalp_v2_selection_loop import get_group_manager
        return get_group_manager()
    except Exception:
        return None


def _class_band(gm, trade_class):
    try:
        return gm._class_band(trade_class)
    except Exception:
        return (0, 0)


def _class_lots(gm, trade_class):
    try:
        return gm._class_lots(trade_class)
    except Exception:
        return 0


def _live_premium(gm, symbol):
    try:
        return gm._live_premium(symbol)
    except Exception:
        return None


def _serialize_leg(leg):
    """LegState -> JSON (None-safe)."""
    if leg is None:
        return None
    return {
        "trade_class": leg.trade_class,
        "symbol":      leg.symbol,
        "qty":         leg.qty,
        "entry_price": leg.entry_price,
        "sl":          leg.sl,
        "tp":          leg.tp,
        "is_master":   leg.is_master,
        "open":        leg.open,
        "exit_price":  leg.exit_price,
        "exit_reason": leg.exit_reason,
        "realized_pnl": leg.realized_pnl(),
    }


def _surveillance_for_class(gm, trade_class, sides):
    """
    Build the surveillance list for one class: every watched contract with
    its live premium and in-band flag. The highest in-band contract is the
    one the engine would pick for this class (marked `armed_pick`).
    """
    lo, hi = _class_band(gm, trade_class)
    watched = []

    for side in sides:
        try:
            symbols = gm.selection_provider(trade_class, side) or []
        except Exception:
            symbols = []
        for sym in symbols:
            prem = _live_premium(gm, sym)
            in_band = (prem is not None and lo <= prem <= hi)
            watched.append({
                "symbol":  sym,
                "side":    side,
                "premium": prem,
                "in_band": in_band,
            })

    # The engine's pick per side = highest in-band premium. Mark it.
    armed_pick_symbol = None
    in_band_sorted = sorted(
        [w for w in watched if w["in_band"]],
        key=lambda w: w["premium"],
        reverse=True,
    )
    if in_band_sorted:
        armed_pick_symbol = in_band_sorted[0]["symbol"]
    for w in watched:
        w["armed_pick"] = (w["symbol"] == armed_pick_symbol)

    return {
        "trade_class": trade_class,
        "band":        {"min": lo, "max": hi},
        "lots":        _class_lots(gm, trade_class),
        "watched":     watched,
        "armed_pick":  armed_pick_symbol,
    }


@router.get("/api/scalp_v2/state")
def scalp_v2_state(side: Optional[str] = Query(default=None)):
    """
    Returns:
    {
      "available": bool,                  # selection loop / group mgr ready
      "mode": "PAPER"|"LIVE",
      "stagger_seconds": int,
      "group": {                          # null if no active group
         "group_id", "status", "direction",
         "master_class", "master_instrument",
         "sl_pct", "tp_pct", "paper",
         "exit_reason",
         "realized_pnl",                  # sum of closed legs
         "legs": { "A": {...}|null, "B": ..., "C": ... }
      } | null,
      "classes": [                        # surveillance, always present
         { "trade_class","band","lots","watched":[...],"armed_pick" }, ...
      ]
    }
    """
    gm = _safe_get_group_manager()

    if gm is None:
        # Selection loop not started (e.g. SCALP_V2 disabled or pre-market).
        return {
            "available": False,
            "mode": None,
            "stagger_seconds": None,
            "group": None,
            "classes": [
                {"trade_class": c, "band": {"min": 0, "max": 0},
                 "lots": 0, "watched": [], "armed_pick": None}
                for c in CLASSES
            ],
        }

    sides = [side] if side in ("CE", "PE") else ["CE", "PE"]

    # ---- config-derived header bits ----
    try:
        mode = "PAPER" if gm._is_paper() else "LIVE"
    except Exception:
        mode = None
    try:
        stagger = gm._stagger_sec()
    except Exception:
        stagger = None

    # ---- live group ----
    group_json = None
    try:
        g = gm.current_group()
    except Exception:
        g = None

    if g is not None:
        legs = {c: _serialize_leg(g.legs.get(c)) for c in CLASSES}
        realized = 0.0
        any_realized = False
        for c in CLASSES:
            lg = g.legs.get(c)
            if lg is not None and lg.realized_pnl() is not None:
                realized += lg.realized_pnl()
                any_realized = True
        group_json = {
            "group_id":          g.group_id,
            "status":            g.status,
            "direction":         g.direction,
            "master_class":      g.master_class,
            "master_instrument": g.master_instrument,
            "sl_pct":            g.sl_pct,
            "tp_pct":            g.tp_pct,
            "paper":             g.paper,
            "exit_reason":       g.exit_reason,
            "realized_pnl":      realized if any_realized else None,
            "legs":              legs,
        }

    # ---- surveillance per class ----
    classes = [_surveillance_for_class(gm, c, sides) for c in CLASSES]

    return {
        "available": True,
        "mode": mode,
        "stagger_seconds": stagger,
        "group": group_json,
        "classes": classes,
    }