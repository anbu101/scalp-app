from fastapi import APIRouter
from app.db.sqlite import get_conn

router = APIRouter(tags=["health"])

@router.get("/health")
def health():
    try:
        conn = get_conn()
        conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok",
        "db": "ok" if db_ok else "error",
    }
