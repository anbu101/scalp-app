import uuid
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.db.paper_trades_repo import (
    insert_paper_trade,
    close_paper_trade,
    has_open_paper_trade_by_side,   # FIX: side-based guard, not symbol-based
    get_paper_trade_by_id,
)

# 🔔 TELEGRAM NOTIFICATIONS
from app.api.telegram_api import (
    notify_tp_exit,
    notify_sl_exit,
    notify_manual_exit,
)


class PaperTradeRecorder:
    """
    📄 PAPER TRADING ENGINE (STRATEGY-SCOPED)

    - Mirrors LIVE signals
    - Respects GLOBAL trade_on
    - One open trade per (strategy + side).  ← FIX: was (strategy + symbol)
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

        # --------------------------------------------------
        # LOT RESOLUTION  (needed before side guard)
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

        # --------------------------------------------------
        # FIX: Guard by SIDE (CE/PE), not exact symbol.
        #
        # Old guard used symbol match — on restart the option
        # selector could pick a different strike (LTP missing),
        # so has_open_paper_trade(symbol=...) returned False even
        # though a PE trade was already open at a different strike.
        #
        # New guard: if ANY open trade exists for this strategy
        # with the same side suffix (CE/PE), block the entry.
        # --------------------------------------------------

        if side_detected in ("CE", "PE") and has_open_paper_trade_by_side(
            strategy_name=strategy_id,
            side=side_detected,
        ):
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][SKIP] "
                f"OPEN_{side_detected}_TRADE_EXISTS — blocking new {side_detected} entry"
            )
            return None

        # --------------------------------------------------
        # INSERT
        # --------------------------------------------------

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

        # 🔔 TELEGRAM ENTRY NOTIFICATION
        try:
            from app.api.telegram_api import notify_trade_entry

            notify_trade_entry({
                "strategy_id": strategy_id,
                "mode": "paper",
                "symbol": symbol,
                "side": side_detected,
                "entry_price": entry_price,
                "quantity": qty,
                "sl": sl_price,
                "tp": tp_price,
            })

            write_audit_log("[TELEGRAM] Paper entry notification sent")

        except Exception as e:
            write_audit_log(f"[TELEGRAM][ENTRY_NOTIFY_ERROR] {e}")

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

        trade = get_paper_trade_by_id(paper_trade_id)
        if not trade:
            return

        entry_price = trade["entry_price"]
        qty = trade["qty"]

        # SL
        if sl_price and sl_price > 0 and ltp <= sl_price:

            pnl = (ltp - entry_price) * qty

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

            try:
                notify_sl_exit({
                    "strategy_id": strategy_id,
                    "mode": "paper",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": ltp,
                    "pnl": pnl,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][SL_NOTIFY_ERROR] {e}")

            return

        # TP
        if tp_price and tp_price > 0 and ltp >= tp_price:

            pnl = (ltp - entry_price) * qty

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

            try:
                notify_tp_exit({
                    "strategy_id": strategy_id,
                    "mode": "paper",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": ltp,
                    "pnl": pnl,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][TP_NOTIFY_ERROR] {e}")

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

        trade = get_paper_trade_by_id(paper_trade_id)
        if not trade:
            return

        entry_price = trade["entry_price"]
        qty = trade["qty"]

        pnl = (ltp - entry_price) * qty

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

        try:
            notify_manual_exit({
                "strategy_id": strategy_id,
                "mode": "paper",
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": ltp,
                "exit_reason": reason,
                "pnl": pnl,
            })
        except Exception as e:
            write_audit_log(f"[TELEGRAM][MANUAL_EXIT_NOTIFY_ERROR] {e}")