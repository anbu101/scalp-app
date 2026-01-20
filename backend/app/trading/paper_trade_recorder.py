import uuid
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config

from app.db.paper_trades_repo import (
    insert_paper_trade,
    close_paper_trade,
    has_open_paper_trade,
)


class PaperTradeRecorder:
    """
    📄 PAPER TRADING ENGINE (NON-INTRUSIVE)

    - Mirrors LIVE signals
    - Respects trade_on
    - ONE OPEN trade per (strategy + symbol)
    - No broker interaction
    """

    STRATEGY_NAME = "1M_SCALP"

    @staticmethod
    def record_entry(
        *,
        symbol: str,
        token: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        candle_ts: int,
    ):
        cfg = load_strategy_config()

        # 🔒 Respect TRADE_ON
        if not cfg.get("trade_on", False):
            return None

        # 🔒 HARD PAPER LOCK (DB AUTHORITATIVE)
        if has_open_paper_trade(
            strategy_name=PaperTradeRecorder.STRATEGY_NAME,
            symbol=symbol,
        ):
            write_audit_log(
                f"[PAPER][SKIP] OPEN_TRADE_EXISTS symbol={symbol}"
            )
            return None

        lots = cfg["quantity"]["lots"]
        lot_size = cfg["quantity"]["lot_size"]
        qty = lots * lot_size

        rr = cfg.get("risk_reward_ratio", 1.0)
        side = cfg.get("trade_side_mode", "BOTH")

        paper_trade_id = str(uuid.uuid4())

        insert_paper_trade(
            paper_trade_id=paper_trade_id,
            strategy_name=PaperTradeRecorder.STRATEGY_NAME,
            trade_mode="PAPER",
            symbol=symbol,
            token=token,
            side=side,
            entry_price=entry_price,
            candle_ts=candle_ts,
            sl_price=sl_price,
            tp_price=tp_price,
            rr=rr,
            lots=lots,
            lot_size=lot_size,
            qty=qty,
        )

        write_audit_log(
            f"[PAPER][ENTRY] {symbol} entry={entry_price} sl={sl_price} tp={tp_price}"
        )

        return paper_trade_id

    @staticmethod
    def try_exit(
        *,
        paper_trade_id: str,
        symbol: str,
        sl_price: float,
        tp_price: float,
    ):
        ltp = LTPStore.get(symbol)
        if ltp is None:
            return

        if ltp <= sl_price:
            close_paper_trade(
                paper_trade_id=paper_trade_id,
                exit_price=ltp,
                exit_reason="SL",
            )
            write_audit_log(f"[PAPER][EXIT_SL] {symbol} price={ltp}")

        elif ltp >= tp_price:
            close_paper_trade(
                paper_trade_id=paper_trade_id,
                exit_price=ltp,
                exit_reason="TP",
            )
            write_audit_log(f"[PAPER][EXIT_TP] {symbol} price={ltp}")
