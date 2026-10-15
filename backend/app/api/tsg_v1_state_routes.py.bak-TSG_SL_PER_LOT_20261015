# backend/app/api/tsg_v1_state_routes.py
#
# TSG_V1 panel state — purpose-built for TSGV1Panel (LD9).
#
# GET /api/tsg_v1/state:
# { "mode": "OFF"|"PAPER"|"LIVE", "engine_up": bool,
#   "day": {  # null when no day yet
#     "state", "paper", "expiry", "day_mtm", "realized",
#     "peak_mtm", "iv_armed_used", "skip_reason",
#     "legs": [ {leg_id, action, opt_type, symbol, strike, qty,
#                entry_price, entry_iv, iv_threshold, last_mark,
#                state, exit_price, exit_reason, pnl} ] },
#   "entry_time","exit_time","lots","expiry_lots","mtm_sl",
#   "iv_sl_delta_pts","latched_today" }
#
# POST /api/tsg_v1/square_off — manual flatten (reason=MANUAL). Safe no-op.
# Kill goes through the shared POST /api/kill/TSG_V1 framework (LD7).
#
# Isolated: reads only the TSG runtime singletons + config; if the runtime
# never launched, returns mode from config with day=null.

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config

router = APIRouter(tags=["tsg-v1"])

STRATEGY_ID = "TSG_V1"


def _cfg() -> dict:
    try:
        return load_strategy_config(STRATEGY_ID) or {}
    except Exception:
        return {}


@router.get("/api/tsg_v1/state")
def tsg_v1_state():
    cfg = _cfg()
    out = {
        "mode": str(cfg.get("trade_execution_mode", "OFF")).upper(),
        "engine_up": False,
        "day": None,
        "entry_time": cfg.get("entry_time", "09:16"),
        "exit_time": cfg.get("exit_time", "15:26"),
        "lots": cfg.get("lots", 1),
        "expiry_lots": cfg.get("expiry_lots", 0),
        "mtm_sl": cfg.get("mtm_sl", 35000),
        "mtm_target": cfg.get("mtm_target", 0),
        "iv_sl_delta_pts": cfg.get("iv_sl_delta_pts", 4),
        "iv_sl_pct": cfg.get("iv_sl_pct", 0),
        "latched_today": False,
    }
    try:
        from app.engine.tsg.tsg_runtime import get_tsg_manager, \
            get_tsg_engine
        gm = get_tsg_manager()
        out["engine_up"] = get_tsg_engine() is not None
        if gm is None:
            return out
        out["latched_today"] = gm.latched_today()
        snap = gm.snapshot()
        if snap is None:
            return out
        legs = []
        for lid, l in (snap.get("legs") or {}).items():
            entry = l.get("entry_price")
            mark = l.get("last_mark")
            px = l.get("exit_price") if l.get("state") == "CLOSED" else mark
            pnl = None
            if entry is not None and px is not None:
                d = (entry - px) if l.get("action") == "SELL" else (px - entry)
                pnl = d * (l.get("qty") or 0)
            legs.append({**l, "pnl": pnl})
        legs.sort(key=lambda x: x.get("leg_id") or "")
        out["day"] = {
            "state": snap.get("state"), "paper": snap.get("paper"),
            # LD5a: the day's EFFECTIVE levels (config × lots-ratio on
            # expiry days) — the panel must show these, not the config
            "mtm_sl_effective": snap.get("mtm_sl"),
            "mtm_target_effective": snap.get("mtm_target"),
            "expiry": snap.get("expiry"),
            "day_mtm": snap.get("day_mtm"),
            "realized": snap.get("realized"),
            "peak_mtm": snap.get("peak_mtm"),
            "iv_armed_used": snap.get("iv_armed_used"),
            "skip_reason": snap.get("skip_reason"),
            "legs": legs,
        }
    except Exception as e:
        write_audit_log(f"[TSG][STATE_ROUTE][ERR] {e!r}")
    return out


@router.post("/api/tsg_v1/square_off")
def tsg_v1_square_off():
    try:
        from app.engine.tsg.tsg_runtime import get_tsg_manager
        gm = get_tsg_manager()
        if gm is None:
            return {"ok": False, "reason": "runtime not up"}
        n = gm.square_off_all("MANUAL")
        write_audit_log(f"[TSG][MANUAL] square-off: {n} leg(s)")
        return {"ok": True, "closed": n}
    except Exception as e:
        return {"ok": False, "reason": repr(e)}