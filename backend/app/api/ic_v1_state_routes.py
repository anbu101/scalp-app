# backend/app/api/ic_v1_state_routes.py
#
# IC_V1 panel state — purpose-built for ICV1Panel.
#
# Response shape (GET /api/ic_v1/state):
# {
#   "mode": "OFF" | "PAPER" | "LIVE",
#   "engine_up": true/false,
#   "group": {                          # null when no group today
#       "state": "ENTERING|OPEN|CLOSING|CLOSED|ABORTED",
#       "paper": true/false,
#       "mtc_fired": true/false,
#       "double_sl_minute": true/false,
#       "legs": [ { "leg_id","action","opt_type","symbol","qty",
#                   "entry_price","sl","tp","state","exit_price",
#                   "exit_reason","mtc_repinned","wing_fallback",
#                   "gtt_ids":[...], "pnl": float|null } ]
#   },
#   "entry_time","exit_time","latched_today": true/false
# }
#
# POST /api/ic_v1/square_off — manual flatten (reason=MANUAL). Same code path
# as EOD; safe no-op when nothing is open.
#
# Isolated: reads only the IC_V1 runtime singletons + config. If the runtime
# has never launched, returns mode from config with group=null.

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config

router = APIRouter(tags=["ic-v1"])

STRATEGY_ID = "IC_V1"


def _cfg() -> dict:
    try:
        return load_strategy_config(STRATEGY_ID) or {}
    except Exception:
        return {}


@router.get("/api/ic_v1/state")
def get_ic_v1_state():
    cfg = _cfg()
    mode = (cfg.get("trade_execution_mode", "OFF") or "OFF").upper()

    engine_up = False
    group_out = None
    latched = False
    try:
        from app.engine.ic_v1.ic_runtime import get_ic_manager, get_ic_engine
        gm = get_ic_manager()
        engine_up = get_ic_engine() is not None
        if gm is not None:
            try:
                latched = bool(gm._latch_today())
            except Exception:
                latched = False
            core = gm.current_group()
            if core is not None:
                legs = []
                for leg in core.legs.values():
                    rt = gm.leg_runtime(leg.leg_id)
                    legs.append({
                        "leg_id":        leg.leg_id,
                        "action":        leg.action,
                        "opt_type":      leg.opt_type,
                        "symbol":        leg.symbol,
                        "qty":           leg.qty,
                        "entry_price":   leg.entry_price,
                        "sl":            leg.sl,
                        "tp":            leg.tp,
                        "state":         leg.state,
                        "exit_price":    leg.exit_price,
                        "exit_reason":   leg.exit_reason,
                        "mtc_repinned":  leg.mtc_repinned,
                        "wing_fallback": leg.wing_fallback,
                        "gtt_ids":       list(rt.get("gtt_ids") or []),
                        "pnl":           leg.pnl(),
                    })
                legs.sort(key=lambda l: l["leg_id"])
                group_out = {
                    "state":            core.state,
                    "paper":            gm.is_paper(),
                    "mtc_fired":        core.mtc_fired,
                    "double_sl_minute": core.double_sl_minute,
                    "legs":             legs,
                }
    except Exception as e:
        write_audit_log(f"[API][IC_STATE][ERR] {e}")

    return {
        "mode":          mode,
        "engine_up":     engine_up,
        "group":         group_out,
        "entry_time":    cfg.get("entry_time", "09:18"),
        "exit_time":     cfg.get("exit_time", "15:28"),
        "latched_today": latched,
    }


@router.post("/api/ic_v1/square_off")
def post_ic_v1_square_off():
    try:
        from app.engine.ic_v1.ic_runtime import get_ic_manager
        gm = get_ic_manager()
        if gm is None:
            return {"ok": False, "closed": 0, "detail": "runtime not initialized"}
        n = gm.force_square_off_all(reason="MANUAL")
        write_audit_log(f"[API][IC_SQUAREOFF] manual — closed={n}")
        return {"ok": True, "closed": n}
    except Exception as e:
        write_audit_log(f"[API][IC_SQUAREOFF][ERR] {e}")
        return {"ok": False, "closed": 0, "detail": str(e)}
