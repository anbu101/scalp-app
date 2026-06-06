# backend/app/api/scalp_v3_state_routes.py
#
# SCALP_V3 panel state — purpose-built for ScalpV3Panel.
#
# Unlike SCALP_V1 (4 independent slots), SCALP_V3 has at most ONE open trade,
# and that trade spans TWO instruments:
#   - signal contract (tracked, never traded): drives the exit
#   - hedge contract  (bought, protected):     carries the P&L
#
# Response shape:
# {
#   "mode": "PAPER" | "LIVE",
#   "selection": { "CE": [...], "PE": [...] },   # under-surveillance strikes
#   "open_trade": {                              # null when flat
#       "v3_trade_id": "...",
#       "paper": true/false,
#       "signal": { "symbol","side","entry","sl","tp" },
#       "hedge":  { "symbol","side","entry","sl","qty","gtt_id" },
#       "state": "OPEN"
#   }
# }
#
# Isolated: reads only scalp_v3_repo + selection files + config. No other
# strategy is touched. If V3 has never run (no table), returns empty/flat.

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import load_selection
from app.db.scalp_v3_repo import get_open_v3_trade

router = APIRouter(tags=["scalp-v3"])

STRATEGY_ID = "SCALP_V3"


@router.get("/api/scalp_v3/state")
def get_scalp_v3_state():
    try:
        cfg  = load_strategy_config(STRATEGY_ID)
        mode = (cfg.get("trade_execution_mode", "PAPER") or "PAPER").upper()
    except Exception:
        mode = "PAPER"

    # Selection (under-surveillance strikes) — best-effort.
    try:
        sel = load_selection(STRATEGY_ID)  # {"CE":[...], "PE":[...]}
    except Exception as e:
        write_audit_log(f"[API][V3_STATE][SEL_ERR] {e}")
        sel = {"CE": [], "PE": []}

    # The single open trade (or None).
    open_trade = None
    try:
        row = get_open_v3_trade()
        if row:
            open_trade = {
                "v3_trade_id": row.get("v3_trade_id"),
                "paper":       bool(row.get("paper")),
                "signal": {
                    "symbol": row.get("signal_symbol"),
                    "side":   row.get("signal_side"),
                    "entry":  row.get("signal_entry_price"),
                    "sl":     row.get("signal_sl"),
                    "tp":     row.get("signal_tp"),
                },
                "hedge": {
                    "symbol": row.get("hedge_symbol"),
                    "side":   row.get("hedge_side"),
                    "entry":  row.get("hedge_entry_price"),
                    "sl":     row.get("hedge_sl"),
                    "qty":    row.get("hedge_qty"),
                    "gtt_id": row.get("hedge_gtt_id"),
                },
                "state": row.get("state"),
            }
    except Exception as e:
        # Table may not exist yet (strategy never ran) — flat is correct.
        write_audit_log(f"[API][V3_STATE][OPEN_ERR] {e}")

    return {
        "mode":       mode,
        "selection":  {"CE": sel.get("CE", []), "PE": sel.get("PE", [])},
        "open_trade": open_trade,
    }