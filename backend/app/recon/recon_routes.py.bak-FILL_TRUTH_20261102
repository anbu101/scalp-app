# backend/app/recon/recon_routes.py
#
# ── BROKER_RECON_20261029 ── /api/recon/* (admin gate in api_server.py).

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.recon import recon_core as RC

router = APIRouter(prefix="/api/recon", tags=["recon"])


def _rows(conn, sql, args=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args)]


@router.get("/status")
def status():
    conn = RC.connect()
    try:
        return {
            "ok": True, "fence": RC.FENCE, "poll_s": RC.POLL_S,
            "last_run": (_rows(conn, "SELECT * FROM recon_runs ORDER BY ts DESC LIMIT 1") or [None])[0],
            "open_issues": _rows(conn, "SELECT * FROM recon_issues WHERE resolved_at IS NULL ORDER BY confirmed_at"),
            "recent_resolved": _rows(conn, "SELECT * FROM recon_issues WHERE resolved_at IS NOT NULL "
                                           "ORDER BY resolved_at DESC LIMIT 20"),
            "ignores": _rows(conn, "SELECT * FROM recon_ignores WHERE removed_at IS NULL ORDER BY added_at"),
        }
    finally:
        conn.close()


class IgnoreReq(BaseModel):
    tradingsymbol: str
    qty: int                      # signed units, as Kite shows it (short = negative)
    note: Optional[str] = None


@router.post("/ignore")
def add_ignore(req: IgnoreReq):
    sym = (req.tradingsymbol or "").strip().upper()
    if not sym or not req.qty:
        raise HTTPException(400, "tradingsymbol and a non-zero signed qty are required")
    conn = RC.connect()
    try:
        cur = conn.execute("INSERT INTO recon_ignores (symbol, qty, note, added_at) VALUES (?,?,?,?)",
                           (sym, int(req.qty), (req.note or "")[:200], RC.now_ts()))
        conn.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        conn.close()


@router.delete("/ignore/{ignore_id}")
def remove_ignore(ignore_id: int):
    conn = RC.connect()
    try:
        n = conn.execute("UPDATE recon_ignores SET removed_at=?, removed_why='removed by user' "
                         "WHERE id=? AND removed_at IS NULL", (RC.now_ts(), int(ignore_id))).rowcount
        conn.commit()
        if not n:
            raise HTTPException(404, "no active ignore with that id")
        return {"ok": True}
    finally:
        conn.close()


@router.post("/run")
def run_now():
    """One poll now, ignoring the time window (still read-only)."""
    return RC.run_once(force=True)
