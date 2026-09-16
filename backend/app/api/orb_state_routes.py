# backend/app/api/orb_state_routes.py
#
# ── ORB_V1 STATE ROUTES · v2 ── Fence: ORB_PANEL_V2_20260916
# Composes the dashboard payload from manager + core + engine (read-only,
# isolated try/except everywhere; sane payload when the runtime never
# launched). Prices TICK on the frontend via LTPStore (the engine
# publishes every quote it takes — TSG_LTP_PUBLISH doctrine); this route
# supplies STRUCTURE every 4 s.

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter

router = APIRouter(prefix="/api/orb_v1", tags=["ORB_V1"])
IST = timezone(timedelta(hours=5, minutes=30))

_EMPTY = {"ok": True, "running": False, "strategy": "ORB_V1", "mode": "OFF",
          "phase": "OFFLINE", "position": None, "levels": None, "spot": None,
          "last_bar": None, "day": {}, "cfg": {}, "heartbeat_ts": None,
          "trades": [], "frozen": False, "refused": None}


def _phase(mgr, day, now):
    hm = now.hour * 60 + now.minute
    if day is None:
        return "REFUSED" if (mgr.day_stats or {}).get("refused") else "PRE_OPEN"
    if day.guard.frozen:
        return "FROZEN"
    if day.refused:
        return "REFUSED"
    if mgr.pos is not None:
        return "IN_POSITION"
    if day.orb_high is None:
        return "WINDOW" if hm >= 555 else "PRE_OPEN"
    if hm >= day._eod_min:
        return "DONE"
    if hm >= day._block_min:
        return "NO_NEW_ENTRIES"
    return "ARMED"


@router.get("/state")
def orb_state():
    try:
        from app.engine.orb.orb_runtime import get_orb_manager, get_orb_engine
        mgr, eng = get_orb_manager(), get_orb_engine()
        if mgr is None:
            return dict(_EMPTY)
        now = datetime.now(IST)
        day = mgr.day
        cfg = mgr.cfg() or {}
        out = dict(_EMPTY)
        out.update({
            "running": True, "mode": mgr.mode(),
            "phase": _phase(mgr, day, now),
            "frozen": bool(day and day.guard.frozen),
            "refused": (day.refused if day else None)
                       or (mgr.day_stats or {}).get("refused"),
            "heartbeat_ts": getattr(eng, "last_poll_ts", None),
            "spot": ({"ltp": eng.last_spot, "ts": eng.last_spot_ts}
                     if getattr(eng, "last_spot", None) else None),
            "cfg": {
                "target_value": cfg.get("target_value"),
                "sl_pct": cfg.get("sl_points"),
                "entry_block_time": cfg.get("entry_block_time"),
                "eod_square_off": cfg.get("eod_square_off"),
                "lots": cfg.get("lots"),
                "premium_min": cfg.get("premium_min"),
                "premium_max": cfg.get("premium_max"),
                "max_trades_per_day": cfg.get("max_trades_per_day"),
                "max_trades_per_side": cfg.get("max_trades_per_side"),
            },
            "day": dict(mgr.day_stats or {}),
        })
        if day is not None:
            if day.orb_high is not None:
                out["levels"] = {"high": day.orb_high, "low": day.orb_low,
                                 "range": round(day.orb_high - day.orb_low, 2)}
            if day.prefix:
                b = day.prefix[-1]
                out["last_bar"] = {"ts": b.ts, "close": b.close,
                                   "bars": len(day.prefix)}
            out["day"].update({
                "day_trades": day.day_trades,
                "side_trades": dict(day.side_trades),
                "dropped_open": day.dropped_open,
                "dropped_budget": day.dropped_budget,
                "dropped_block": day.dropped_block,
                "pending_side": day.pending_side,
                "consumed_signals": day.consumed_sigs,
            })
        p = mgr.pos
        if p is not None:
            marks = getattr(eng, "last_marks", {}) or {}
            out["position"] = {
                "symbol": p.symbol, "side": p.side, "mode": p.mode,
                "entry": p.entry_px, "qty": p.qty, "lots": p.lots,
                "sl_spot": p.sl_spot, "tp": p.tp_prem,
                "entry_ts": p.entry_ts, "mark": marks.get(p.symbol),
                "row_id": p.row_id}
        try:
            from app.db.sqlite import get_conn
            day0 = int(now.replace(hour=0, minute=0, second=0,
                                   microsecond=0).timestamp())
            rows = get_conn().execute(
                "SELECT paper_trade_id, symbol, side, trade_mode, group_id,"
                " entry_price, exit_price, exit_reason, qty, state,"
                " entry_time, exit_time, sl_price, tp_price FROM paper_trades"
                " WHERE strategy_name='ORB_V1' AND candle_ts >= ?"
                " ORDER BY entry_time DESC LIMIT 20", (day0,)).fetchall()
            out["trades"] = [dict(r) for r in rows]
        except Exception:
            pass
        return out
    except Exception as e:
        return dict(_EMPTY, ok=False, error=repr(e))


@router.post("/square_off")
def orb_square_off():
    try:
        from app.engine.orb.orb_runtime import get_orb_manager
        mgr = get_orb_manager()
        if mgr is None:
            return {"ok": False, "error": "runtime not up"}
        return {"ok": True, "closed": mgr.kill_all()}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
