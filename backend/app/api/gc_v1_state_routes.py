# backend/app/api/gc_v1_state_routes.py
#
# ── GC_V1 ── panel state + manual square-off. Isolated: if the runtime
# never launched, returns mode from config with day=null (checklist 2.4).
# Kill goes through the shared POST /api/kill/GC_V1 framework (LD12).

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config

router = APIRouter(tags=["gc-v1"])

STRATEGY_ID = "GC_V1"


def _cfg() -> dict:
    try:
        return load_strategy_config(STRATEGY_ID) or {}
    except Exception:
        return {}


@router.get("/api/gc_v1/state")
def gc_v1_state():
    cfg = _cfg()
    out = {
        "mode": str(cfg.get("trade_execution_mode", "OFF")).upper(),
        "engine_up": False,
        "day": None,
        "gc_mode": str(cfg.get("mode", "SELL")).upper(),
        "exit_time": cfg.get("exit_time", "15:15"),
        "entry_cutoff_time": cfg.get("entry_cutoff_time", "13:00"),
        "premium_max": cfg.get("premium_max", 200),
        "hedge_premium_max": cfg.get("hedge_premium_max", 5),
        "lots": cfg.get("lots", 1),
        "max_trades_per_day": cfg.get("max_trades_per_day", 4),
    }
    try:
        from app.engine.gc.gc_runtime import get_gc_manager, gc_engine_up
        gm = get_gc_manager()
        out["engine_up"] = gc_engine_up()
        if gm is not None:
            out["day"] = gm.snapshot()
    except Exception as e:
        out["error"] = str(e)
    return out


@router.post("/api/gc_v1/square_off")
def gc_v1_square_off():
    try:
        from app.engine.gc.gc_runtime import get_gc_manager
        gm = get_gc_manager()
        if gm is None:
            return {"ok": True, "flattened_legs": 0, "note": "runtime down"}
        n = gm.square_off_all("MANUAL")
        write_audit_log(f"[GC][SQUARE_OFF][MANUAL] {n} leg(s)")
        return {"ok": True, "flattened_legs": n}
    except Exception as e:
        write_audit_log(f"[GC][SQUARE_OFF][ERR] {e!r}")
        return {"ok": False, "error": str(e)}