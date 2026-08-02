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
#                   "carried","is_adjust","adjust_of","entry_date","expiry",
#                   "phantom","gtt_ids":[...], "pnl": float|null } ],
#       "adjust_only": bool, "carry_committed": bool,
#       "pending": {"mtc": {...}, "adjust": {...}}
#   },
#   "entry_time","exit_time","latched_today": true/false,
#   "exit_mode","next_open_time","expiry_exit_time"
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

# ── IC_MTM BEGIN ── live LTP / unrealized enrichment (2026-07-30). The
# engine REST-polls open-leg LTPs into LTPStore every 4s; the panel polls
# this route every 5s — so open legs can show LTP, live P&L, and a group
# MTM without any new data path. Isolated try/excepts: pricing enrichment
# must never break the state response.
import time as _time
try:
    from app.marketdata.ltp_store import LTPStore as _LTPStore
except Exception:
    _LTPStore = None


def _leg_ltp(symbol: str):
    """(ltp, age_s) from LTPStore; (None, None) when unavailable."""
    if _LTPStore is None or not symbol:
        return None, None
    try:
        res = _LTPStore.get_with_timestamp(symbol)
        if not res:
            return None, None
        ltp, ts = res
        if not ltp or ltp <= 0:
            return None, None
        return float(ltp), max(0, int(_time.time() - ts))
    except Exception:
        return None, None
# ── IC_MTM END ──


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
                        "carried":       bool(getattr(leg, "carried", False)),
                        "is_adjust":     bool(getattr(leg, "is_adjust", False)),
                        "adjust_of":     getattr(leg, "adjust_of", None),
                        "entry_date":    getattr(leg, "entry_date", ""),
                        "expiry":        getattr(leg, "expiry", ""),
                        "phantom":       bool(rt.get("phantom")),
                        "gtt_ids":       list(rt.get("gtt_ids") or []),
                        "pnl":           leg.pnl(),
                        # ── IC_MTM ── live enrichment for OPEN legs
                        "ltp":           None,
                        "ltp_age_s":     None,
                        "open_pnl":      None,
                    })
                    if leg.state == "OPEN":
                        _ltp, _age = _leg_ltp(leg.symbol)
                        if _ltp is not None:
                            _row = legs[-1]
                            _row["ltp"] = round(_ltp, 2)
                            _row["ltp_age_s"] = _age
                            if leg.action == "SELL":
                                _row["open_pnl"] = round((leg.entry_price - _ltp) * leg.qty, 2)
                            else:
                                _row["open_pnl"] = round((_ltp - leg.entry_price) * leg.qty, 2)
                legs.sort(key=lambda l: l["leg_id"])
                # ── IC_MTM ── group aggregates. Booked legs only (phantom
                # sim legs excluded, matching the panel's realised line).
                # mtm is None while any open booked leg lacks a price — a
                # partial MTM presented as total would be a lie.
                _realized = sum((l["pnl"] or 0.0) for l in legs
                                if l["state"] == "CLOSED" and not l["phantom"])
                _uvals = [l["open_pnl"] for l in legs
                          if l["state"] == "OPEN" and not l["phantom"]
                          and l["open_pnl"] is not None]
                _open_total = sum(1 for l in legs
                                  if l["state"] == "OPEN" and not l["phantom"])
                _unreal = sum(_uvals) if _uvals else (0.0 if _open_total == 0 else None)
                _mtm = None
                if _open_total == len(_uvals):
                    _mtm = _realized + (sum(_uvals) if _uvals else 0.0)
                group_out = {
                    "realized_pnl":     round(_realized, 2),
                    "unrealized_pnl":   (round(_unreal, 2) if _unreal is not None else None),
                    "mtm":              (round(_mtm, 2) if _mtm is not None else None),
                    "open_legs_priced": len(_uvals),
                    "open_legs_total":  _open_total,
                    "state":            core.state,
                    "paper":            gm.is_paper(),
                    "mtc_fired":        core.mtc_fired,
                    "double_sl_minute": core.double_sl_minute,
                    "adjust_only":      bool(getattr(gm, "is_adjust_only", lambda: False)()),
                    "carry_committed":  bool(getattr(gm, "carry_committed", lambda: False)()),
                    "pending":          (gm.pending_view() if hasattr(gm, "pending_view") else {}),
                    "legs":             legs,
                }
    except Exception as e:
        write_audit_log(f"[API][IC_STATE][ERR] {e}")

    return {
        "mode":             mode,
        "engine_up":        engine_up,
        "group":            group_out,
        "entry_time":       cfg.get("entry_time", "09:18"),
        "exit_time":        cfg.get("exit_time", "15:28"),
        "exit_mode":        str(cfg.get("exit_mode", "NEXT_OPEN") or "NEXT_OPEN").upper(),
        "next_open_time":   cfg.get("next_open_time", "09:16"),
        "expiry_exit_time": cfg.get("expiry_exit_time", "15:28"),
        "latched_today":    latched,
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
