from typing import Dict
from app.db.sqlite import get_conn


def get_today_paper_summary() -> Dict:
    """
    Returns structured analytics for today's CLOSED paper trades.
    Grouped by strategy.
    """

    conn = get_conn()

    rows = conn.execute(
        """
        SELECT strategy_id, pnl
        FROM paper_trades
        WHERE state = 'CLOSED'
          AND date(entry_time, 'unixepoch', 'localtime') =
              date('now', 'localtime')
        """
    ).fetchall()

    summary = {}

    for strategy_id, pnl in rows:

        if strategy_id not in summary:
            summary[strategy_id] = {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0,
                "best": None,
                "worst": None,
                "win_sum": 0,
                "loss_sum": 0,
            }

        s = summary[strategy_id]

        s["total"] += 1
        s["total_pnl"] += pnl

        if pnl >= 0:
            s["wins"] += 1
            s["win_sum"] += pnl
        else:
            s["losses"] += 1
            s["loss_sum"] += pnl

        s["best"] = pnl if s["best"] is None else max(s["best"], pnl)
        s["worst"] = pnl if s["worst"] is None else min(s["worst"], pnl)

    # Final calculations
    for strategy_id, s in summary.items():

        total = s["total"]

        s["win_rate"] = round((s["wins"] / total) * 100, 1) if total else 0

        s["avg_win"] = (
            round(s["win_sum"] / s["wins"], 2)
            if s["wins"] > 0 else 0
        )

        s["avg_loss"] = (
            round(s["loss_sum"] / s["losses"], 2)
            if s["losses"] > 0 else 0
        )

    return summary
