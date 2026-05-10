# backend/app/engine/bb_options/gtt_monitor.py
#
# BUG FIXES vs previous version:
#
# BUG1 FIX — Single-target TP hit left signal_engine permanently stuck:
#   After clear_trade_leg1(), if active_trade_leg2 is None (single-target),
#   signal_engine.notify_exit() is now called so new entries are allowed.
#
# BUG2 FIX — Partial position detection incorrectly closed leg2:
#   _close_leg1_keep_leg2() closes leg1 in DB only via clear_trade_leg1(),
#   leaving GTT2 and leg2 state completely untouched.
#
# BUG3 FIX — Trailing SL: cancel-before-place caused unprotected leg2:
#   Root cause A: old code cancelled GTT2 first; if new GTT placement
#     failed (Zerodha 0.25% proximity error), leg2 had NO protection and
#     the old gtt_id in state pointed to a cancelled GTT, causing the
#     GTTMonitor miss-loop to run forever (seen in prod logs 14:23–15:25).
#   Root cause B: LTPStore was used as last_price for the new GTT. Option
#     WS ticks can be stale (last tick near entry price), making breakeven_sl
#     and last_price too close → Zerodha rejects with "difference < 0.25%".
#
#   Fix A: PLACE-FIRST, CANCEL-AFTER. New GTT is confirmed before old one
#     is cancelled. If placement fails, old GTT remains active — leg2 stays
#     protected. State is only updated when new GTT is confirmed.
#   Fix B: Use REST kite.ltp() for the option's current price as last_price.
#     REST is always fresh. LTPStore is only used as a fallback when REST fails.
#   Fix C: If breakeven_sl >= current_ltp (LTP moved below entry — trade in
#     loss), skip trailing SL entirely and leave original GTT2 in place.

import time
import threading
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import close_trade, get_trade_by_id
from app.marketdata.ltp_store import LTPStore
from app.config.strategy_loader import load_strategy_config


