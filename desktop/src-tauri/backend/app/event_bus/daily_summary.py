from dataclasses import dataclass
from typing import List
from event_bus.audit_logger import write_audit_log


@dataclass
class TradeResult:
    pnl: float


class DailySummary:
    def __init__(self):
        self.trades: List[TradeResult] = []

    def record_trade(self, pnl: float):
        self.trades.append(TradeResult(pnl=pnl))

    def write_summary(self):
        total = len(self.trades)
        wins = len([t for t in self.trades if t.pnl > 0])
        losses = len([t for t in self.trades if t.pnl <= 0])
        net_pnl = sum(t.pnl for t in self.trades)

        write_audit_log("")
        write_audit_log("=============== DAILY SUMMARY ===============")
        write_audit_log(f"Total Trades : {total}")
        write_audit_log(f"Winning Trades : {wins}")
        write_audit_log(f"Losing Trades : {losses}")
        write_audit_log(f"Net PnL : {net_pnl:.2f}")
        write_audit_log("============================================")
