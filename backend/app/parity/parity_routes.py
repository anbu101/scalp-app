# backend/app/parity/parity_routes.py — ── PARITY_V1_20261030 ── /api/parity/*
# (admin gate in api_server.py).
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.parity import parity_core as PC

router = APIRouter(prefix="/api/parity", tags=["parity"])


@router.get("/days")
def days(limit: int = 20):
    from app.recorder import rec_db as RD
    out = {"ok": True, "strategies": [{"id": k, "name": v} for k, v in PC.SUPPORTED.items()], "days": []}
    if not RD.db_path().exists():
        return out
    c = RD.connect()
    try:
        out["days"] = [{"day": r[0], "quality": r[1], "configs": r[2]} for r in c.execute(
            "SELECT d.day, d.quality, (SELECT COUNT(*) FROM session_config s WHERE s.day=d.day) "
            "FROM day_status d ORDER BY d.day DESC LIMIT ?", (max(1, min(int(limit), 120)),))]
    finally:
        c.close()
    return out


class RunReq(BaseModel):
    day: str
    strategy_id: str
    book: Optional[str] = "PAPER"


@router.post("/run")
def run(req: RunReq):
    try:
        d = date.fromisoformat(req.day)
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    book = (req.book or "PAPER").upper()
    if book not in ("PAPER", "LIVE"):
        raise HTTPException(400, "book must be PAPER or LIVE")
    res = PC.run_day(req.strategy_id, d, book)
    try:                                                   # ── PARITY_DAILY_20261030 ── kept for history
        if res.get("ok") or req.strategy_id in PC.SUPPORTED:
            res.setdefault("strategy_id", req.strategy_id)
            res.setdefault("day", d.isoformat())
            res.setdefault("book", book)
            PC.store(res, "manual")
    except Exception:
        pass
    return res


@router.get("/history")
def history(days: int = 10):
    return {"ok": True, "days": PC.history(max(1, min(int(days), 60))),
            "strategies": [{"id": k, "name": v} for k, v in PC.SUPPORTED.items()]}


@router.get("/result")
def result(day: str, strategy_id: str, book: str = "PAPER"):
    r = PC.load(day, strategy_id, book.upper())
    if r is None:
        raise HTTPException(404, "no stored result")
    return r
