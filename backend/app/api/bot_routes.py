from fastapi import APIRouter, Query
from pathlib import Path
from datetime import date
import re
from pathlib import Path
from datetime import date
import json
router = APIRouter(prefix="/bot", tags=["Bot"])

LOG_DIR = Path("logs")


@router.get("/summary")
def bot_summary(
    log_date: str = Query(default=None, description="YYYY-MM-DD")
):
    if not log_date:
        log_date = date.today().isoformat()

    log_file = LOG_DIR / f"{log_date}.log"

    if not log_file.exists():
        return {
            "date": log_date,
            "trades": [],
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "net_pnl": 0.0,
        }

    trades = []
    open_trades = {}

    for line in log_file.read_text().splitlines():
        # BUY line
        if " BUY @" in line:
            m = re.search(
                r"\[(CE|PE)\]\s+(\S+)\s+BUY @ ([\d.]+)",
                line
            )
            if not m:
                continue

            side, symbol, entry = m.groups()
            open_trades[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry": float(entry),
            }

        # EXIT line
        elif " EXIT-" in line:
            m = re.search(
                r"\[(CE|PE)\]\s+(\S+)\s+EXIT-(TP|SL) @ ([\d.]+)",
                line
            )
            if not m:
                continue

            side, symbol, reason, exit_price = m.groups()

            trade = open_trades.pop(symbol, None)
            if not trade:
                continue

            pnl = (float(exit_price) - trade["entry"]) * 75  # lot size

            trades.append({
                "symbol": symbol,
                "side": side,
                "entry": trade["entry"],
                "exit": float(exit_price),
                "result": reason,
                "pnl": round(pnl, 2),
            })

    total = len(trades)
    wins = len([t for t in trades if t["pnl"] > 0])
    losses = total - wins
    net = round(sum(t["pnl"] for t in trades), 2)

    return {
        "date": log_date,
        "trades": trades,
        "total_trades": total,
        "winning_trades": wins,
        "losing_trades": losses,
        "net_pnl": net,
    }

    


@router.get("/bot/summary")
def get_bot_summary():
    """
    Returns today's bot trade summary for UI.
    """
    summary_file = Path("logs") / f"{date.today().isoformat()}_summary.json"

    if not summary_file.exists():
        return {
            "date": date.today().isoformat(),
            "trades": [],
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "net_pnl": 0.0,
        }

    return json.loads(summary_file.read_text())
