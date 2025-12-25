from pathlib import Path
from datetime import datetime
import re

from event_bus.audit_logger import write_audit_log


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def parse_trades_from_log(lines):
    """
    Parses BUY / EXIT lines and returns list of PnLs.
    """
    trades = []
    open_trade = None

    buy_pattern = re.compile(
        r"\[(CE|PE)\]\s+(?P<symbol>\S+)\s+BUY\s+@\s+(?P<price>[\d.]+).*QTY=(?P<qty>\d+)"
    )
    exit_pattern = re.compile(
        r"\[(CE|PE)\]\s+(?P<symbol>\S+)\s+EXIT-(TP|SL)\s+@\s+(?P<price>[\d.]+).*QTY=(?P<qty>\d+)"
    )

    for line in lines:
        buy_match = buy_pattern.search(line)
        if buy_match:
            open_trade = {
                "price": float(buy_match.group("price")),
                "qty": int(buy_match.group("qty")),
            }
            continue

        exit_match = exit_pattern.search(line)
        if exit_match and open_trade:
            exit_price = float(exit_match.group("price"))
            pnl = (exit_price - open_trade["price"]) * open_trade["qty"]
            trades.append(pnl)
            open_trade = None

    return trades


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{today}.log"

    if not log_file.exists():
        print("No log file for today")
        return

    lines = log_file.read_text().splitlines()
    pnls = parse_trades_from_log(lines)

    total = len(pnls)
    wins = len([p for p in pnls if p > 0])
    losses = len([p for p in pnls if p <= 0])
    net_pnl = sum(pnls)

    write_audit_log("")
    write_audit_log("=============== DAILY SUMMARY ===============")
    write_audit_log(f"Total Trades : {total}")
    write_audit_log(f"Winning Trades : {wins}")
    write_audit_log(f"Losing Trades : {losses}")
    write_audit_log(f"Net PnL : {net_pnl:.2f}")
    write_audit_log("============================================")

    print("Daily summary appended")


if __name__ == "__main__":
    main()
