# backend/app/api/scalpv5_state_routes.py
#
# SCALP_V5 panel state — purpose-built for ScalpV5Panel.
#
# SCALP_V5 has at most ONE open trade on ONE instrument (it buys the signalling
# contract directly — no hedge). So the response is simpler than V3's.
#
# Response shape:
# {
#   "mode": "PAPER" | "LIVE",
#   "day_blocked": true/false,                   # V5-local MTM re-entry latch
#   "selection": { "CE": [...], "PE": [...] },   # under-surveillance strikes
#   "open_trade": {                              # null when flat
#       "v5_trade_id": "...",
#       "paper": true/false,
#       "symbol","side","entry","sl","tp","qty",
#       "gtt_id",
#       "entry_candle_ts",
#       "state": "OPEN"
#   }
# }
#
# Isolated: reads only scalpv5_repo + selection files + config + the V5 latch.
# If V5 has never run (no table), returns empty/flat.

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import load_selection
from app.db.scalpv5_repo import get_open_v5_trade

router = APIRouter(tags=["scalp-v5"])

STRATEGY_ID = "SCALP_V5"


@router.get("/api/scalp_v5/state")
def get_scalp_v5_state():
    try:
        cfg  = load_strategy_config(STRATEGY_ID)
        mode = (cfg.get("trade_execution_mode", "PAPER") or "PAPER").upper()
    except Exception:
        mode = "PAPER"

    # V5-local MTM day-block latch (best-effort).
    try:
        from app.engine.scalpv5.scalpv5_manager import is_v5_day_blocked
        day_blocked = bool(is_v5_day_blocked())
    except Exception:
        day_blocked = False

    # Selection (under-surveillance strikes) — best-effort.
    try:
        sel = load_selection(STRATEGY_ID)  # {"CE":[...], "PE":[...]}
    except Exception as e:
        write_audit_log(f"[API][V5_STATE][SEL_ERR] {e}")
        sel = {"CE": [], "PE": []}

    # The single open trade (or None).
    open_trade = None
    try:
        row = get_open_v5_trade()
        if row:
            open_trade = {
                "v5_trade_id":     row.get("v5_trade_id"),
                "paper":           bool(row.get("paper")),
                "symbol":          row.get("symbol"),
                "side":            row.get("side"),
                "entry":           row.get("entry_price"),
                "sl":              row.get("sl_price"),
                "tp":              row.get("tp_price"),
                "qty":             row.get("qty"),
                "gtt_id":          row.get("gtt_id"),
                "entry_candle_ts": row.get("entry_candle_ts"),
                "state":           row.get("state"),
            }
    except Exception as e:
        # Table may not exist yet (strategy never ran) — flat is correct.
        write_audit_log(f"[API][V5_STATE][OPEN_ERR] {e}")

    return {
        "mode":        mode,
        "day_blocked": day_blocked,
        "selection":   {"CE": sel.get("CE", []), "PE": sel.get("PE", [])},
        "open_trade":  open_trade,
    }