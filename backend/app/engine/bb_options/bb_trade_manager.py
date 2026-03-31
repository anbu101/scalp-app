# backend/app/engine/bb_options/bb_trade_manager.py

from datetime import datetime
import uuid
import time

from app.engine.bb_options.option_selector import OptionSelector
from app.engine.bb_options.confluence_signal_engine import TradeSignal
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import insert_trade, update_gtt, close_trade
from app.marketdata.ltp_store import LTPStore
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.config.strategy_loader import load_strategy_config
from app.db.paper_trades_repo import (
    get_open_paper_trades_by_side,
    get_paper_trade_by_id,
)

# 🔔 TELEGRAM NOTIFICATIONS
from app.api.telegram_api import (
    notify_trade_entry,
    notify_manual_exit,
)


class BBTradeManager:

    def __init__(
        self,
        strategy_id: str,
        trade_mode: str,   # "LIVE" or "PAPER" — startup value only
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
        self.strategy_id       = strategy_id
        self._startup_trade_mode = trade_mode   # immutable startup value
        self.executor          = executor
        self.symbol_fut        = symbol_fut
        self.config            = config

        self.lot_size          = lot_size
        self.default_lot_count = lot_count

        # Store constructor values as fallbacks only.
        # All _live_*() helpers read from disk so Settings changes
        # take effect on the next trade without restarting.
        self._sl_percent_fallback  = sl_percent  or 0
        self._tp_percent_fallback  = tp_percent  or 0
        self._max_premium_fallback = max_premium or 300

        self.selector = OptionSelector(
            max_premium=max_premium,
            scan_strikes=scan_strikes,
        )

        self.ce_state      = None
        self.pe_state      = None
        self.signal_engine = None

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self._startup_trade_mode}] "
            f"TradeManager INIT fut_symbol={self.symbol_fut}"
        )

    # ==================================================
    # STATE MANAGERS
    # ==================================================

    def attach_state_managers(self, ce_state, pe_state, signal_engine=None):
        self.ce_state      = ce_state
        self.pe_state      = pe_state
        self.signal_engine = signal_engine

    # ==================================================
    # LIVE CONFIG HELPERS
    # Always read from disk so Settings UI changes apply
    # immediately on the next trade without a restart.
    # ==================================================

    def _live_trade_mode(self) -> str:
        """
        Returns the current effective trade mode from live config.

        If the user switches LIVE → PAPER in the UI, the next signal
        will be handled as PAPER even though the engine started in LIVE.

        NOTE: switching PAPER → LIVE mid-session is intentionally blocked
        here — going from paper to live requires a restart because the
        LIVE path needs broker state managers (ce_state, pe_state) that
        are only created at startup in LIVE mode.
        """
        try:
            cfg  = load_strategy_config(self.strategy_id)
            mode = cfg.get("trade_execution_mode", self._startup_trade_mode)

            # Safety: only allow downgrading to PAPER mid-session.
            # Upgrading PAPER → LIVE needs a restart.
            if self._startup_trade_mode == "PAPER" and mode == "LIVE":
                write_audit_log(
                    f"[BB][TRADE_MODE] Config says LIVE but engine started "
                    f"in PAPER mode — keeping PAPER (restart required to go LIVE)."
                )
                return "PAPER"

            return mode

        except Exception:
            return self._startup_trade_mode

    def _live_sl_pct(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "sl_pct", self._sl_percent_fallback
                ) or 0
            )
        except Exception:
            return self._sl_percent_fallback

    def _live_tp_pct(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "tp_pct", self._tp_percent_fallback
                ) or 0
            )
        except Exception:
            return self._tp_percent_fallback

    def _live_max_premium(self) -> float:
        """
        Read max_premium fresh from config so UI changes take effect
        immediately without restarting the engine.
        """
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "max_premium", self._max_premium_fallback
                ) or self._max_premium_fallback
            )
        except Exception:
            return self._max_premium_fallback

    def _live_lot_count(self, side: str) -> int:
        try:
            cfg = load_strategy_config(self.strategy_id)
            key = "ce_lots" if side == "CE" else "pe_lots"
            return int(
                cfg.get(key, self.default_lot_count) or self.default_lot_count
            )
        except Exception:
            return self.default_lot_count

    # ==================================================
    # HANDLE SIGNAL
    # ==================================================

    def handle_signal(self, signal: TradeSignal):

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self._startup_trade_mode}] "
            f"[SIGNAL_RECEIVED] action={signal.action} "
            f"reason={signal.reason} rejection={signal.rejection_reason}"
        )

        if not signal or not signal.action:
            return

        # Re-read trade mode from live config on every signal.
        # This is how switching to PAPER in the UI takes effect
        # without restarting the engine.
        effective_mode = self._live_trade_mode()

        now          = datetime.now().strftime("%H:%M")
        live_cfg     = load_strategy_config(self.strategy_id)
        session_start = live_cfg.get("session_start", "09:15")
        session_end   = live_cfg.get("session_end",   "15:15")

        if now < session_start or now >= session_end:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][{effective_mode}][BLOCKED] "
                f"Outside session window ({session_start}–{session_end})"
            )
            return

        if signal.action == "EXIT_CE":
            self._exit("CE", effective_mode=effective_mode)
            return

        if signal.action == "EXIT_PE":
            self._exit("PE", effective_mode=effective_mode)
            return

        if signal.action == "ENTER_CE":
            if effective_mode == "LIVE" and self.ce_state and self.ce_state.in_trade:
                write_audit_log("[BB][SKIP] CE already in trade")
                return
            self.signal_engine.confirm_entry("CE")
            if not self._enter("CE", effective_mode=effective_mode):
                self.signal_engine.notify_exit("CE")

        elif signal.action == "ENTER_PE":
            if effective_mode == "LIVE" and self.pe_state and self.pe_state.in_trade:
                write_audit_log("[BB][SKIP] PE already in trade")
                return
            self.signal_engine.confirm_entry("PE")
            if not self._enter("PE", effective_mode=effective_mode):
                self.signal_engine.notify_exit("PE")

    # ==================================================
    # ENTRY
    # ==================================================

    def _enter(self, side: str, effective_mode: str) -> bool:
        """Returns True if entry was confirmed, False on any abort."""

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{effective_mode}] "
            f"[ENTRY_ATTEMPT] side={side}"
        )

        fut_price = LTPStore.get(self.symbol_fut)
        if not fut_price:
            write_audit_log("[BB][ENTRY_ABORT] No FUT LTP")
            return False

        # Read max_premium live so UI changes apply immediately
        live_max_premium = self._live_max_premium()

        selected = self.selector.select(
            futures_price=fut_price,
            direction=side,
            max_premium_override=live_max_premium,   # ← live value
        )

        if not selected:
            write_audit_log("[BB][ENTRY_ABORT] No suitable option found")
            return False

        symbol, premium = selected

        if symbol == self.symbol_fut:
            write_audit_log("[BB_FATAL] OptionSelector returned FUT symbol. ABORTING.")
            return False

        write_audit_log(
            f"[BB][OPTION_SELECTED] symbol={symbol} premium={premium}"
        )

        # Read all risk params live
        live_sl_pct = self._live_sl_pct()
        live_tp_pct = self._live_tp_pct()
        lot_count   = self._live_lot_count(side)
        quantity    = self.lot_size * lot_count

        write_audit_log(
            f"[BB][LIVE_CONFIG] side={side} "
            f"sl_pct={live_sl_pct} tp_pct={live_tp_pct} "
            f"max_premium={live_max_premium} "
            f"lots={lot_count} qty={quantity}"
        )

        # Preliminary SL/TP from selector premium (before fill price known)
        sl_price = premium * (1 - live_sl_pct / 100) if live_sl_pct > 0 else 0
        tp_price = premium * (1 + live_tp_pct / 100) if live_tp_pct > 0 else 0

        # ==========================
        # PAPER MODE
        # ==========================

        if effective_mode == "PAPER":

            paper_trade_id = PaperTradeRecorder.record_entry(
                strategy_id=self.strategy_id,
                symbol=symbol,
                token=0,
                entry_price=premium,
                sl_price=sl_price,
                tp_price=tp_price,
                candle_ts=int(datetime.now().timestamp()),
            )

            if not paper_trade_id:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_BLOCKED] "
                    f"record_entry returned None for side={side}"
                )
                return False

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_CONFIRMED] "
                f"{symbol} side={side} trade_id={paper_trade_id}"
            )
            return True

        # ==========================
        # LIVE MODE
        # ==========================

        # Phase 1: place the buy order
        try:
            order_id, avg_price, filled_qty = self.executor.place_buy(
                symbol=symbol,
                token=0,
                qty=quantity,
            )
        except Exception as e:
            write_audit_log(
                f"[BB][LIVE][ENTRY_FAILED] side={side} BUY_ERROR={repr(e)}"
            )
            return False

        if filled_qty <= 0:
            write_audit_log("[BB][LIVE][ENTRY_ABORT] No fill")
            return False

        # Poll for fill price up to 5 seconds
        start = time.time()
        while avg_price <= 0 and time.time() - start < 5:
            avg_price = self.executor.get_last_avg_price(order_id)
            time.sleep(0.3)

        # Fallback 1: WS LTPStore
        if avg_price <= 0:
            avg_price = LTPStore.get(symbol) or 0

        # Fallback 2: REST quote
        if avg_price <= 0:
            try:
                quote = self.executor.broker_manager.get_data_kite().ltp(
                    f"NFO:{symbol}"
                )
                avg_price = quote[f"NFO:{symbol}"]["last_price"] or 0
                if avg_price > 0:
                    write_audit_log(
                        f"[BB][LIVE][AVG_PRICE_REST] {symbol} ltp={avg_price} "
                        f"(WS unavailable, used REST fallback)"
                    )
            except Exception as ltp_err:
                write_audit_log(f"[BB][LIVE][AVG_PRICE_REST_FAIL] {ltp_err}")

        # Fallback 3: selector premium
        if avg_price <= 0:
            avg_price = premium
            write_audit_log(
                f"[BB][LIVE][AVG_PRICE_PREMIUM] {symbol} using selector "
                f"premium={premium} as fill price"
            )

        # Recalculate SL/TP from actual fill price
        sl_price = avg_price * (1 - live_sl_pct / 100) if live_sl_pct > 0 else 0
        tp_price = avg_price * (1 + live_tp_pct / 100) if live_tp_pct > 0 else 0

        write_audit_log(
            f"[BB][GTT_PARAMS] symbol={symbol} "
            f"avg_price={avg_price:.2f} "
            f"sl_pct={live_sl_pct} tp_pct={live_tp_pct} "
            f"sl_price={sl_price:.2f} tp_price={tp_price:.2f}"
        )

        if live_tp_pct > 0 and tp_price <= 0:
            write_audit_log(
                f"[BB][GTT_WARN] tp_pct={live_tp_pct} but tp_price resolved "
                f"to {tp_price} — avg_price may be zero. GTT will be SL-only."
            )

        # Phase 2: GTT + DB + state
        gtt_id          = None
        _entry_notified = False

        if sl_price > 0 or tp_price > 0:
            try:
                gtt_id = self.executor.place_gtt_oco(
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    last_price=avg_price,
                )
            except Exception as gtt_err:
                write_audit_log(
                    f"[BB][LIVE][CRITICAL] GTT FAILED — position is UNPROTECTED. "
                    f"side={side} symbol={symbol} entry={avg_price} "
                    f"ERR={repr(gtt_err)}. "
                    f"Trade registered without GTT; ST exit and EOD squareoff will close it."
                )
                _entry_notified = True
                try:
                    notify_trade_entry({
                        "strategy_id": self.strategy_id,
                        "mode":        "live",
                        "symbol":      symbol,
                        "side":        side,
                        "entry_price": avg_price,
                        "quantity":    quantity,
                        "sl":          sl_price,
                        "tp":          tp_price,
                        "note": f"⚠️ GTT FAILED: {gtt_err}. No SL/TP protection.",
                    })
                except Exception:
                    pass
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({
                        "message": (
                            f"GTT placement FAILED for {symbol} ({side})\n"
                            f"Entry: ₹{avg_price} | SL: ₹{sl_price:.2f} | TP: ₹{tp_price:.2f}\n"
                            f"ERR: {gtt_err}\n"
                            f"Position is UNPROTECTED — will exit via SuperTrend/EOD only."
                        ),
                        "severity": "error",
                    })
                except Exception:
                    pass

        trade_id = str(uuid.uuid4())

        try:
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
                tp_mode="GTT",
            )
        except Exception as db_err:
            write_audit_log(
                f"[BB][LIVE][CRITICAL] DB INSERT FAILED — "
                f"trade exists in broker but NOT in DB. "
                f"side={side} symbol={symbol} order_id={order_id} ERR={repr(db_err)}"
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
            f"{symbol} side={side} entry={avg_price:.2f} "
            f"sl={sl_price:.2f} tp={tp_price:.2f} "
            f"gtt={'placed' if gtt_id else 'NONE'}"
        )

        try:
            if not _entry_notified:
                notify_trade_entry({
                    "strategy_id": self.strategy_id,
                    "mode":        "live",
                    "symbol":      symbol,
                    "side":        side,
                    "entry_price": avg_price,
                    "quantity":    quantity,
                    "sl":          sl_price,
                    "tp":          tp_price,
                })
        except Exception as e:
            write_audit_log(f"[TELEGRAM][ENTRY_NOTIFY_ERROR] {e}")

        return True

    # ==================================================
    # EXIT
    # ==================================================

    def _exit(self, side: str, effective_mode: str, exit_reason: str = "SuperTrend"):

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{effective_mode}] "
            f"[EXIT_ATTEMPT] side={side}"
        )

        # ==========================
        # PAPER MODE
        # ==========================

        if effective_mode == "PAPER":

            open_trades = get_open_paper_trades_by_side(
                strategy_name=self.strategy_id,
                side=side,
            )

            if not open_trades:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EXIT_ABORT] "
                    f"No open {side} trade found in DB"
                )
                return

            for trade_row in open_trades:
                paper_trade_id = trade_row["paper_trade_id"]
                symbol         = trade_row["symbol"]
                entry_price    = trade_row["entry_price"]
                exit_price     = LTPStore.get(symbol)

                try:
                    PaperTradeRecorder.force_exit(
                        paper_trade_id=paper_trade_id,
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        reason="SuperTrend",
                    )
                except Exception as e:
                    write_audit_log(
                        f"[BB][PAPER][EXIT_FAILED] "
                        f"trade_id={paper_trade_id} ERR={e}"
                    )
                    continue

                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EXIT_CONFIRMED] "
                    f"{symbol} side={side} trade_id={paper_trade_id}"
                )

                try:
                    safe_exit = exit_price if exit_price is not None else entry_price
                    pnl = (
                        (safe_exit - entry_price) * trade_row["qty"]
                        if safe_exit is not None and entry_price is not None
                        else None
                    )
                    notify_manual_exit({
                        "strategy_id": self.strategy_id,
                        "mode":        "paper",
                        "symbol":      symbol,
                        "entry_price": entry_price,
                        "exit_price":  safe_exit,
                        "exit_reason": "SuperTrend",
                        "pnl":         pnl,
                    })
                except Exception as e:
                    write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

            return

        # ==========================
        # LIVE MODE
        # ==========================

        state = self.ce_state if side == "CE" else self.pe_state

        if not state or not state.active_trade:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][LIVE] "
                f"[EXIT_ABORT] No active trade for side={side}"
            )
            if self.signal_engine:
                self.signal_engine.notify_exit(side)
            return

        trade    = state.active_trade
        symbol   = trade.symbol
        trade_id = trade.trade_id
        qty      = trade.qty
        gtt_id   = trade.gtt_id

        # Step 1: Cancel live GTT to prevent race
        if gtt_id:
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(
                    f"[BB][LIVE][GTT_CANCEL_WARN] "
                    f"gtt_id={gtt_id} ERR={e} — continuing with market sell"
                )

        # Step 2: Market SELL
        try:
            exit_order_id = self.executor.place_market_sell(
                symbol=symbol,
                qty=qty,
            )
        except Exception as e:
            write_audit_log(
                f"[BB][LIVE][EXIT_FAILED] side={side} ERR={repr(e)}"
            )
            return

        # Step 3: Fetch actual fill price
        exit_price = LTPStore.get(symbol)

        try:
            start = time.time()
            while time.time() - start < 3:
                avg = self.executor.get_last_avg_price(exit_order_id)
                if avg and avg > 0:
                    exit_price = avg
                    break
                time.sleep(0.3)
        except Exception:
            pass

        if not exit_price:
            try:
                quote    = self.executor.broker_manager.get_data_kite().ltp(
                    f"NFO:{symbol}"
                )
                rest_ltp = quote[f"NFO:{symbol}"]["last_price"]
                if rest_ltp and rest_ltp > 0:
                    exit_price = rest_ltp
            except Exception as rest_err:
                write_audit_log(
                    f"[BB][LIVE][EXIT_PRICE_REST_FAIL] {symbol} ERR={rest_err}"
                )

        # Step 4: Fetch entry_price from DB for Telegram PnL
        entry_price = None
        try:
            from app.db.trades_repo import get_trade_by_id
            db_trade = get_trade_by_id(trade_id)
            if db_trade:
                entry_price = db_trade.get("entry_price")
        except Exception:
            pass

        # Step 5: Close in DB
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=exit_reason,
            )
        except Exception as e:
            write_audit_log(
                f"[BB][LIVE][DB_CLOSE_FAIL] trade_id={trade_id} ERR={e}"
            )

        # Step 6: Clear in-memory state
        state.clear_trade()
        if self.signal_engine:
            self.signal_engine.notify_exit(side)

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][LIVE][EXIT_CONFIRMED] "
            f"{symbol} side={side} exit={exit_price}"
        )

        # Step 7: Telegram
        try:
            pnl = (
                (exit_price - entry_price) * qty
                if exit_price is not None and entry_price is not None
                else None
            )
            notify_manual_exit({
                "strategy_id": self.strategy_id,
                "mode":        "live",
                "symbol":      symbol,
                "entry_price": entry_price,
                "exit_price":  exit_price,
                "exit_reason": exit_reason,
                "pnl":         pnl,
            })
        except Exception as e:
            write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

    # ==================================================
    # EOD SQUARE-OFF
    # ==================================================

    def eod_squareoff(self):

        effective_mode = self._live_trade_mode()

        if effective_mode == "PAPER":
            if self.signal_engine:
                for side in ("CE", "PE"):
                    self.signal_engine.notify_exit(side)
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EOD] "
                    f"Signal engine flags cleared"
                )
            return

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][LIVE][EOD] Square-off triggered"
        )

        for side in ("CE", "PE"):
            state = self.ce_state if side == "CE" else self.pe_state
            if state and state.in_trade and state.active_trade:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][LIVE][EOD] "
                    f"Closing open {side} trade"
                )
                try:
                    self._exit(side, effective_mode="LIVE", exit_reason="EOD_SQUARE_OFF")
                except Exception as e:
                    write_audit_log(
                        f"[STRATEGY={self.strategy_id}][LIVE][EOD][FAIL] "
                        f"side={side} ERR={repr(e)}"
                    )
            else:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][LIVE][EOD] "
                    f"No open {side} trade — skipping"
                )