class GTTMonitor:
    """
    Polls Zerodha GTT status every POLL_INTERVAL seconds.

    Supports single-target (one GTT per side) and multiple-targets
    (two GTTs per side — leg1 and leg2).

    CRITICAL SAFETY RULES:
    1. Network error on GTT fetch must NEVER close a trade.
    2. GTT status="triggered" alone is insufficient — must confirm fill.
    3. MISSING_THRESHOLD consecutive clean misses + position gone = close.
    4. Position verification always runs before acting on missing GTT.
    5. For trailing SL: place new GTT FIRST, cancel old only on success.
    """

    POLL_INTERVAL        = 30
    MISSING_THRESHOLD    = 3
    FILL_CONFIRM_RETRIES = 3

    def __init__(self, executor, signal_engine, ce_state, pe_state, strategy_id):
        self.executor      = executor
        self.signal_engine = signal_engine
        self.ce_state      = ce_state
        self.pe_state      = pe_state
        self.strategy_id   = strategy_id
        self._running      = False

        self._missing_counts: dict = {}
        self._pending_fill:   dict = {}

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="GTTMonitor")
        t.start()
        write_audit_log(f"[GTT_MONITOR] Started STRATEGY={self.strategy_id}")

    def stop(self):
        self._running = False

    # --------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------

    def _loop(self):
        while self._running:
            try:
                self._check_all()
            except Exception as e:
                write_audit_log(f"[GTT_MONITOR][ERROR] {e}")
            time.sleep(self.POLL_INTERVAL)

    def _check_all(self):
        for side in ("CE", "PE"):
            state = self.ce_state if side == "CE" else self.pe_state
            if not state or not state.in_trade:
                continue

            if state.active_trade and state.active_trade.gtt_id:
                self._check_gtt(
                    side=side, state=state,
                    gtt_id=state.active_trade.gtt_id,
                    leg=1,
                )

            # Re-check in_trade in case leg1 just cleared everything
            if state.in_trade and state.active_trade_leg2 and state.active_trade_leg2.gtt_id:
                self._check_gtt(
                    side=side, state=state,
                    gtt_id=state.active_trade_leg2.gtt_id,
                    leg=2,
                )

    # --------------------------------------------------
    # CHECK ONE GTT
    # --------------------------------------------------

    def _check_gtt(self, side: str, state, gtt_id: str, leg: int):

        # STEP 1: Fetch GTT list
        try:
            kite = self.executor._kite()
            if not kite:
                return
            gtts = kite.get_gtts()
        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR][FETCH_FAIL] Network error — trade NOT closed. "
                f"side={side} leg={leg} GTT_ID={gtt_id} ERR={e}"
            )
            return

        # STEP 2: Find our GTT
        gtt = next(
            (g for g in gtts if str(g.get("id")) == str(gtt_id)),
            None,
        )

        if gtt is not None:
            if gtt_id in self._missing_counts:
                write_audit_log(
                    f"[GTT_MONITOR] GTT_ID={gtt_id} found again after "
                    f"{self._missing_counts[gtt_id]} miss(es) — resetting counter"
                )
            self._missing_counts.pop(gtt_id, None)

            status = gtt.get("status", "")
            if status not in ("triggered", "disabled"):
                self._pending_fill.pop(gtt_id, None)
                return

            trade_obj = state.active_trade if leg == 1 else state.active_trade_leg2
            if not trade_obj:
                write_audit_log(
                    f"[GTT_MONITOR] GTT_ID={gtt_id} triggered but leg{leg} "
                    f"trade object is gone — skipping"
                )
                return

            symbol = trade_obj.symbol
            orders = gtt.get("orders", [])

            exit_reason, exit_price, exit_order_id = self._resolve_exit(
                gtt=gtt, orders=orders, kite=kite,
                symbol=symbol, trade_obj=trade_obj,
            )

            # Fill confirmation guard
            if exit_order_id is None:
                retry = self._pending_fill.get(gtt_id, 0) + 1
                self._pending_fill[gtt_id] = retry
                write_audit_log(
                    f"[GTT_MONITOR] GTT_ID={gtt_id} triggered but fill not confirmed. "
                    f"side={side} leg={leg} retry={retry}/{self.FILL_CONFIRM_RETRIES}"
                )
                if retry < self.FILL_CONFIRM_RETRIES:
                    return
                write_audit_log(
                    f"[GTT_MONITOR] Fill confirmation exhausted for GTT_ID={gtt_id}. "
                    f"Closing as BROKER_EXIT."
                )
                exit_reason = "BROKER_EXIT"
            else:
                write_audit_log(
                    f"[GTT_MONITOR] GTT CONFIRMED TRIGGERED "
                    f"GTT_ID={gtt_id} side={side} leg={leg} "
                    f"reason={exit_reason} price={exit_price} order_id={exit_order_id}"
                )

            self._handle_triggered(
                side=side, state=state, leg=leg,
                exit_reason=exit_reason,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
            )
            self._missing_counts.pop(gtt_id, None)
            self._pending_fill.pop(gtt_id, None)
            return

        # STEP 3: GTT not found (clean fetch)
        self._pending_fill.pop(gtt_id, None)

        count = self._missing_counts.get(gtt_id, 0) + 1
        self._missing_counts[gtt_id] = count

        write_audit_log(
            f"[GTT_MONITOR] GTT_ID={gtt_id} not in broker list "
            f"side={side} leg={leg} "
            f"consecutive_clean_misses={count}/{self.MISSING_THRESHOLD}"
        )

        if count < self.MISSING_THRESHOLD:
            return

        # STEP 4: Threshold reached — verify broker position
        trade_obj = state.active_trade if leg == 1 else state.active_trade_leg2
        if not trade_obj:
            self._missing_counts.pop(gtt_id, None)
            return

        symbol = trade_obj.symbol

        try:
            positions = self.executor.get_open_positions()

            broker_qty = sum(
                abs(p.get("quantity", 0))
                for p in positions
                if p.get("tradingsymbol") == symbol and p.get("quantity", 0) != 0
            )

            expected_remaining = (
                (state.active_trade.qty      if state.active_trade      else 0) +
                (state.active_trade_leg2.qty if state.active_trade_leg2 else 0)
            )

            if broker_qty >= expected_remaining and expected_remaining > 0:
                write_audit_log(
                    f"[GTT_MONITOR] SAFETY OVERRIDE: Position still open "
                    f"broker_qty={broker_qty} expected={expected_remaining} "
                    f"for {symbol}. Resetting miss count."
                )
                self._missing_counts.pop(gtt_id, None)
                return

            # BUG2 FIX: Partial position — leg1 gone but leg2 still at broker
            if broker_qty > 0 and leg == 1 and state.active_trade_leg2:
                leg2_qty = state.active_trade_leg2.qty
                if broker_qty == leg2_qty:
                    write_audit_log(
                        f"[GTT_MONITOR] PARTIAL: leg1 GTT gone, leg2 still open "
                        f"broker_qty={broker_qty} for {symbol}. "
                        f"Closing leg1 in DB only — NOT disturbing leg2."
                    )
                    actual_fill, actual_order_id = self._fetch_fill_from_orders(kite, symbol)
                    self._close_leg1_keep_leg2(
                        side=side, state=state,
                        exit_price=actual_fill,
                        exit_order_id=actual_order_id,
                        kite=kite,
                    )
                    self._missing_counts.pop(gtt_id, None)
                    return

        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR] Position verification failed: {e}. "
                f"Trade NOT closed. Resetting miss count."
            )
            self._missing_counts.pop(gtt_id, None)
            return

        # Both GTT missing AND position gone — close this leg
        write_audit_log(
            f"[GTT_MONITOR] GTT_ID={gtt_id} confirmed missing ({count}x) "
            f"AND broker position gone for {symbol}. "
            f"Closing leg{leg} as BROKER_EXIT. side={side}"
        )

        actual_fill_price, actual_fill_order_id = self._fetch_fill_from_orders(kite, symbol)
        self._handle_triggered(
            side=side, state=state, leg=leg,
            exit_reason="BROKER_EXIT",
            exit_price=actual_fill_price,
            exit_order_id=actual_fill_order_id,
        )
        self._missing_counts.pop(gtt_id, None)

    # --------------------------------------------------
    # BUG2 FIX: Close leg1 in DB without touching leg2
    # --------------------------------------------------

    def _close_leg1_keep_leg2(
        self,
        side:          str,
        state,
        exit_price,
        exit_order_id=None,
        kite=None,
    ):
        """
        Closes leg1 DB row and calls clear_trade_leg1().
        Does NOT cancel GTT2, sell leg2, or clear signal_engine.
        """
        leg1 = state.active_trade
        if not leg1:
            return

        symbol   = leg1.symbol
        trade_id = leg1.trade_id
        gtt_id   = leg1.gtt_id

        if not exit_price:
            exit_price = LTPStore.get(symbol)

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id or str(gtt_id),
                exit_reason="BROKER_EXIT",
            )
            write_audit_log(
                f"[GTT_MONITOR][PARTIAL_CLOSE] Leg1 closed in DB only. "
                f"trade_id={trade_id} exit={exit_price} side={side}"
            )
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR][PARTIAL_CLOSE_FAIL] trade_id={trade_id} ERR={e}")
            return

        state.clear_trade_leg1()

        # Apply trailing SL now that leg1 is confirmed closed
        cfg = load_strategy_config(self.strategy_id)
        if cfg.get("trailing_sl", False) and state.active_trade_leg2:
            self._apply_trailing_sl(side=side, state=state, exit_price=exit_price, kite=kite)

        self._write_exit_signal_to_chart(side=side, exit_reason="BROKER_EXIT")
        self._send_telegram(
            trade_id=trade_id, trade_obj=leg1,
            side=side, leg=1,
            exit_price=exit_price,
            exit_reason="BROKER_EXIT",
        )

    # --------------------------------------------------
    # HANDLE TRIGGERED
    # --------------------------------------------------

    def _handle_triggered(
        self,
        side:          str,
        state,
        leg:           int,
        exit_reason:   str,
        exit_price,
        exit_order_id=None,
    ):
        trade_obj = state.active_trade if leg == 1 else state.active_trade_leg2
        if not trade_obj:
            write_audit_log(
                f"[GTT_MONITOR] _handle_triggered: leg{leg} trade_obj is None — skipping"
            )
            return

        symbol   = trade_obj.symbol
        trade_id = trade_obj.trade_id
        gtt_id   = trade_obj.gtt_id

        # Resolve exit price if still missing
        if not exit_price:
            try:
                kite = self.executor._kite()
                if kite:
                    fill_price, fill_order_id = self._fetch_fill_from_orders(kite, symbol)
                    if fill_price is not None:
                        exit_price    = fill_price
                        exit_order_id = exit_order_id or fill_order_id
            except Exception as e:
                write_audit_log(f"[GTT_MONITOR] Fill lookup failed: {e}")

            if not exit_price:
                exit_price = LTPStore.get(symbol)

        # Write this leg to DB
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id or str(gtt_id),
                exit_reason=exit_reason,
            )
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR][CLOSE_FAIL] trade_id={trade_id} ERR={e}")
            return

        write_audit_log(
            f"[GTT_MONITOR][TRADE_CLOSED] "
            f"STRATEGY={self.strategy_id} side={side} leg={leg} "
            f"trade_id={trade_id} reason={exit_reason} exit={exit_price}"
        )

        self._write_exit_signal_to_chart(side=side, exit_reason=exit_reason)

        is_sl = exit_reason in ("GTT_SL", "BROKER_EXIT")

        if leg == 2:
            # Leg 2 closed — full trade done
            state.clear_trade()
            self.signal_engine.notify_exit(side)
            write_audit_log(f"[GTT_MONITOR] Leg2 closed → full trade done. side={side}")

        elif leg == 1 and is_sl:
            # Leg 1 SL — force-close leg 2 immediately
            write_audit_log(f"[GTT_MONITOR] Leg1 SL hit → force-closing leg2. side={side}")

            leg2 = state.active_trade_leg2
            if leg2:
                if leg2.gtt_id:
                    try:
                        self.executor.cancel_gtt(leg2.gtt_id)
                        write_audit_log(f"[GTT_MONITOR] Cancelled leg2 GTT={leg2.gtt_id}")
                    except Exception as e:
                        write_audit_log(
                            f"[GTT_MONITOR] Leg2 GTT cancel failed: {e} — placing sell anyway"
                        )
                try:
                    self.executor.place_market_sell(symbol=symbol, qty=leg2.qty)
                    write_audit_log(
                        f"[GTT_MONITOR] Market sell placed for leg2 qty={leg2.qty}"
                    )
                    close_trade(
                        trade_id=leg2.trade_id,
                        exit_price=exit_price,
                        exit_order_id=None,
                        exit_reason="GTT_SL",
                    )
                    self._send_telegram(
                        trade_id=leg2.trade_id, trade_obj=leg2,
                        side=side, leg=2,
                        exit_price=exit_price,
                        exit_reason="GTT_SL",
                    )
                except Exception as e:
                    write_audit_log(f"[GTT_MONITOR] Leg2 force-close failed: {e}")

            state.clear_trade()
            self.signal_engine.notify_exit(side)

        elif leg == 1 and not is_sl:
            # Leg 1 TP hit
            state.clear_trade_leg1()

            # BUG1 FIX: notify signal_engine only in single-target mode
            if state.active_trade_leg2 is None:
                # Single-target: full trade done
                self.signal_engine.notify_exit(side)
                write_audit_log(
                    f"[GTT_MONITOR] Single-target TP → full trade done. side={side}"
                )
            else:
                # Multi-target: leg2 still running
                write_audit_log(
                    f"[GTT_MONITOR] Multi-target Leg1 TP → partial profit booked. "
                    f"Leg2 still running. side={side}"
                )
                cfg = load_strategy_config(self.strategy_id)
                if cfg.get("trailing_sl", False):
                    try:
                        kite = self.executor._kite()
                    except Exception:
                        kite = None
                    self._apply_trailing_sl(
                        side=side, state=state,
                        exit_price=exit_price, kite=kite,
                    )

        self._send_telegram(
            trade_id=trade_id, trade_obj=trade_obj,
            side=side, leg=leg,
            exit_price=exit_price,
            exit_reason=exit_reason,
        )

    # --------------------------------------------------
    # BUG3 FIX: TRAILING SL — place-first, cancel-after
    # --------------------------------------------------

    def _apply_trailing_sl(self, side: str, state, exit_price, kite=None):
        """
        Replace GTT2 with a new GTT where SL = entry_price (breakeven).
        TP2 is kept intact.

        SAFETY ORDER:
          1. Fetch fresh REST LTP for the option (not LTPStore — may be stale)
          2. Validate: if current LTP ≤ breakeven_sl, skip (trade already at loss)
          3. Validate: Zerodha requires |last_price - trigger| > 0.25%
          4. PLACE new GTT first — if this fails, old GTT remains, leg2 stays protected
          5. Only CANCEL old GTT after new one is confirmed
          6. Update state with new gtt_id
        """
        leg2 = state.active_trade_leg2
        if not leg2:
            return

        entry_price = leg2.entry_price
        old_gtt_id  = leg2.gtt_id
        symbol      = leg2.symbol
        qty         = leg2.qty

        # Breakeven SL = entry price rounded to NFO tick (0.05)
        breakeven_sl = round(round(entry_price / 0.05) * 0.05, 2)

        if breakeven_sl <= 0:
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] Invalid breakeven "
                f"(entry_price={entry_price}) — skipping"
            )
            return

        # --------------------------------------------------
        # Step 1: Fetch FRESH REST LTP for the option.
        # LTPStore holds the last WS tick which may be stale
        # (e.g. still near entry price). Zerodha needs the
        # actual current market price as last_price parameter.
        # --------------------------------------------------
        current_ltp = self._fetch_option_ltp_rest(symbol=symbol, kite=kite)

        if current_ltp is None or current_ltp <= 0:
            # REST failed — fall back to LTPStore
            current_ltp = LTPStore.get(symbol)
            if current_ltp:
                write_audit_log(
                    f"[GTT_MONITOR][TRAILING_SL] REST LTP unavailable — "
                    f"using LTPStore ltp={current_ltp} for {symbol}"
                )

        if not current_ltp or current_ltp <= 0:
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] Cannot determine current LTP for "
                f"{symbol} — leaving original GTT in place (protected)"
            )
            return

        # --------------------------------------------------
        # Step 2: Guard — if LTP ≤ breakeven_sl the trade is
        # already at or below entry. Trailing SL makes no
        # sense here; leave original GTT2 in place.
        # --------------------------------------------------
        if current_ltp <= breakeven_sl:
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] LTP={current_ltp:.2f} ≤ "
                f"breakeven={breakeven_sl:.2f} — trade at loss, "
                f"keeping original GTT (protected)"
            )
            return

        # --------------------------------------------------
        # Step 3: Zerodha requires |last_price - trigger| > 0.25%.
        # Check before attempting placement to give a clear log
        # rather than a cryptic 500 error.
        # --------------------------------------------------
        min_gap = current_ltp * 0.0025
        if (current_ltp - breakeven_sl) <= min_gap:
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] Gap too small — "
                f"current_ltp={current_ltp:.2f} breakeven_sl={breakeven_sl:.2f} "
                f"gap={current_ltp - breakeven_sl:.2f} required>{min_gap:.2f}. "
                f"Keeping original GTT (protected)"
            )
            return

        # --------------------------------------------------
        # Step 4: PLACE NEW GTT FIRST.
        # If placement fails for any reason, old GTT remains
        # active and leg2 stays protected. We do NOT cancel
        # the old GTT until we have confirmed the new one.
        # --------------------------------------------------
        new_gtt_id = None
        try:
            new_gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=qty,
                sl_price=breakeven_sl,
                tp_price=leg2.tp_price,
                last_price=current_ltp,
            )
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] New GTT placed successfully — "
                f"symbol={symbol} sl_breakeven={breakeven_sl:.2f} "
                f"tp2={leg2.tp_price:.2f} ltp={current_ltp:.2f} "
                f"new_gtt_id={new_gtt_id}"
            )
        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] New GTT placement FAILED — "
                f"symbol={symbol} breakeven_sl={breakeven_sl:.2f} "
                f"ltp={current_ltp:.2f} ERR={e}. "
                f"Original GTT2={old_gtt_id} remains active — leg2 PROTECTED."
            )
            return  # Old GTT still active — do NOT cancel

        # --------------------------------------------------
        # Step 5: CANCEL OLD GTT only after new one confirmed.
        # A failure here is non-fatal — both GTTs briefly
        # exist but Zerodha OCO prevents double execution.
        # --------------------------------------------------
        if old_gtt_id:
            try:
                self.executor.cancel_gtt(old_gtt_id)
                write_audit_log(
                    f"[GTT_MONITOR][TRAILING_SL] Old GTT2={old_gtt_id} cancelled "
                    f"after new GTT={new_gtt_id} confirmed"
                )
            except Exception as e:
                write_audit_log(
                    f"[GTT_MONITOR][TRAILING_SL] Old GTT cancel failed "
                    f"(non-fatal — both GTTs active briefly): {e}"
                )

        # --------------------------------------------------
        # Step 6: Update state only after new GTT is placed.
        # --------------------------------------------------
        state.update_leg2_gtt(
            new_gtt_id=new_gtt_id,
            new_sl_price=breakeven_sl,
        )

    # --------------------------------------------------
    # REST LTP FETCH FOR OPTION
    # --------------------------------------------------

    def _fetch_option_ltp_rest(self, symbol: str, kite=None) -> float:
        """
        Fetch fresh REST LTP for an NFO option symbol.
        Returns float or None on failure.
        """
        try:
            if kite is None:
                kite = self.executor._kite()
            if not kite:
                return None
            quote = kite.ltp(f"NFO:{symbol}")
            ltp   = quote.get(f"NFO:{symbol}", {}).get("last_price")
            if ltp and ltp > 0:
                write_audit_log(
                    f"[GTT_MONITOR][TRAILING_SL] REST LTP for {symbol} = {ltp:.2f}"
                )
                return float(ltp)
        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR][TRAILING_SL] REST LTP fetch failed "
                f"symbol={symbol} ERR={e}"
            )
        return None

    # --------------------------------------------------
    # EXIT RESOLUTION
    # --------------------------------------------------

    def _resolve_exit(self, gtt, orders, kite, symbol, trade_obj) -> tuple:
        """Returns (exit_reason, exit_price, exit_order_id)."""

        exit_reason   = None
        exit_price    = None
        exit_order_id = None
        triggered_idx = None

        for i, order in enumerate(orders):
            result = order.get("result") or {}
            if result.get("order_id"):
                triggered_idx = i
                raw_price     = result.get("average_price")
                exit_price    = float(raw_price) if raw_price else None
                exit_order_id = result.get("order_id")
                break

        if triggered_idx == 0:
            exit_reason = "GTT_SL"
        elif triggered_idx == 1:
            exit_reason = "GTT_TP"

        if exit_order_id is None:
            write_audit_log(
                f"[GTT_MONITOR] GTT result not yet populated for {symbol} — "
                f"falling back to kite.orders()"
            )
            fb_price, fb_order_id = self._fetch_fill_from_orders(kite, symbol)
            if fb_price is not None:
                exit_price    = fb_price
                exit_order_id = fb_order_id

        if exit_reason is None:
            exit_reason = self._infer_reason(
                gtt=gtt, exit_price=exit_price, trade_obj=trade_obj,
            )

        if exit_price is None and symbol:
            ltp = LTPStore.get(symbol)
            if ltp:
                exit_price = ltp

        write_audit_log(
            f"[GTT_MONITOR] Resolved: symbol={symbol} "
            f"reason={exit_reason} price={exit_price} order_id={exit_order_id}"
        )

        return exit_reason or "BROKER_EXIT", exit_price, exit_order_id

    def _fetch_fill_from_orders(self, kite, symbol: str) -> tuple:
        try:
            broker_orders   = kite.orders()
            completed_sells = [
                o for o in broker_orders
                if o.get("tradingsymbol") == symbol
                and o.get("transaction_type") == "SELL"
                and o.get("status") == "COMPLETE"
            ]
            if not completed_sells:
                return None, None
            completed_sells.sort(
                key=lambda x: x.get("exchange_timestamp") or "",
                reverse=True,
            )
            best = completed_sells[0]
            avg  = best.get("average_price")
            oid  = best.get("order_id")
            return (float(avg) if avg else None), oid
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR] kite.orders() fallback failed: {e}")
            return None, None

    def _infer_reason(self, gtt: dict, exit_price: float, trade_obj) -> str:
        trigger_values = gtt.get("trigger_values") or []

        if exit_price and len(trigger_values) == 2:
            dist_sl = abs(exit_price - trigger_values[0])
            dist_tp = abs(exit_price - trigger_values[1])
            reason  = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            write_audit_log(
                f"[GTT_MONITOR] Reason inferred from fill vs triggers: "
                f"fill={exit_price} → {reason}"
            )
            return reason

        if exit_price and trade_obj:
            sl_price = trade_obj.sl_price
            tp_price = trade_obj.tp_price
            if sl_price and tp_price:
                dist_sl = abs(exit_price - sl_price)
                dist_tp = abs(exit_price - tp_price)
                return "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            if sl_price and exit_price <= sl_price * 1.02:
                return "GTT_SL"

        if len(trigger_values) == 2:
            condition  = gtt.get("condition") or {}
            last_price = condition.get("last_price") or 0
            dist_sl    = abs(last_price - trigger_values[0])
            dist_tp    = abs(last_price - trigger_values[1])
            return "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"

        if len(trigger_values) == 1:
            return "GTT_SL"

        return "BROKER_EXIT"

    # --------------------------------------------------
    # CHART MARKER
    # --------------------------------------------------

    def _write_exit_signal_to_chart(self, side: str, exit_reason: str):
        try:
            from app.core.engine_registry import BB_ENGINE_REGISTRY
            from app.db.futures_candles_repo import insert_candle

            if not BB_ENGINE_REGISTRY:
                return

            engine     = BB_ENGINE_REGISTRY[0]
            fut_symbol = engine.fut_symbol
            now        = int(time.time())
            bucket_ts  = (now // 180) * 180
            ltp        = LTPStore.get(fut_symbol) or 0.0

            insert_candle(
                symbol=fut_symbol, timeframe="3m", ts=bucket_ts,
                open_=ltp, high=ltp, low=ltp, close=ltp,
                indicators=None,
                signal_action=f"EXIT_{side}",
                signal_reason=exit_reason,
            )
        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR] Chart exit signal write failed (non-fatal): {e}"
            )

    # --------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------

    def _send_telegram(
        self,
        trade_id:    str,
        trade_obj,
        side:        str,
        leg:         int,
        exit_price,
        exit_reason: str,
    ):
        try:
            from app.api.telegram_api import notify_tp_exit, notify_sl_exit, notify_manual_exit
            db_trade    = get_trade_by_id(trade_id)
            entry_price = db_trade.get("entry_price") if db_trade else None
            qty         = trade_obj.qty
            safe_exit   = exit_price if exit_price is not None else entry_price
            pnl = (
                (safe_exit - entry_price) * qty
                if safe_exit is not None and entry_price is not None
                else None
            )
            payload = {
                "strategy_id":  self.strategy_id,
                "mode":         "live",
                "symbol":       trade_obj.symbol,
                "side":         side,
                "leg":          leg,
                "qty":          qty,
                "entry_price":  entry_price,
                "exit_price":   safe_exit,
                "pnl":          pnl,
            }
            if exit_reason == "GTT_TP":
                notify_tp_exit(payload)
            elif exit_reason == "GTT_SL":
                notify_sl_exit(payload)
            else:
                payload["exit_reason"] = exit_reason
                notify_manual_exit(payload)
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR][TELEGRAM_FAIL] {e}")