from datetime import datetime
import uuid
import time

from app.engine.bb_options.option_selector import OptionSelector
from app.engine.bb_options.confluence_signal_engine import TradeSignal
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import insert_trade, update_gtt, close_trade
from app.marketdata.ltp_store import LTPStore
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.db.paper_trades_repo import (
    get_open_paper_trades_by_side,   # FIX Bug 1: PAPER exit routing
    get_paper_trade_by_id,           # FIX Bug 4: entry_price for Telegram
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
        self.signal_engine = None  # set properly via attach_state_managers()

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] TradeManager INIT "
            f"fut_symbol={self.symbol_fut}"
        )

    # ==================================================
    # STATE MANAGERS
    # ==================================================

    def attach_state_managers(self, ce_state, pe_state, signal_engine=None):
        self.ce_state = ce_state
        self.pe_state = pe_state
        self.signal_engine = signal_engine

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
        session_end   = self.config.get("session_end",   "15:15")

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

            self.signal_engine.confirm_entry("CE")
            if not self._enter("CE"):
                # Broker reject / no option / paper guard — reverse the flag
                self.signal_engine.notify_exit("CE")

        elif signal.action == "ENTER_PE":

            if self.pe_state and self.pe_state.in_trade:
                write_audit_log("[BB][SKIP] PE already in trade")
                return

            self.signal_engine.confirm_entry("PE")
            if not self._enter("PE"):
                self.signal_engine.notify_exit("PE")

    # ==================================================
    # ENTRY
    # ==================================================

    def _enter(self, side: str) -> bool:
        """Returns True if entry was confirmed, False on any abort."""

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] "
            f"[ENTRY_ATTEMPT] side={side}"
        )

        fut_price = LTPStore.get(self.symbol_fut)

        if not fut_price:
            write_audit_log("[BB][ENTRY_ABORT] No FUT LTP")
            return False

        selected = self.selector.select(
            futures_price=fut_price,
            direction=side,
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

            # FIX Bug 3: use the trade_id returned by record_entry (the DB id),
            # not a separate local uuid. record_entry returns the paper_trade_id
            # it inserted, so both the DB row and any future reference are consistent.
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
                # record_entry blocked the trade (duplicate side guard, trade_on=False, etc.)
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_BLOCKED] "
                    f"record_entry returned None for side={side}"
                )
                return False

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_CONFIRMED] "
                f"{symbol} side={side} trade_id={paper_trade_id}"
            )

            # NOTE: ce_state / pe_state are None in PAPER mode.
            # State is tracked in DB via paper_trades table.
            # signal_engine.ce_in_trade / pe_in_trade are the in-memory guards.

            return True

        # ==========================
        # LIVE MODE
        # ==========================

        # ── Phase 1: place the buy order ──────────────────────────
        # Any failure here means nothing happened broker-side — safe to abort.
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

        # ── Poll for fill price up to 5 seconds ──────────────────────
        start = time.time()
        while avg_price <= 0 and time.time() - start < 5:
            avg_price = self.executor.get_last_avg_price(order_id)
            time.sleep(0.3)

        # Fallback 1: WS LTPStore (may be None for fresh option)
        if avg_price <= 0:
            avg_price = LTPStore.get(symbol) or 0

        # Fallback 2: REST quote — option may have no WS ticks yet
        if avg_price <= 0:
            try:
                quote = self.executor.broker_manager.get_data_kite().ltp(f"NFO:{symbol}")
                avg_price = quote[f"NFO:{symbol}"]["last_price"] or 0
                if avg_price > 0:
                    write_audit_log(
                        f"[BB][LIVE][AVG_PRICE_REST] {symbol} ltp={avg_price} "
                        f"(WS unavailable, used REST fallback)"
                    )
            except Exception as ltp_err:
                write_audit_log(f"[BB][LIVE][AVG_PRICE_REST_FAIL] {ltp_err}")

        # Fallback 3: use premium from option selector as last resort
        if avg_price <= 0:
            avg_price = premium
            write_audit_log(
                f"[BB][LIVE][AVG_PRICE_PREMIUM] {symbol} using selector premium={premium} "
                f"as fill price (all LTP sources unavailable)"
            )

        sl_price = avg_price * (1 - self.sl_percent / 100) if self.sl_percent > 0 else 0
        tp_price = avg_price * (1 + self.tp_percent / 100) if self.tp_percent > 0 else 0

        # ── Phase 2: GTT + DB + state ─────────────────────────────
        # Buy is already confirmed — never abort here.
        # If GTT fails for any reason, register the trade anyway so that
        # SuperTrend exit and EOD squareoff can still close the position.
        gtt_id = None

        if sl_price > 0 or tp_price > 0:
            try:
                gtt_id = self.executor.place_gtt_oco(
                    symbol=symbol,
                    qty=quantity,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    last_price=avg_price,   # fill price avoids LTPStore lookup on fresh options
                )
            except Exception as gtt_err:
                write_audit_log(
                    f"[BB][LIVE][CRITICAL] GTT FAILED — position is UNPROTECTED. "
                    f"side={side} symbol={symbol} entry={avg_price} "
                    f"ERR={repr(gtt_err)}. "
                    f"Trade registered without GTT; ST exit and EOD squareoff will close it."
                )
                try:
                    notify_trade_entry({
                        "strategy_id": self.strategy_id,
                        "mode": "live",
                        "symbol": symbol,
                        "side": side,
                        "entry_price": avg_price,
                        "quantity": quantity,
                        "sl": sl_price,
                        "tp": tp_price,
                        "note": f"⚠️ GTT FAILED: {gtt_err}. No SL/TP protection. Will exit via ST/EOD.",
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
                # DB CHECK constraint only allows ('AUTO_RR', 'MANUAL', 'GTT').
                # Strategy is always GTT-mode; gtt_id=None means GTT placement
                # failed but intent remains GTT. Use 'GTT' unconditionally.
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
            f"{symbol} side={side} entry={avg_price} "
            f"gtt={'placed' if gtt_id else 'NONE'}"
        )

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

        return True

    # ==================================================
    # EXIT
    # ==================================================

    def _exit(self, side: str, exit_reason: str = "SuperTrend"):

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self.trade_mode}] "
            f"[EXIT_ATTEMPT] side={side}"
        )

        # ==================================================
        # PAPER MODE
        # FIX Bug 1: pe_state / ce_state are None in PAPER mode.
        # Old code did `if not state or not state.active_trade: return`
        # which always aborted — SuperTrend exits NEVER fired for paper.
        # Fix: query open paper trades by side directly from DB.
        # ==================================================

        if self.trade_mode == "PAPER":

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
                entry_price    = trade_row["entry_price"]   # FIX Bug 4: now available
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

                # Telegram — FIX Bug 4: pass real entry/exit/pnl
                try:
                    # exit_price can be None if option has no WS tick yet.
                    # Fall back to entry_price (flat PnL) rather than
                    # passing None which crashes comparison inside notify.
                    safe_exit = exit_price if exit_price is not None else entry_price
                    pnl = (
                        (safe_exit - entry_price) * trade_row["qty"]
                        if safe_exit is not None and entry_price is not None
                        else None
                    )
                    notify_manual_exit({
                        "strategy_id": self.strategy_id,
                        "mode": "paper",
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "exit_price":  safe_exit,
                        "exit_reason": "SuperTrend",
                        "pnl": pnl,
                    })
                except Exception as e:
                    write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

            return

        # ==================================================
        # LIVE MODE
        # ==================================================

        state = self.ce_state if side == "CE" else self.pe_state

        if not state or not state.active_trade:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][LIVE] "
                f"[EXIT_ABORT] No active trade for side={side}"
            )
            # Ensure signal engine is consistent even if state is missing
            if self.signal_engine:
                self.signal_engine.notify_exit(side)
            return

        trade      = state.active_trade
        symbol     = trade.symbol
        trade_id   = trade.trade_id
        qty        = trade.qty
        gtt_id     = trade.gtt_id

        # --------------------------------------------------
        # STEP 1 — Cancel the live GTT first.
        # This prevents a race where the GTT fires at the
        # same moment as our market SELL.
        # Non-fatal: if the GTT is already gone (triggered
        # or deleted), we log and continue.
        # --------------------------------------------------

        if gtt_id:
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(
                    f"[BB][LIVE][GTT_CANCEL_WARN] "
                    f"gtt_id={gtt_id} ERR={e} — continuing with market sell"
                )

        # --------------------------------------------------
        # STEP 2 — Place a market SELL to close the position.
        # If this fails we do NOT clear any state so that
        # GTTMonitor can still catch the trade later.
        # --------------------------------------------------

        try:
            exit_order_id = self.executor.place_market_sell(
                symbol=symbol,
                qty=qty,
            )
        except Exception as e:
            write_audit_log(
                f"[BB][LIVE][EXIT_FAILED] side={side} ERR={repr(e)}"
            )
            return  # state and signal_engine flags intentionally left unchanged

        # --------------------------------------------------
        # STEP 3 — Fetch actual average fill price (up to 3s).
        # Priority: broker avg poll → LTPStore → REST kite.ltp()
        # REST fallback handles illiquid/far-OTM options that have
        # no WS ticks, ensuring exit_price is never NULL in the DB.
        # --------------------------------------------------

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
            pass  # fallback to LTP already set above

        # REST fallback — only if both broker poll and LTPStore failed
        if not exit_price:
            try:
                quote = self.executor.broker_manager.get_data_kite().ltp(
                    f"NFO:{symbol}"
                )
                rest_ltp = quote[f"NFO:{symbol}"]["last_price"]
                if rest_ltp and rest_ltp > 0:
                    exit_price = rest_ltp
                    write_audit_log(
                        f"[BB][LIVE][EXIT_PRICE_REST] {symbol} ltp={rest_ltp} "
                        f"(WS unavailable, used REST fallback)"
                    )
            except Exception as rest_err:
                write_audit_log(
                    f"[BB][LIVE][EXIT_PRICE_REST_FAIL] {symbol} ERR={rest_err}"
                )

        # --------------------------------------------------
        # STEP 4 — Fetch entry_price from DB for Telegram PnL.
        # --------------------------------------------------

        entry_price = None
        try:
            from app.db.trades_repo import get_trade_by_id
            db_trade = get_trade_by_id(trade_id)
            if db_trade:
                entry_price = db_trade.get("entry_price")
        except Exception:
            pass

        # --------------------------------------------------
        # STEP 5 — Close in DB.
        # --------------------------------------------------

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=exit_reason,
            )
        except Exception as e:
            write_audit_log(f"[BB][LIVE][DB_CLOSE_FAIL] trade_id={trade_id} ERR={e}")

        # --------------------------------------------------
        # STEP 6 — Clear in-memory state + signal engine.
        # Only reached if market sell was placed successfully.
        # --------------------------------------------------

        state.clear_trade()
        if self.signal_engine:
            self.signal_engine.notify_exit(side)

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][LIVE][EXIT_CONFIRMED] "
            f"{symbol} side={side} exit={exit_price}"
        )

        # --------------------------------------------------
        # STEP 7 — Telegram notification.
        # --------------------------------------------------

        try:
            pnl = (
                (exit_price - entry_price) * qty
                if exit_price is not None and entry_price is not None
                else None
            )
            notify_manual_exit({
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price":  exit_price,
                "exit_reason": exit_reason,
                "pnl": pnl,
            })
        except Exception as e:
            write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

    # ==================================================
    # EOD SQUARE-OFF (LIVE only)
    # Called at 15:25 by the scheduler.  Bypasses the
    # session-window gate in handle_signal() intentionally.
    # ==================================================

    def eod_squareoff(self):

        # ==================================================
        # PAPER MODE — DB closure is handled by paper_trade_eod_job.
        # We only need to sync the in-memory signal engine flags so
        # candles after 15:25 don't log spurious PE/CE_ALREADY_IN_TRADE
        # rejections, and so next-day entries are not blocked before
        # reset_daily() fires at 09:15.
        # ==================================================

        if self.trade_mode == "PAPER":
            if self.signal_engine:
                for side in ("CE", "PE"):
                    self.signal_engine.notify_exit(side)
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EOD] "
                    f"Signal engine flags cleared (trades closed by paper EOD job)"
                )
            return

        # ==================================================
        # LIVE MODE — cancel GTT + market sell for each open side.
        # ==================================================

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][LIVE][EOD] "
            f"Square-off triggered"
        )

        for side in ("CE", "PE"):
            state = self.ce_state if side == "CE" else self.pe_state
            if state and state.in_trade and state.active_trade:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][LIVE][EOD] "
                    f"Closing open {side} trade"
                )
                try:
                    self._exit(side, exit_reason="EOD_SQUARE_OFF")
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