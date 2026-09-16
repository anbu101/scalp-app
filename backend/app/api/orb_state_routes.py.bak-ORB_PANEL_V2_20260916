# backend/app/api/orb_state_routes.py
#
# ── ORB_V1 STATE ROUTES ── panel GET + square_off POST. Fence: ORB_LIVE_20260903
# Isolated try/except everywhere; sane payload when the runtime never launched.

from __future__ import annotations
from fastapi import APIRouter

router = APIRouter(prefix="/api/orb_v1", tags=["ORB_V1"])


@router.get("/state")
def orb_state():
    try:
        from app.engine.orb.orb_runtime import get_orb_manager
        mgr = get_orb_manager()
        if mgr is None:
            return {"ok": True, "running": False, "strategy": "ORB_V1",
                    "mode": "OFF", "position": None, "levels": None,
                    "day": {}, "frozen": False}
        return {"ok": True, "running": True, **mgr.state()}
    except Exception as e:
        return {"ok": False, "error": repr(e), "running": False,
                "strategy": "ORB_V1", "mode": "OFF", "position": None,
                "levels": None, "day": {}, "frozen": False}


@router.post("/square_off")
def orb_square_off():
    try:
        from app.engine.orb.orb_runtime import get_orb_manager
        mgr = get_orb_manager()
        if mgr is None:
            return {"ok": False, "error": "runtime not up"}
        n = mgr.kill_all()
        return {"ok": True, "closed": n}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
