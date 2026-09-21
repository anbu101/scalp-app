# backend/app/api/vet_state_routes.py
#
# ── VET_V1 STATE API ── read-only, feeds the VET dashboard panel + curl-level
# inspection (tma2_state_routes pattern). Touches no other strategy.

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/vet", tags=["vet"])


def _repo():
    from app.engine.vet.vet_common import VetRepo
    return VetRepo()


@router.get("/trades")
def vet_trades_route(limit: int = Query(200, le=1000),
                     status: str = Query("all")):
    repo = _repo()
    try:
        import sqlite3
        with sqlite3.connect(repo.db_path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            q = "SELECT * FROM vet_trades"
            if status in ("OPEN", "CLOSED", "STALE"):
                q += f" WHERE status='{status}'"
            q += " ORDER BY entry_ts DESC, leg_role ASC LIMIT ?"
            rows = [dict(r) for r in c.execute(q, (limit,))]
        closed = [r for r in rows if r["status"] == "CLOSED"]
        mains = [r for r in closed if r["leg_role"] == "MAIN"]
        return {"trades": rows,
                "summary": {
                    "open": sum(1 for r in rows if r["status"] == "OPEN"),
                    "closed": len(closed),
                    # positions, not legs — a wing is not an independent trade
                    "closed_positions": len(mains),
                    "net_pnl": round(sum(r["net_pnl"] or r["pnl"] or 0
                                         for r in closed), 2),
                }}
    except Exception as e:
        return {"error": str(e), "trades": []}


@router.get("/status")
def vet_status():
    """Liveness for the panel: manager position, signal-engine health
    (frozen flag, warmup depth, last bar), mode and leg action. Everything a
    'why is it not trading?' question needs, in one call."""
    out = {"manager": None, "signal_engine": None, "open_legs": 0}
    try:
        from app.engine.vet.vet_selection_loop import get_engine, get_manager
        m = get_manager()
        e = get_engine()
        if m is not None:
            out["manager"] = m.state()
        if e is not None:
            out["signal_engine"] = e.status()
    except Exception as exc:
        out["error"] = str(exc)
    try:
        out["open_legs"] = len(_repo().open_legs())
    except Exception:
        pass
    return out