from datetime import datetime
import uuid
import time

from app.engine.bb_options.option_selector import OptionSelector
from app.engine.bb_options.confluence_signal_engine import TradeSignal
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import insert_trade, update_gtt, close_trade
from app.marketdata.ltp_store import LTPStore
from app.trading.paper_trade_recorder import PaperTradeRecorder

# 🔔 TELEGRAM NOTIFICATIONS
from app.api.telegram_api import (
    notify_trade_entry,
    notify_manual_exit,
)


class BBTradeManager:

    def __init__(
        self,
        strategy_id: str,
        trade_mode: str,  # "LIVE" or "PAPER"
        executor,
        symbol_fut: str,
        lot_size: int,
        lot_count: int,
        sl_percent: float,
        tp_percent: float,
        max_premium: float,
        scan_strikes: int,
        config: dict,
    ):

        self.strategy_id = strategy_id
        self.trade_mode = trade_mode
        self.executor = executor
        self.symbol_fut = symbol_fut
        self.config = config

        self.lot_size = lot_size
        self.default_lot_count = lot_count

        self.sl_percent = sl_percent or 0
        self.tp_percent = tp_percent or 0

        self.selector = OptionSelector(
            max_premium=max_premium,
            scan_strikes=scan_strikes,
        )

        self.ce_state = None
        self.pe_state = None

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] TradeManager INIT "
            f"fut_symbol={self.symbol_fut}"
        )

    # ==================================================
    # STATE MANAGERS
    # ==================================================

    def attach_state_managers(self, ce_state, pe_state):
        self.ce_state = ce_state
        self.pe_state = pe_state

    # ==================================================
    # HANDLE SIGNAL
    # ==================================================

    def handle_signal(self, signal: TradeSignal):

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] "
            f"[SIGNAL_RECEIVED] action={signal.action} "
            f"reason={signal.reason} rejection={signal.rejection_reason}"
        )

        if not signal or not signal.action:
            return

        now = datetime.now().strftime("%H:%M")

        session_start = self.config.get("session_start", "09:15")
        session_end = self.config.get("session_end", "15:15")

        if now < session_start or now >= session_end:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][{self.trade_mode}][BLOCKED] "
                f"Outside session window"
            )
            return

        if signal.action == "EXIT_CE":
            self._exit("CE")
            return

        if signal.action == "EXIT_PE":
            self._exit("PE")
            return

        if signal.action == "ENTER_CE":

            if self.ce_state and self.ce_state.in_trade:
                write_audit_log("[BB][SKIP] CE already in trade")
                return

            self._enter("CE")

        elif signal.action == "ENTER_PE":

            if self.pe_state and self.pe_state.in_trade:
                write_audit_log("[BB][SKIP] PE already in trade")
                return

            self._enter("PE")

    # ==================================================
    # ENTRY
    # ==================================================

    def _enter(self, side: str):

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] "
            f"[ENTRY_ATTEMPT] side={side}"
        )

        fut_price = LTPStore.get(self.symbol_fut)

        if not fut_price:
            write_audit_log("[BB][ENTRY_ABORT] No FUT LTP")
            return

        selected = self.selector.select(
            futures_price=fut_price,
            direction=side,
        )

        if not selected:
            write_audit_log("[BB][ENTRY_ABORT] No suitable option found")
            return

        symbol, premium = selected

        if symbol == self.symbol_fut:
            write_audit_log("[BB_FATAL] OptionSelector returned FUT symbol. ABORTING.")
            return

        write_audit_log(
            f"[BB][OPTION_SELECTED] symbol={symbol} premium={premium}"
        )

        lot_count = (
            self.config.get("ce_lots", 1)
            if side == "CE"
            else self.config.get("pe_lots", 1)
        )

        quantity = self.lot_size * lot_count

        sl_price = premium * (1 - self.sl_percent / 100) if self.sl_percent > 0 else 0
        tp_price = premium * (1 + self.tp_percent / 100) if self.tp_percent > 0 else 0

        # ==========================
        # PAPER MODE
        # ==========================

        if self.trade_mode == "PAPER":

            trade_id = str(uuid.uuid4())

            PaperTradeRecorder.record_entry(
                strategy_id=self.strategy_id,
                symbol=symbol,
                token=0,
                entry_price=premium,
                sl_price=sl_price,
                tp_price=tp_price,
                candle_ts=int(datetime.now().timestamp()),
            )

            # Mirror LIVE behavior in PAPER mode

            if side == "CE" and self.ce_state:
                self.ce_state.register_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    gtt_id=None,
                )

            elif side == "PE" and self.pe_state:
                self.pe_state.register_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    gtt_id=None,
                )

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_CONFIRMED] "
                f"{symbol} side={side}"
            )

            # 🔔 TELEGRAM ENTRY NOTIFICATION (SAFE)
            try:
                notify_trade_entry({
                    "strategy_id": self.strategy_id,
                    "mode": self.trade_mode.lower(),
                    "symbol": symbol,
                    "side": side,
                    "entry_price": premium,
                    "quantity": quantity,
                    "sl": sl_price,
                    "tp": tp_price,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][ENTRY_NOTIFY_ERROR] {e}")

            return

        # ==========================
        # LIVE MODE (SAFE)
        # ==========================

        try:

            order_id, avg_price, filled_qty = self.executor.place_buy(
                symbol=symbol,
                token=0,
                qty=quantity,
            )

            if filled_qty <= 0:
                write_audit_log("[BB][LIVE][ENTRY_ABORT] No fill")
                return

            start = time.time()
            while avg_price <= 0 and time.time() - start < 3:
                avg_price = self.executor.get_last_avg_price(order_id)
                time.sleep(0.3)

            if avg_price <= 0:
                avg_price = LTPStore.get(symbol) or 0

            sl_price = avg_price * (1 - self.sl_percent / 100) if self.sl_percent > 0 else 0
            tp_price = avg_price * (1 + self.tp_percent / 100) if self.tp_percent > 0 else 0

            gtt_id = None

            if sl_price > 0 or tp_price > 0:
                gtt_id = self.executor.place_gtt_oco(
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                )

            trade_id = str(uuid.uuid4())

            insert_trade(
                trade_id=trade_id,
                strategy_id=self.strategy_id,
                slot=side,
                symbol=symbol,
                token=0,
                entry_price=avg_price,
                qty=quantity,
                buy_order_id=order_id,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_mode="GTT" if gtt_id else "NONE",
            )

            if side == "CE" and self.ce_state:
                self.ce_state.register_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    gtt_id=gtt_id,
                )

            elif side == "PE" and self.pe_state:
                self.pe_state.register_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    gtt_id=gtt_id,
                )

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][LIVE][ENTRY_CONFIRMED] "
                f"{symbol} side={side} entry={avg_price}"
            )

            # 🔔 TELEGRAM ENTRY NOTIFICATION (SAFE)
            try:
                notify_trade_entry({
                    "strategy_id": self.strategy_id,
                    "mode": self.trade_mode.lower(),
                    "symbol": symbol,
                    "side": side,
                    "entry_price": avg_price,
                    "quantity": quantity,
                    "sl": sl_price,
                    "tp": tp_price,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][ENTRY_NOTIFY_ERROR] {e}")

        except Exception as e:

            write_audit_log(
                f"[BB][LIVE][ENTRY_FAILED] side={side} ERR={repr(e)}"
            )
            return

    # ==================================================
    # EXIT
    # ==================================================

    def _exit(self, side: str):

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] "
            f"[EXIT_ATTEMPT] side={side}"
        )

        state = self.ce_state if side == "CE" else self.pe_state

        if not state or not state.active_trade:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][{self.trade_mode}] "
                f"[EXIT_ABORT] No active trade for side={side}"
            )
            return

        trade = state.active_trade
        symbol = trade.symbol
        trade_id = trade.trade_id
        qty = trade.qty
        entry_price = None  # Not stored in BBTrade currently
        exit_price = LTPStore.get(symbol)

        # ==================================================
        # PAPER MODE
        # ==================================================

        if self.trade_mode == "PAPER":

            try:
                PaperTradeRecorder.force_exit(
                    paper_trade_id=trade_id,
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="Strategy exit",
                )
            except Exception as e:
                write_audit_log(f"[BB][PAPER][EXIT_FAILED] ERR={e}")
                return

            state.clear_trade()

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][EXIT_CONFIRMED] "
                f"{symbol} side={side}"
            )

            try:
                notify_manual_exit({
                    "strategy_id": self.strategy_id,
                    "mode": self.trade_mode.lower(),
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_reason": "Strategy exit",
                    "pnl": 0,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

            return

        # ==================================================
        # LIVE MODE
        # ==================================================

        try:
            self.executor.place_exit(
                symbol=symbol,
                qty=qty,
                reason="SuperTrend",
            )

            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=None,
                exit_reason="SuperTrend",
            )

            state.clear_trade()

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][LIVE][EXIT_CONFIRMED] "
                f"{symbol} side={side}"
            )

            try:
                notify_manual_exit({
                    "strategy_id": self.strategy_id,
                    "mode": self.trade_mode.lower(),
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_reason": "Strategy exit",
                    "pnl": 0,
                })
            except Exception as e:
                write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

        except Exception as e:
            write_audit_log(
                f"[BB][LIVE][EXIT_FAILED] side={side} ERR={repr(e)}"
            )