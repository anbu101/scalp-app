# backend/app/api/live_sizer_routes.py
#
# ── LIVE_SIZER_20261024 ── /api/live-sizer/*  (suggest-only; see risk/live_sizer.py)
#   GET    /api/live-sizer                 today's suggestion + breakdown + history
#   PUT    /api/live-sizer/config          save the plan (validated; exact reason on 400)
#   POST   /api/live-sizer/adjustments     {day, kind: DEPOSIT|WITHDRAW|TAX, amount, note}
#   DELETE /api/live-sizer/adjustments/{id}
#   POST   /api/live-sizer/snapshot        store today's suggestion now (manual)
#   GET    /api/live-sizer/broker-funds    Kite equity margins — a manual cross-check only;
#                                          never polled, never used in the rule
# No route writes a strategy config or places an order.

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.risk import live_sizer as LS

router = APIRouter(tags=["live-sizer"])


@router.get("/api/live-sizer")
def get_live_sizer():
    res = LS.compute()
    res["history"] = LS.history()
    res["supported"] = LS.SUPPORTED
    return res


@router.put("/api/live-sizer/config")
def put_config(payload: dict = Body(...)):
    try:
        return {"ok": True, "config": LS.save_config(payload)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/live-sizer/adjustments")
def post_adjustment(payload: dict = Body(...)):
    try:
        return {"ok": True, "adjustment": LS.add_adjustment(payload.get("day"), payload.get("kind"),
                                                           payload.get("amount"), payload.get("note") or "")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/live-sizer/adjustments/{adj_id}")
def delete_adjustment(adj_id: int):
    if not LS.delete_adjustment(adj_id):
        raise HTTPException(status_code=404, detail="no such adjustment")
    return {"ok": True}


@router.post("/api/live-sizer/snapshot")
def post_snapshot():
    return LS.snapshot()


@router.get("/api/live-sizer/broker-funds")
def broker_funds():
    """Kite's view of the account, for comparison with the sizer's equity.
    Kite's numbers include premium received on open shorts and blocked
    margin, so they differ from 'start + realised' while positions are open."""
    try:
        from app.api.zerodha_routes import zerodha_manager
        kite = zerodha_manager.get_kite()
        if kite is None:
            return {"ok": False, "reason": "Kite not logged in"}
        m = kite.margins("equity") or {}
        av, ut = m.get("available") or {}, m.get("utilised") or {}
        return {"ok": True, "net": m.get("net"), "opening_balance": av.get("opening_balance"),
                "live_balance": av.get("live_balance"), "collateral": av.get("collateral"),
                "m2m_realised": ut.get("m2m_realised"), "m2m_unrealised": ut.get("m2m_unrealised"),
                "utilised": ut.get("debits")}
    except Exception as e:
        return {"ok": False, "reason": f"funds call failed: {e!r}"}
