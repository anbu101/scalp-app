from fastapi import APIRouter
from pathlib import Path
import json
from datetime import date

router = APIRouter(tags=["trade-history"])

TRADE_STATE_DIR = Path("app/trading/state")


@router.get("/trades/today")
def get_today_trades():
    today = date.today().isoformat()

    open_trades = []
    closed_trades = []

    for f in TRADE_STATE_DIR.glob("*.json"):
        raw = f.read_text().strip()
        if not raw or raw == "{}":
            continue

        data = json.loads(raw)

        trade_date = (
            data.get("entry_time") and
            date.fromtimestamp(data["entry_time"]).isoformat()
        )

        if trade_date != today:
            continue

        if data.get("is_closed"):
            closed_trades.append(data)
        else:
            open_trades.append(data)

    return {
        "open": open_trades,
        "closed": closed_trades,
    }
