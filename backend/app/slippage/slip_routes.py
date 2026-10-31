# backend/app/slippage/slip_routes.py — ── SLIP_VIEW_20261031 ── /api/slippage/*
# (admin gate in api_server.py)
from __future__ import annotations

from fastapi import APIRouter

from app.slippage import slip_report as SR

router = APIRouter(prefix="/api/slippage", tags=["slippage"])


@router.get("/summary")
def summary(days: int = 10):
    return {"ok": True, **SR.summary(max(1, min(int(days), 90)))}


@router.get("/fills")
def fills(days: int = 10):
    return {"ok": True, "fills": SR.fills(max(1, min(int(days), 90)))}


@router.post("/refresh")
def refresh():
    """Read today's fills from Kite now (otherwise 15:36 / 15:50)."""
    from app.slippage.slip_store import fetch_fills
    from app.recon.recon_core import trade_kite
    k = trade_kite()
    if k is None:
        return {"ok": False, "error": "no Kite session"}
    return {"ok": True, **fetch_fills(k)}
