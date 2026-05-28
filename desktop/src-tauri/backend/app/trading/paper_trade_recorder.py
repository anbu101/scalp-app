# backend/app/trading/paper_trade_recorder.py

import uuid
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.db.paper_trades_repo import (
    insert_paper_trade,
    close_paper_trade,
    has_open_paper_trade_by_side,
    get_paper_trade_by_id,
)

from app.api.telegram_api import (
    notify_tp_exit,
    notify_sl_exit,
    notify_manual_exit,
)


class PaperTradeRecorder:
    """
    📄 PAPER TRADING ENGINE (STRATEGY-SCOPED)

    Supports both LONG (BB/HA) and SHORT (SCALP_V1) trades.
    P&L direction is determined by trade_direction:
      LONG  → pnl = (exit - entry) × qty
      SHORT → pnl = (entry - exit) × qty

    SL/TP exit conditions also flip for SHORT:
      SHORT SL: ltp >= sl_price   (premium rises above stop)
      SHORT TP: ltp <= tp_price   (premium falls to target)
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
        trade_direction: str = "LONG",   # "LONG" | "SHORT"
    ):
        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][ENTRY_ATTEMPT] "
            f"symbol={symbol} entry={entry_price} dir={trade_direction}"
        )

        cfg = load_strategy_config(strategy_id)

        if not load_global_config().get("trade_on", False):
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][BLOCKED] GLOBAL trade_on=FALSE"
            )
            return None

        # --------------------------------------------------
        # LOT RESOLUTION
        # --------------------------------------------------

        if strategy_id == "BB_V1":
            if "CE" in symbol:
                lots          = cfg.get("ce_lots", cfg.get("lots", 1))
                side_detected = "CE"
            elif "PE" in symbol:
                lots          = cfg.get("pe_lots", cfg.get("lots", 1))
                side_detected = "PE"
            else:
                lots          = cfg.get("lots", 1)
                side_detected = "UNKNOWN"
            lot_size = 30  # BANKNIFTY

        elif strategy_id == "BB_V2":
            if "CE" in symbol:
                lots          = cfg.get("ce_lots", cfg.get("lots", 1))
                side_detected = "CE"
            elif "PE" in symbol:
                lots          = cfg.get("pe_lots", cfg.get("lots", 1))
                side_detected = "PE"
            else:
                lots          = cfg.get("lots", 1)
                side_detected = "UNKNOWN"
            lot_size = 30  # BANKNIFTY

        else:
            # SCALP_V1, HA_V1, and any future strategies
            quantity      = cfg.get("quantity", {})
            lots          = quantity.get("lots", 1)
            lot_size      = quantity.get("lot_size", 65)
            side_detected = cfg.get("trade_side_mode", "BOTH")

        qty = lots * lot_size

        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][LOT_RESOLVE] "
            f"side={side_detected} lots={lots} lot_size={lot_size} qty={qty}"
        )

        # --------------------------------------------------
        # GUARD: one open trade per (strategy + side)
        # --------------------------------------------------
        if side_detected in ("CE", "PE") and has_open_paper_trade_by_side(
            strategy_name=strategy_id,
            side=side_detected,
        ):
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][SKIP] "
                f"OPEN_{side_detected}_TRADE_EXISTS"
            )
            return None

        # --------------------------------------------------
        # INSERT
        # --------------------------------------------------
        rr             = cfg.get("risk_reward_ratio", 1.0)
        side           = cfg.get("trade_side_mode", "BOTH")
        paper_trade_id = str(uuid.uuid4())

        insert_paper_trade(
            paper_trade_id=paper_trade_id,
            strategy_name=strategy_id,
            trade_mode="PAPER",
            symbol=symbol,
            token=token,
            side=side_detected,
            entry_price=entry_price,
            candle_ts=candle_ts,
            sl_price=sl_price,
            tp_price=tp_price,
            rr=rr,
            lots=lots,
            lot_size=lot_size,
            qty=qty,
            trade_direction=trade_direction,
        )

        # Telegram entry notification
        try:
            from app.api.telegram_api import notify_trade_entry
            notify_trade_entry({
                "strategy_id":    strategy_id,
                "mode":           "paper",
                "symbol":         symbol,
                "side":           side_detected,
                "entry_price":    entry_price,
                "quantity":       qty,
                "sl":             sl_price,
                "tp":             tp_price,
                "trade_direction": trade_direction,
            })
            write_audit_log("[TELEGRAM] Paper entry notification sent")
        except Exception as e:
            write_audit_log(f"[TELEGRAM][ENTRY_NOTIFY_ERROR] {e}")

        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][ENTRY_CONFIRMED] "
            f"trade_id={paper_trade_id} symbol={symbol} dir={trade_direction} "
            f"entry={entry_price} sl={sl_price} tp={tp_price} qty={qty}"
        )

        return paper_trade_id

    # ==================================================
    # EXIT (SL / TP AUTO — called from tick engine)
    # Direction-aware: LONG and SHORT have inverted
    # SL/TP trigger conditions and P&L sign.
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
                f"[STRATEGY={strategy_id}][PAPER][EXIT_SKIP] LTP_MISSING symbol={symbol}"
            )
            return

        trade = get_paper_trade_by_id(paper_trade_id)
        if not trade:
            return

        entry_price     = trade["entry_price"]
        qty             = trade["qty"]
        trade_direction = trade.get("trade_direction") or "LONG"

        def _pnl(exit_p):
            if trade_direction == "SHORT":
                return (entry_price - exit_p) * qty
            return (exit_p - entry_price) * qty

        if trade_direction == "SHORT":
            # SHORT: SL fires when premium RISES above sl_price
            sl_hit = sl_price and sl_price > 0 and ltp >= sl_price
            # SHORT: TP fires when premium FALLS below tp_price
            tp_hit = tp_price and tp_price > 0 and ltp <= tp_price
        else:
            # LONG: SL fires when premium falls below sl_price
            sl_hit = sl_price and sl_price > 0 and ltp <= sl_price
            # LONG: TP fires when premium rises above tp_price
            tp_hit = tp_price and tp_price > 0 and ltp >= tp_price

        if sl_hit:
            pnl = _pnl(ltp)
            close_paper_trade(
                paper_trade_id=paper_trade_id,
                exit_price=ltp,
                exit_reason="SL",
                trade_direction=trade_direction,
            )
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][EXIT_SL] "
                f"trade_id={paper_trade_id} dir={trade_direction} "
                f"ltp={ltp} sl={sl_price}"
            )
            try:
                notify_sl_exit({
                    "strategy_id":    strategy_id,
                    "mode":           "paper",
                    "symbol":         symbol,
                    "entry_price":    entry_price,
                    "exit_price":     ltp,
                    "pnl":            pnl,
                    "trade_direction": trade_direction,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][SL_NOTIFY_ERROR] {e}")
            return

        if tp_hit:
            pnl = _pnl(ltp)
            close_paper_trade(
                paper_trade_id=paper_trade_id,
                exit_price=ltp,
                exit_reason="TP",
                trade_direction=trade_direction,
            )
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][EXIT_TP] "
                f"trade_id={paper_trade_id} dir={trade_direction} "
                f"ltp={ltp} tp={tp_price}"
            )
            try:
                notify_tp_exit({
                    "strategy_id":    strategy_id,
                    "mode":           "paper",
                    "symbol":         symbol,
                    "entry_price":    entry_price,
                    "exit_price":     ltp,
                    "pnl":            pnl,
                    "trade_direction": trade_direction,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][TP_NOTIFY_ERROR] {e}")

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

        entry_price     = trade["entry_price"]
        qty             = trade["qty"]
        trade_direction = trade.get("trade_direction") or "LONG"

        if trade_direction == "SHORT":
            pnl = (entry_price - ltp) * qty
        else:
            pnl = (ltp - entry_price) * qty

        close_paper_trade(
            paper_trade_id=paper_trade_id,
            exit_price=ltp,
            exit_reason=reason,
            trade_direction=trade_direction,
        )

        write_audit_log(
            f"[STRATEGY={strategy_id}][PAPER][FORCE_EXIT] "
            f"trade_id={paper_trade_id} dir={trade_direction} "
            f"symbol={symbol} reason={reason} exit={ltp} pnl={pnl:.2f}"
        )

        try:
            notify_manual_exit({
                "strategy_id":    strategy_id,
                "mode":           "paper",
                "symbol":         symbol,
                "entry_price":    entry_price,
                "exit_price":     ltp,
                "exit_reason":    reason,
                "pnl":            pnl,
                "trade_direction": trade_direction,
            })
        except Exception as e:
            write_audit_log(f"[TELEGRAM][MANUAL_EXIT_NOTIFY_ERROR] {e}")