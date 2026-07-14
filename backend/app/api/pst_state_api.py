# backend/app/api/pst_state_api.py
#
# ── PST STATE API ── (Phase 1, Delivery 3 — read-only, paper)
# Feeds the PST Sell / PST Hedge dashboard panels (Delivery 3B) and any
# curl-level inspection during the paper parity period.

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/pst", tags=["pst"])

_TABLES = {"PST_SELL": "pst_sell_trades", "PST_HEDGE": "pst_hedge_trades"}


def _repo():
    from app.engine.pst.pst_common import PSTRepo, canonical_db_path
    return PSTRepo(canonical_db_path())


@router.get("/trades")
def pst_trades(strategy_id: str = Query(...), limit: int = Query(200, le=1000),
               status: str = Query("all")):
    table = _TABLES.get(strategy_id.upper())
    if table is None:
        return {"error": f"unknown strategy {strategy_id}", "trades": []}
    repo = _repo()
    try:
        import sqlite3
        with sqlite3.connect(repo.db_path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            q = f"SELECT * FROM {table}"
            if status in ("OPEN", "CLOSED"):
                q += f" WHERE status='{status}'"
            q += " ORDER BY entry_ts DESC, leg_id LIMIT ?"
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
def pst_status():
    """Coarse liveness: open legs per table + today's capture file size."""
    import os
    from datetime import datetime
    repo = _repo()
    out = {}
    for sid, table in _TABLES.items():
        out[sid] = {"open_legs": len(repo.open_legs(table))}
    try:
        from app.utils.app_paths import APP_HOME
        cap = APP_HOME / "pst_capture" / f"{datetime.now().date().isoformat()}.jsonl"
        out["capture_bytes_today"] = os.path.getsize(cap) if cap.exists() else 0
    except Exception:
        out["capture_bytes_today"] = None
    return out