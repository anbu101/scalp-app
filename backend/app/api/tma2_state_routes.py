# backend/app/api/tma2_state_routes.py
#
# ── TMA_V2 STATE API ── read-only, feeds the TMA2 dashboard panel + curl-level
# inspection (pst_state_api pattern). Touches no other strategy.

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/tma2", tags=["tma2"])


def _repo():
    from app.engine.tma2.tma2_common import TMA2Repo
    return TMA2Repo()


@router.get("/trades")
def tma2_trades_route(limit: int = Query(200, le=1000), status: str = Query("all")):
    repo = _repo()
    try:
        import sqlite3
        with sqlite3.connect(repo.db_path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            q = "SELECT * FROM tma2_trades"
            if status in ("OPEN", "CLOSED", "STALE"):
                q += f" WHERE status='{status}'"
            q += " ORDER BY entry_ts DESC, direction DESC LIMIT ?"
            rows = [dict(r) for r in c.execute(q, (limit,))]
        closed = [r for r in rows if r["status"] == "CLOSED"]
        return {"trades": rows,
                "summary": {
                    "open": sum(1 for r in rows if r["status"] == "OPEN"),
                    "closed": len(closed),
                    "net_pnl": round(sum(r["net_pnl"] or 0 for r in closed), 2),
                }}
    except Exception as e:
        return {"error": str(e), "trades": []}


@router.get("/status")
def tma2_status():
    """Liveness + the current group for the panel: open legs, diag funnel,
    signal-engine health (frozen flag, warmup depth), position snapshot."""
    out = {"open_legs": 0, "group": None, "diag": {}, "signal_engine": {}}
    try:
        repo = _repo()
        out["open_legs"] = len(repo.open_legs())
    except Exception:
        pass
    try:
        from app.engine.tma2.tma2_selection_loop import (get_manager,
                                                       get_signal_engine)
        m = get_manager()
        if m is not None:
            out["diag"] = dict(getattr(m, "diag", {}))
            out["disabled"] = bool(getattr(m, "disabled", False))
            g = getattr(m, "group", None)
            if g:
                out["group"] = {
                    "group_id": g["group_id"], "mode": g["mode"],
                    "trade_mode": g["trade_mode"],
                    "trend_side": g["trend_side"], "expiry": g["expiry"],
                    "sell": {k: g["sell"].get(k) for k in
                             ("symbol", "qty", "entry", "sl", "tp", "gtt_id")},
                    "hedge": {k: g["hedge"].get(k) for k in
                              ("symbol", "qty", "entry", "fallback")},
                    "last_close": g["pos"].get("last_close"),
                    "last_ts": g["pos"].get("last_ts"),
                }
            p = getattr(m, "pending", None)
            if p:
                out["pending"] = {"sell": p.get("sell_symbol"),
                                  "hedge": p.get("hedge_symbol"),
                                  "fill_ts": p.get("fill_ts")}
        se = get_signal_engine()
        if se is not None:
            out["signal_engine"] = {"frozen": bool(getattr(se, "frozen", False)),
                                    **dict(getattr(se, "diag", {}))}
    except Exception:
        pass
    return out