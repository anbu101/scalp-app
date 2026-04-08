# backend/app/engine/bb_options/gtt_monitor.py
#
# CHANGES vs original:
#
# FIX (Issue 2 — manual exit price):
#   In _handle_triggered(), when exit_price is None (the BROKER_EXIT path
#   reached via MISSING_THRESHOLD), the old code fell straight to
#   LTPStore.get(symbol) which returned the stale selector premium (569).
#   Now we first try _fetch_fill_from_orders() to find the actual completed
#   SELL in kite.orders() (e.g. 713.05), and only fall back to LTPStore
#   if no completed SELL is found.
#
# FIX (Issue 3 — exit not shown on chart):
#   Added _write_exit_signal_to_chart() called from _handle_triggered().
#   Writes EXIT_CE / EXIT_PE into futures_candles at the current 3-minute
#   bucket so BBPanel chart shows ✕ markers for ALL exit types.

import time
import threading
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import close_trade, get_trade_by_id
from app.marketdata.ltp_store import LTPStore


class GTTMonitor:
    """
    Polls Zerodha GTT status every POLL_INTERVAL seconds for each
    active BB trade.

    CRITICAL SAFETY RULES:
    1. A network error on GTT fetch must NEVER close a trade.
    2. GTT status="triggered" alone is NOT sufficient to close a trade —
       Zerodha sets this flag before the child SL/TP order is even sent
       to the exchange.  We must confirm the actual fill via kite.orders()
       before writing to DB.
    3. Only after FILL_CONFIRM_RETRIES failed attempts to find the fill
       do we fall back to BROKER_EXIT with LTPStore price.
    4. Position gone at broker (after MISSING_THRESHOLD clean misses) is
       an additional closure path for edge cases.
    """

    POLL_INTERVAL = 30  # seconds

    # How many consecutive consecutive poll cycles the GTT can be absent
    # before we verify the broker position and possibly close.
    MISSING_THRESHOLD = 3

    # How many poll cycles to wait for the child order fill to appear
    # in kite.orders() after the GTT shows status="triggered".
    # 3 retries x 30s = up to 90 seconds of patience before giving up.
    FILL_CONFIRM_RETRIES = 3

    def __init__(self, executor, signal_engine, ce_state, pe_state, strategy_id):
        self.executor      = executor
        self.signal_engine = signal_engine
        self.ce_state      = ce_state
        self.pe_state      = pe_state
        self.strategy_id   = strategy_id
        self._running      = False

        # Consecutive clean-fetch miss counts per gtt_id.
        # Network errors do NOT increment this.
        self._missing_counts: dict = {}

        # Tracks GTTs that have shown status="triggered" but whose child
        # order fill has not yet appeared in kite.orders().
        # Structure: { gtt_id: retry_count }
        self._pending_fill: dict = {}

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="GTTMonitor")
        t.start()
        write_audit_log(f"[GTT_MONITOR] Started STRATEGY={self.strategy_id}")

    def stop(self):
        self._running = False

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
            if not state or not state.in_trade or not state.active_trade:
                continue
            gtt_id = state.active_trade.gtt_id
            if not gtt_id:
                continue
            self._check_gtt(side, state, gtt_id)

    def _check_gtt(self, side: str, state, gtt_id: str):
        """
        Full decision tree — see class docstring for rules.
        """

        # --------------------------------------------------
        # STEP 1: Fetch GTTs with explicit error propagation.
        # --------------------------------------------------
        try:
            kite = self.executor._kite()
            if not kite:
                write_audit_log(
                    f"[GTT_MONITOR] Broker not ready, skipping poll "
                    f"side={side} GTT_ID={gtt_id}"
                )
                return

            gtts = kite.get_gtts()

        except Exception as e:
            # Network error — GTT may still be live. Never close.
            write_audit_log(
                f"[GTT_MONITOR][FETCH_FAIL] Network error — "
                f"trade NOT closed. side={side} GTT_ID={gtt_id} ERR={e}"
            )
            return

        # --------------------------------------------------
        # STEP 2: Find our GTT in the returned list
        # --------------------------------------------------
        gtt = next(
            (g for g in gtts if str(g.get("id")) == str(gtt_id)),
            None
        )

        if gtt is not None:
            # GTT found — reset miss counter
            if gtt_id in self._missing_counts:
                write_audit_log(
                    f"[GTT_MONITOR] GTT_ID={gtt_id} found again after "
                    f"{self._missing_counts[gtt_id]} miss(es) — resetting counter"
                )
            self._missing_counts.pop(gtt_id, None)

            status = gtt.get("status", "")

            if status not in ("triggered", "disabled"):
                # GTT is live — clear any stale pending-fill tracker
                self._pending_fill.pop(gtt_id, None)
                return

            # --------------------------------------------------
            # GTT is triggered/disabled — resolve the fill.
            # --------------------------------------------------
            orders = gtt.get("orders", [])
            symbol = state.active_trade.symbol if state.active_trade else None

            exit_reason, exit_price, exit_order_id = self._resolve_exit(
                gtt=gtt,
                orders=orders,
                kite=kite,
                symbol=symbol,
                state=state,
            )

            # --------------------------------------------------
            # FILL CONFIRMATION GUARD
            # --------------------------------------------------
            if exit_order_id is None:
                retry = self._pending_fill.get(gtt_id, 0) + 1
                self._pending_fill[gtt_id] = retry

                write_audit_log(
                    f"[GTT_MONITOR] GTT_ID={gtt_id} triggered but fill not "
                    f"confirmed yet. side={side} "
                    f"retry={retry}/{self.FILL_CONFIRM_RETRIES}"
                )

                if retry < self.FILL_CONFIRM_RETRIES:
                    write_audit_log(
                        f"[GTT_MONITOR] Waiting for fill confirmation — "
                        f"trade NOT closed yet."
                    )
                    return

                # Exhausted retries — close as BROKER_EXIT rather than
                # leaving the trade open indefinitely.
                write_audit_log(
                    f"[GTT_MONITOR] Fill confirmation exhausted after "
                    f"{retry} retries for GTT_ID={gtt_id}. "
                    f"Closing as BROKER_EXIT with best available price."
                )
                exit_reason = "BROKER_EXIT"

            else:
                write_audit_log(
                    f"[GTT_MONITOR] GTT CONFIRMED TRIGGERED "
                    f"GTT_ID={gtt_id} side={side} status={status} "
                    f"reason={exit_reason} price={exit_price} "
                    f"order_id={exit_order_id}"
                )

            self._handle_triggered(side, state, exit_reason, exit_price, exit_order_id)
            self._missing_counts.pop(gtt_id, None)
            self._pending_fill.pop(gtt_id, None)
            return

        # --------------------------------------------------
        # STEP 3: GTT not found in broker list (clean fetch)
        # --------------------------------------------------
        self._pending_fill.pop(gtt_id, None)

        count = self._missing_counts.get(gtt_id, 0) + 1
        self._missing_counts[gtt_id] = count

        write_audit_log(
            f"[GTT_MONITOR] GTT_ID={gtt_id} not in broker list "
            f"side={side} consecutive_clean_misses={count}/{self.MISSING_THRESHOLD}"
        )

        if count < self.MISSING_THRESHOLD:
            write_audit_log(
                f"[GTT_MONITOR] Below miss threshold — "
                f"waiting for more evidence before any trade action."
            )
            return

        # --------------------------------------------------
        # STEP 4: Threshold reached — verify broker position
        # --------------------------------------------------
        write_audit_log(
            f"[GTT_MONITOR] Miss threshold reached for GTT_ID={gtt_id}. "
            f"Verifying broker position for confirmation..."
        )

        symbol = state.active_trade.symbol if state.active_trade else None

        try:
            positions = self.executor.get_open_positions()

            position_still_open = any(
                p.get("tradingsymbol") == symbol and p.get("quantity", 0) != 0
                for p in positions
            )

            if position_still_open:
                write_audit_log(
                    f"[GTT_MONITOR] SAFETY OVERRIDE: Position still open at "
                    f"broker for {symbol}. GTT list was incomplete. "
                    f"Resetting miss count. Trade NOT closed."
                )
                self._missing_counts.pop(gtt_id, None)
                return

        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR] Position verification failed: {e}. "
                f"Trade NOT closed (safety first). Resetting miss count."
            )
            self._missing_counts.pop(gtt_id, None)
            return

        # Both GTT missing AND position gone — safe to close.
        # Try to find the actual fill price from broker order history
        # before falling back to LTPStore (which may be stale).
        write_audit_log(
            f"[GTT_MONITOR] GTT_ID={gtt_id} confirmed missing ({count}x) "
            f"AND broker position gone for {symbol}. "
            f"Closing as BROKER_EXIT. side={side}"
        )

        # ── FIX (Issue 2): try to find actual fill before using LTPStore ──
        actual_fill_price, actual_fill_order_id = self._fetch_fill_from_orders(
            kite, symbol
        )
        if actual_fill_price is not None:
            write_audit_log(
                f"[GTT_MONITOR] Found actual fill in broker orders: "
                f"symbol={symbol} price={actual_fill_price} "
                f"order_id={actual_fill_order_id}"
            )
            self._handle_triggered(
                side, state, "BROKER_EXIT",
                exit_price=actual_fill_price,
                exit_order_id=actual_fill_order_id,
            )
        else:
            # No completed SELL found — fall back to LTPStore
            self._handle_triggered(side, state, "BROKER_EXIT", exit_price=None)
        # ──────────────────────────────────────────────────────────────────

        self._missing_counts.pop(gtt_id, None)

    # --------------------------------------------------
    # EXIT RESOLUTION
    #
    # Zerodha OCO layout:
    #   orders[0] = SL leg   (lower trigger)
    #   orders[1] = TP leg   (upper trigger)
    #
    # Fallback chain:
    #   1. GTT order result (populated after child order confirmed)
    #   2. kite.orders() completed SELL for this symbol
    #   3. Trigger-value proximity heuristic for reason (SL vs TP)
    #   4. LTPStore price as absolute last resort
    # --------------------------------------------------

    def _resolve_exit(
        self,
        gtt: dict,
        orders: list,
        kite,
        symbol: str,
        state,
    ) -> tuple:
        """
        Returns (exit_reason, exit_price, exit_order_id).
        exit_order_id=None signals "fill not yet confirmed".
        """

        exit_reason   = None
        exit_price    = None
        exit_order_id = None
        triggered_idx = None

        # Pass 1: GTT child order result
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

        # Pass 2: kite.orders() when GTT result not yet populated
        if exit_order_id is None:
            write_audit_log(
                f"[GTT_MONITOR] GTT result not yet populated for {symbol} — "
                f"falling back to kite.orders() for fill price"
            )
            fb_price, fb_order_id = self._fetch_fill_from_orders(kite, symbol)
            if fb_price is not None:
                exit_price    = fb_price
                exit_order_id = fb_order_id
                write_audit_log(
                    f"[GTT_MONITOR] Fill resolved via kite.orders(): "
                    f"symbol={symbol} price={exit_price} order_id={exit_order_id}"
                )

        # Pass 3: infer reason when triggered_idx unknown
        if exit_reason is None:
            exit_reason = self._infer_reason(
                gtt=gtt,
                exit_price=exit_price,
                state=state,
            )

        # Pass 4: LTPStore price as last resort
        if exit_price is None and symbol:
            ltp = LTPStore.get(symbol)
            if ltp:
                exit_price = ltp
                write_audit_log(
                    f"[GTT_MONITOR] Using LTPStore price as last resort: "
                    f"symbol={symbol} ltp={ltp}"
                )

        write_audit_log(
            f"[GTT_MONITOR] Resolved: symbol={symbol} "
            f"reason={exit_reason} price={exit_price} order_id={exit_order_id}"
        )

        return exit_reason or "BROKER_EXIT", exit_price, exit_order_id

    def _fetch_fill_from_orders(self, kite, symbol: str) -> tuple:
        """
        Search kite.orders() for the most recent completed SELL
        order matching the symbol.
        Returns (avg_price, order_id) or (None, None) if not found.
        (None, None) means "fill not yet confirmed" — caller waits.
        """
        try:
            broker_orders = kite.orders()

            completed_sells = [
                o for o in broker_orders
                if o.get("tradingsymbol") == symbol
                and o.get("transaction_type") == "SELL"
                and o.get("status") == "COMPLETE"
            ]

            if not completed_sells:
                write_audit_log(
                    f"[GTT_MONITOR] No completed SELL orders found for {symbol}"
                )
                return None, None

            # Most recent first
            completed_sells.sort(
                key=lambda x: x.get("exchange_timestamp") or "",
                reverse=True,
            )

            best = completed_sells[0]
            avg  = best.get("average_price")
            oid  = best.get("order_id")

            return (float(avg) if avg else None), oid

        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR] kite.orders() fallback failed "
                f"symbol={symbol} ERR={e}"
            )
            return None, None

    def _infer_reason(self, gtt: dict, exit_price: float, state) -> str:
        """
        Infer GTT_SL vs GTT_TP when child order result is unpopulated.
        """
        trigger_values = gtt.get("trigger_values") or []

        # Method 1: fill price vs OCO trigger values
        if exit_price and len(trigger_values) == 2:
            sl_trigger = trigger_values[0]
            tp_trigger = trigger_values[1]
            dist_sl    = abs(exit_price - sl_trigger)
            dist_tp    = abs(exit_price - tp_trigger)
            reason     = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            write_audit_log(
                f"[GTT_MONITOR] Reason inferred from fill vs triggers: "
                f"fill={exit_price} SL_trigger={sl_trigger} "
                f"TP_trigger={tp_trigger} → {reason}"
            )
            return reason

        # Method 2: fill price vs stored trade SL/TP
        if exit_price and state and state.active_trade:
            sl_price = state.active_trade.sl_price
            tp_price = state.active_trade.tp_price
            if sl_price and tp_price:
                dist_sl = abs(exit_price - sl_price)
                dist_tp = abs(exit_price - tp_price)
                reason  = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
                write_audit_log(
                    f"[GTT_MONITOR] Reason inferred from fill vs trade SL/TP: "
                    f"fill={exit_price} sl={sl_price} tp={tp_price} → {reason}"
                )
                return reason
            if sl_price and exit_price <= sl_price * 1.02:
                return "GTT_SL"

        # Method 3: GTT placement price vs trigger proximity
        if len(trigger_values) == 2:
            condition  = gtt.get("condition") or {}
            last_price = condition.get("last_price") or 0
            dist_sl    = abs(last_price - trigger_values[0])
            dist_tp    = abs(last_price - trigger_values[1])
            reason     = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            write_audit_log(
                f"[GTT_MONITOR] Reason inferred from GTT placement price: "
                f"last_price={last_price} → {reason}"
            )
            return reason

        if len(trigger_values) == 1:
            return "GTT_SL"

        write_audit_log(
            "[GTT_MONITOR] Could not infer exit reason — defaulting to BROKER_EXIT"
        )
        return "BROKER_EXIT"

    # --------------------------------------------------
    # HANDLE TRIGGERED
    # --------------------------------------------------

    def _handle_triggered(
        self,
        side: str,
        state,
        exit_reason: str,
        exit_price,
        exit_order_id=None,
    ):
        trade    = state.active_trade
        symbol   = trade.symbol
        trade_id = trade.trade_id

        # ── FIX (Issue 2 — manual exit price) ─────────────────────────
        # Old code: if not exit_price → LTPStore.get(symbol) immediately.
        # Problem: LTPStore may hold a stale pre-session price (569) when
        # the option WS subscription never updated it (e.g. option was
        # outside the ±30 ATM window or WS was briefly disconnected).
        #
        # New code: try kite.orders() FIRST to find the actual completed
        # SELL (e.g. 713.05 from the manual exit), fall back to LTPStore
        # only if no completed SELL is found.
        # ──────────────────────────────────────────────────────────────
        if not exit_price:
            try:
                kite = self.executor._kite()
                if kite:
                    fill_price, fill_order_id = self._fetch_fill_from_orders(kite, symbol)
                    if fill_price is not None:
                        exit_price    = fill_price
                        exit_order_id = exit_order_id or fill_order_id
                        write_audit_log(
                            f"[GTT_MONITOR] BROKER_EXIT fill found via kite.orders(): "
                            f"symbol={symbol} price={exit_price}"
                        )
            except Exception as e:
                write_audit_log(
                    f"[GTT_MONITOR] BROKER_EXIT fill lookup failed: {e}"
                )

            # Final fallback: LTPStore
            if not exit_price:
                exit_price = LTPStore.get(symbol)
                if exit_price:
                    write_audit_log(
                        f"[GTT_MONITOR] Using LTPStore as exit price (last resort): "
                        f"symbol={symbol} price={exit_price}"
                    )
        # ──────────────────────────────────────────────────────────────

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id or str(trade.gtt_id),
                exit_reason=exit_reason,
            )
        except Exception as e:
            write_audit_log(
                f"[GTT_MONITOR][CLOSE_FAIL] trade_id={trade_id} ERR={e}"
            )
            return

        # ── FIX (Issue 3 — exit not on chart) ─────────────────────────
        # Write EXIT_CE / EXIT_PE signal into futures_candles so BBPanel
        # shows a ✕ marker for this candle from the next UI poll cycle.
        self._write_exit_signal_to_chart(side=side, exit_reason=exit_reason)
        # ──────────────────────────────────────────────────────────────

        # Clear in-memory state
        state.clear_trade()

        if side == "CE":
            self.signal_engine.ce_in_trade = False
        else:
            self.signal_engine.pe_in_trade = False

        write_audit_log(
            f"[GTT_MONITOR][TRADE_CLOSED] "
            f"STRATEGY={self.strategy_id} side={side} "
            f"trade_id={trade_id} reason={exit_reason} exit={exit_price}"
        )

        # Telegram notification with correct type
        try:
            from app.api.telegram_api import notify_tp_exit, notify_sl_exit, notify_manual_exit
            db_trade    = get_trade_by_id(trade_id)
            entry_price = db_trade.get("entry_price") if db_trade else None
            qty         = trade.qty
            safe_exit   = exit_price if exit_price is not None else entry_price
            pnl = (
                (safe_exit - entry_price) * qty
                if safe_exit is not None and entry_price is not None
                else None
            )

            payload = {
                "strategy_id": self.strategy_id,
                "mode":        "live",
                "symbol":      symbol,
                "side":        side,
                "entry_price": entry_price,
                "exit_price":  safe_exit,
                "pnl":         pnl,
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

    # --------------------------------------------------
    # WRITE EXIT SIGNAL TO futures_candles (Issue 3 fix)
    #
    # futures_candles.signal_action is what BBPanel reads for markers.
    # SuperTrend exits are written by _process_candle → insert_candle.
    # BROKER_EXIT and GTT exits happen outside candle processing, so we
    # write directly to the current 3-minute bucket here.
    # --------------------------------------------------

    def _write_exit_signal_to_chart(self, side: str, exit_reason: str):
        try:
            from app.core.engine_registry import BB_ENGINE_REGISTRY
            from app.db.futures_candles_repo import insert_candle

            if not BB_ENGINE_REGISTRY:
                return

            engine     = BB_ENGINE_REGISTRY[0]
            fut_symbol = engine.fut_symbol

            # Current 3-minute bucket (same alignment as CandleBuilder)
            now        = int(time.time())
            bucket_ts  = (now // 180) * 180

            signal_action = f"EXIT_{side}"   # EXIT_CE or EXIT_PE

            # Use current FUT LTP as OHLC proxy for this row.
            # The row may already exist (current candle); insert_candle uses
            # ON CONFLICT DO UPDATE so the signal_action column is updated
            # without overwriting existing OHLC data.
            ltp = LTPStore.get(fut_symbol) or 0.0

            insert_candle(
                symbol=fut_symbol,
                timeframe="3m",
                ts=bucket_ts,
                open_=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                indicators=None,
                signal_action=signal_action,
                signal_reason=exit_reason,
            )

            write_audit_log(
                f"[GTT_MONITOR] Chart exit signal written: "
                f"symbol={fut_symbol} ts={bucket_ts} "
                f"signal={signal_action} reason={exit_reason}"
            )

        except Exception as e:
            # Non-fatal — trade is already closed, chart display is secondary
            write_audit_log(
                f"[GTT_MONITOR] Chart exit signal write failed (non-fatal): {e}"
            )