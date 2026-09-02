# backend/app/api/brk_v1_state_routes.py
#
# ── BRK_V1 STATE API ── feeds the dashboard panel (vet_state_routes pattern).
# Fence BRK_V1_LIVE_20260902. Isolated try/except; sane payload when the
# runtime never launched. Touches no other strategy.

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/api/brk", tags=["brk"])
IST = timezone(timedelta(minutes=330))


@router.get("/state")
def brk_state_route():
    try:
        from app.engine.brk.brk_runtime import get_brk_engine, get_brk_manager
        eng, gm = get_brk_engine(), get_brk_manager()
        out = {"running": eng is not None, "status": {}, "position": None,
               "sessions": [], "day_results": {}, "trades": []}
        if eng is not None and eng.core is not None:
            out["status"] = dict(eng.status)
            for sess in filter(None, (eng.core.s1, eng.core.s2)):
                out["sessions"].append({
                    "tag": sess.spec.tag,
                    "ce_sym": sess.ce_sym, "pe_sym": sess.pe_sym,
                    "sel_prints": sess.sel_prints,
                    "entered": sess.entered, "done": sess.done})
            out["frozen"] = eng.core.guard.frozen
        if gm is not None:
            if gm.pos is not None:
                p = gm.pos
                out["position"] = {"symbol": p.symbol, "side": p.side,
                                   "tag": p.tag, "entry": p.entry_px,
                                   "sl": p.sl_px, "tp": p.tp_px,
                                   "qty": p.qty, "mode": p.mode,
                                   "gtt_id": p.gtt_id}
            out["day_results"] = dict(gm.day_results)
        # today's rows from the generic table (LD3)
        try:
            from app.db.database import get_conn
            day0 = int(datetime.now(IST).replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp())
            conn = get_conn()
            rows = conn.execute(
                "SELECT paper_trade_id, symbol, side, trade_mode, group_id,"
                " entry_price, exit_price, exit_reason, qty, state,"
                " entry_time FROM paper_trades WHERE strategy_name='BRK_V1'"
                " AND candle_ts >= ? ORDER BY entry_time DESC LIMIT 20",
                (day0,)).fetchall()
            out["trades"] = [dict(r) for r in rows]
        except Exception:
            pass
        return out
    except Exception as e:
        return {"running": False, "error": str(e), "sessions": [],
                "position": None, "trades": []}


@router.post("/square_off")
def brk_square_off_route():
    """Manual flatten (panel two-tap). Uses the manager's full exit path —
    cancel-GTT-verified before any sell (LD8)."""
    try:
        from app.engine.brk.brk_runtime import get_brk_manager
        gm = get_brk_manager()
        if gm is None:
            return {"ok": False, "error": "runtime not launched"}
        n = 0
        if gm.pos is not None:
            n = 1 if gm.close_trade(reason="MANUAL") else 0
        return {"ok": True, "closed": n, "ts": int(time.time())}
    except Exception as e:
        return {"ok": False, "error": str(e)}