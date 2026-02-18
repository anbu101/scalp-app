import uuid
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.db.paper_trades_repo import (
    insert_paper_trade,
    close_paper_trade,
    has_open_paper_trade,
)


class PaperTradeRecorder:
    """
    📄 PAPER TRADING ENGINE (STRATEGY-SCOPED)

    - Mirrors LIVE signals
    - Respects GLOBAL trade_on
    - One open trade per (strategy + symbol)
    - No broker interaction
    - Supports SL / TP / SuperTrend / EOD exits
    """

    # ==================================================
    # ENTRY
    # ==================================================

    @staticmethod
    def record_entry(
        *,
        strategy_id: str,
        symbol: str,
        token: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        candle_ts: int,
    ):

        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][ENTRY_ATTEMPT] "
            f"symbol={symbol} entry={entry_price}"
        )

        cfg = load_strategy_config(strategy_id)

        if not load_global_config().get("trade_on", False):
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][BLOCKED] "
                f"GLOBAL trade_on=FALSE"
            )
            return None

        if has_open_paper_trade(
            strategy_name=strategy_id,
            symbol=symbol,
        ):
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][SKIP] "
                f"OPEN_TRADE_EXISTS symbol={symbol}"
            )
            return None

        # --------------------------------------------------
        # LOT RESOLUTION
        # --------------------------------------------------

        if strategy_id == "BB_V1":

            if "CE" in symbol:
                lots = cfg.get("ce_lots", 1)
                side_detected = "CE"
            elif "PE" in symbol:
                lots = cfg.get("pe_lots", 1)
                side_detected = "PE"
            else:
                lots = 1
                side_detected = "UNKNOWN"

            lot_size = cfg.get("quantity", {}).get("lot_size", 65)

        else:
            lots = cfg["quantity"]["lots"]
            lot_size = cfg["quantity"]["lot_size"]
            side_detected = cfg.get("trade_side_mode", "BOTH")

        qty = lots * lot_size

        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][LOT_RESOLVE] "
            f"side={side_detected} lots={lots} "
            f"lot_size={lot_size} qty={qty}"
        )

        rr = cfg.get("risk_reward_ratio", 1.0)
        side = cfg.get("trade_side_mode", "BOTH")

        paper_trade_id = str(uuid.uuid4())

        insert_paper_trade(
            paper_trade_id=paper_trade_id,
            strategy_name=strategy_id,
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
            f"[STRATEGY={strategy_id}][PAPER][ENTRY_CONFIRMED] "
            f"trade_id={paper_trade_id} symbol={symbol} "
            f"entry={entry_price} sl={sl_price} tp={tp_price} qty={qty}"
        )

        return paper_trade_id

    # ==================================================
    # EXIT (SL / TP AUTO)
    # ==================================================

    @staticmethod
    def try_exit(
        *,
        paper_trade_id: str,
        strategy_id: str,
        symbol: str,
        sl_price: float,
        tp_price: float,
    ):

        ltp = LTPStore.get(symbol)

        if ltp is None:
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][EXIT_SKIP] "
                f"LTP_MISSING symbol={symbol}"
            )
            return

        # SL
        if sl_price and sl_price > 0 and ltp <= sl_price:
            close_paper_trade(
                paper_trade_id=paper_trade_id,
                exit_price=ltp,
                exit_reason="SL",
            )

            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][EXIT_SL] "
                f"trade_id={paper_trade_id} symbol={symbol} "
                f"ltp={ltp} sl={sl_price}"
            )
            return

        # TP
        if tp_price and tp_price > 0 and ltp >= tp_price:
            close_paper_trade(
                paper_trade_id=paper_trade_id,
                exit_price=ltp,
                exit_reason="TP",
            )

            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][EXIT_TP] "
                f"trade_id={paper_trade_id} symbol={symbol} "
                f"ltp={ltp} tp={tp_price}"
            )
            return

    # ==================================================
    # FORCE EXIT (SuperTrend / Manual / EOD)
    # ==================================================

    @staticmethod
    def force_exit(
        *,
        paper_trade_id: str,
        strategy_id: str,
        symbol: str,
        reason: str,
    ):

        ltp = LTPStore.get(symbol)

        if ltp is None:
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][FORCE_EXIT_SKIP] "
                f"LTP_MISSING symbol={symbol}"
            )
            return

        close_paper_trade(
            paper_trade_id=paper_trade_id,
            exit_price=ltp,
            exit_reason=reason,
        )

        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][FORCE_EXIT] "
            f"trade_id={paper_trade_id} symbol={symbol} "
            f"reason={reason} exit_price={ltp}"
        )
