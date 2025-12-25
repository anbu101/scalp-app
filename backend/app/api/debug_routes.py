from fastapi import APIRouter, Query
from typing import Optional
from app.db.sqlite import get_conn

router = APIRouter(prefix="/debug", tags=["debug"])


# =========================
# market_timeline
# =========================

@router.get("/market_timeline/count")
def market_timeline_count():
    conn = get_conn()
    cur = conn.execute("SELECT COUNT(*) AS cnt FROM market_timeline")
    return cur.fetchone()["cnt"]


@router.get("/market_timeline/latest")
def market_timeline_latest(
    limit: int = Query(10, ge=1, le=500),
    symbol: Optional[str] = None,
):
    conn = get_conn()

    if symbol:
        cur = conn.execute(
            """
            SELECT *
            FROM market_timeline
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol, limit),
        )
    else:
        cur = conn.execute(
            """
            SELECT *
            FROM market_timeline
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

    rows = cur.fetchall()
    if not rows:
        return {"columns": [], "rows": []}

    columns = list(rows[0].keys())
    data = [list(r) for r in rows]

    return {
        "columns": columns,
        "rows": data,
    }


# =========================
# trades
# =========================

@router.get("/trades/count")
def trades_count():
    conn = get_conn()
    cur = conn.execute("SELECT COUNT(*) AS cnt FROM trades")
    return cur.fetchone()["cnt"]


@router.get("/trades/latest")
def trades_latest(
    limit: int = Query(20, ge=1, le=200),
    symbol: Optional[str] = None,
):
    conn = get_conn()

    if symbol:
        cur = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE symbol = ?
            ORDER BY entry_time DESC
            LIMIT ?
            """,
            (symbol, limit),
        )
    else:
        cur = conn.execute(
            """
            SELECT *
            FROM trades
            ORDER BY entry_time DESC
            LIMIT ?
            """,
            (limit,),
        )

    rows = cur.fetchall()
    if not rows:
        return {"columns": [], "rows": []}

    columns = list(rows[0].keys())
    data = [list(r) for r in rows]

    return {
        "columns": columns,
        "rows": data,
    }
