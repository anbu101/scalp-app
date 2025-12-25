from fastapi import APIRouter, HTTPException

from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.config.zerodha_credentials import (
    API_KEY,
    API_SECRET,
)

router = APIRouter(prefix="/broker", tags=["Broker"])


@router.get("/positions")
def get_positions():
    try:
        executor = ZerodhaOrderExecutor(
            api_key=API_KEY,
            api_secret=API_SECRET,
        )

        # If token is missing, kite will throw — we catch and return cleanly
        positions = executor.kite.positions()

        return {
            "status": "ok",
            "data": positions,
        }

    except Exception as e:
        msg = str(e)

        if "Token" in msg or "login" in msg.lower():
            raise HTTPException(
                status_code=401,
                detail="Zerodha not logged in. Please login via UI.",
            )

        raise HTTPException(status_code=500, detail=msg)
