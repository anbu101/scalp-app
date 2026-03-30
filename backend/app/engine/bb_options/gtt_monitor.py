# backend/app/engine/bb_options/gtt_monitor.py

import time
import threading
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import close_trade, get_trade_by_id
from app.marketdata.ltp_store import LTPStore


class GTTMonitor:
    """
    Polls Zerodha GTT status every POLL_INTERVAL seconds for each
    active BB trade. When a GTT fires it:
      1. Resolves SL_HIT vs TP_HIT from OCO order index
      2. Extracts the actual fill price from the triggered order result
         — with fallback to kite.orders() when GTT result is unpopulated
      3. Closes the trade in DB with the correct exit_reason
      4. Clears state.in_trade on the BBTradeStateManager
      5. Clears signal_engine.ce_in_trade / pe_in_trade
      6. Fires a Telegram notification

    CRITICAL SAFETY RULE:
    A network error on GTT fetch must NEVER close a trade.
    Only a confirmed "triggered" or "disabled" GTT status,
    or a verified broker position absence (after MISSING_THRESHOLD
    consecutive clean misses), may close a trade.
    """

    POLL_INTERVAL = 30  # seconds

    # How many consecutive times the GTT can be "missing" from the broker
    # list before we treat it as actually triggered. A single network error
    # or transient empty list is NOT enough — we require this many
    # consecutive clean (non-error) fetches where the GTT is absent,
    # PLUS a broker position check confirming the position is gone.
    MISSING_THRESHOLD = 3

    def __init__(self, executor, signal_engine, ce_state, pe_state, strategy_id):
        self.executor      = executor
        self.signal_engine = signal_engine
        self.ce_state      = ce_state
        self.pe_state      = pe_state
        self.strategy_id   = strategy_id
        self._running      = False

        # Track consecutive "GTT missing from list" counts per gtt_id.
        # Only incremented on clean fetches where the GTT is absent —
        # network errors do NOT increment this counter.
        self._missing_counts: dict = {}

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
                # No GTT placed (e.g. SL/TP were 0) — nothing to monitor
                continue
            self._check_gtt(side, state, gtt_id)

    def _check_gtt(self, side: str, state, gtt_id: str):
        """
        Safe GTT check with consecutive-miss guard.

        Decision tree:
        - Network / API error on fetch  → log WARN, return (NO trade action)
        - GTT found + status live       → reset miss count, no action
        - GTT found + status triggered  → resolve exit and close trade
        - GTT not found, clean fetch    → increment miss count
            - count < MISSING_THRESHOLD → wait for more evidence
            - count >= MISSING_THRESHOLD → verify broker position:
                - position still open   → broker list was incomplete, reset counter
                - position gone         → safe to close as BROKER_EXIT
                - position check fails  → reset counter (safety first)
        """

        # --------------------------------------------------
        # STEP 1: Fetch GTTs directly from broker kite object
        # so we get explicit exception propagation instead of
        # the silent [] that executor.get_gtts() returns on error.
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
            # NETWORK ERROR — do NOT close trade, do NOT increment miss count.
            # The GTT may still be perfectly live at the broker.
            write_audit_log(
                f"[GTT_MONITOR][FETCH_FAIL] Network error fetching GTTs — "
                f"trade NOT closed. side={side} GTT_ID={gtt_id} ERR={e}"
            )
            return

        # --------------------------------------------------
        # STEP 2: Search for our GTT in the returned list
        # --------------------------------------------------
        gtt = next(
            (g for g in gtts if str(g.get("id")) == str(gtt_id)),
            None
        )

        if gtt is not None:
            # GTT found — reset consecutive miss counter
            if gtt_id in self._missing_counts:
                write_audit_log(
                    f"[GTT_MONITOR] GTT_ID={gtt_id} found again after "
                    f"{self._missing_counts[gtt_id]} miss(es) — resetting counter"
                )
            self._missing_counts.pop(gtt_id, None)

            status = gtt.get("status", "")

            if status not in ("triggered", "disabled"):
                # GTT is active — nothing to do this cycle
                return

            # --------------------------------------------------
            # GTT confirmed triggered/disabled by Zerodha.
            #
            # Zerodha sets status="triggered" BEFORE populating the
            # child order result fields. _resolve_exit() handles this
            # by falling back to kite.orders() when result is empty.
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

            write_audit_log(
                f"[GTT_MONITOR] GTT CONFIRMED TRIGGERED "
                f"GTT_ID={gtt_id} side={side} status={status} "
                f"reason={exit_reason} price={exit_price} "
                f"order_id={exit_order_id}"
            )

            self._handle_triggered(side, state, exit_reason, exit_price, exit_order_id)
            self._missing_counts.pop(gtt_id, None)
            return

        # --------------------------------------------------
        # STEP 3: GTT not found in the broker list (clean fetch).
        # Apply the consecutive-miss guard before any trade action.
        # --------------------------------------------------
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
        # STEP 4: Threshold reached.
        # Before closing, verify the broker position is actually gone.
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

        # Both GTT missing (N× confirmed) AND position gone at broker.
        write_audit_log(
            f"[GTT_MONITOR] GTT_ID={gtt_id} confirmed missing ({count}×) "
            f"AND broker position gone for {symbol}. "
            f"Closing as BROKER_EXIT. side={side}"
        )
        self._handle_triggered(side, state, "BROKER_EXIT", exit_price=None)
        self._missing_counts.pop(gtt_id, None)

    # --------------------------------------------------
    # EXIT RESOLUTION
    #
    # Zerodha OCO layout:
    #   orders[0] = SL leg   (lower trigger)
    #   orders[1] = TP leg   (upper trigger)
    #
    # A triggered leg has result.order_id set — but Zerodha populates
    # status="triggered" BEFORE filling in the result, so we must
    # handle the case where result is still None/empty.
    #
    # Fallback chain:
    #   1. GTT order result (ideal — has exact fill price)
    #   2. kite.orders() — find the completed SELL for this symbol
    #   3. Trigger-value proximity heuristic for reason (SL vs TP)
    #   4. LTPStore as last-resort price
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

        Priority:
        1. GTT child order result (populated after fill confirmation)
        2. kite.orders() lookup for completed SELL of this symbol
        3. Trigger-value proximity heuristic for reason
        4. LTPStore price as absolute last resort
        """

        exit_reason   = None
        exit_price    = None
        exit_order_id = None
        triggered_idx = None

        # ── Pass 1: GTT child order result ────────────────────────
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

        # ── Pass 2: kite.orders() fallback when result not yet populated ──
        #
        # This handles the race condition where Zerodha marks the GTT
        # as "triggered" before the child order execution details are
        # written back into the GTT's orders[].result field.
        if exit_price is None or exit_order_id is None:
            write_audit_log(
                f"[GTT_MONITOR] GTT result not yet populated for {symbol} — "
                f"falling back to kite.orders() for fill price"
            )
            fb_price, fb_order_id = self._fetch_fill_from_orders(kite, symbol)
            if fb_price is not None:
                exit_price    = fb_price
                exit_order_id = fb_order_id or exit_order_id
                write_audit_log(
                    f"[GTT_MONITOR] Fill resolved via kite.orders(): "
                    f"symbol={symbol} price={exit_price} order_id={exit_order_id}"
                )

        # ── Pass 3: determine reason from trigger-value proximity ──
        #
        # Used when triggered_idx is None (no result populated yet).
        # Compare the actual fill price (or trade's SL level) against
        # the OCO trigger_values to decide which leg fired.
        if exit_reason is None:
            exit_reason = self._infer_reason(
                gtt=gtt,
                exit_price=exit_price,
                state=state,
            )

        # ── Pass 4: price of last resort — LTPStore ────────────────
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
        order matching the symbol. Returns (avg_price, order_id).
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
        Infer GTT_SL vs GTT_TP when the child order result field is
        not yet populated.

        Strategy (in priority order):
        1. Compare fill price against the two OCO trigger values —
           whichever trigger it's closer to is the one that fired.
        2. Compare fill price against trade's stored sl_price:
           if fill <= sl_price + buffer → GTT_SL, else → GTT_TP
        3. Use trigger-value proximity with GTT placement price.
        """
        trigger_values = gtt.get("trigger_values") or []

        # ── Method 1: fill price vs trigger values ─────────────────
        if exit_price and len(trigger_values) == 2:
            sl_trigger = trigger_values[0]
            tp_trigger = trigger_values[1]
            dist_sl = abs(exit_price - sl_trigger)
            dist_tp = abs(exit_price - tp_trigger)
            reason  = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            write_audit_log(
                f"[GTT_MONITOR] Reason inferred from fill vs triggers: "
                f"fill={exit_price} SL_trigger={sl_trigger} "
                f"TP_trigger={tp_trigger} → {reason}"
            )
            return reason

        # ── Method 2: fill price vs stored sl_price ────────────────
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
                write_audit_log(
                    f"[GTT_MONITOR] Reason inferred: fill={exit_price} "
                    f"near sl={sl_price} → GTT_SL"
                )
                return "GTT_SL"

        # ── Method 3: GTT placement price vs trigger proximity ─────
        if len(trigger_values) == 2:
            condition  = gtt.get("condition") or {}
            last_price = condition.get("last_price") or 0
            dist_sl = abs(last_price - trigger_values[0])
            dist_tp = abs(last_price - trigger_values[1])
            reason  = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
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

    def _handle_triggered(
        self,
        side: str,
        state,
        exit_reason: str,
        exit_price,
        exit_order_id=None,
    ):
        trade         = state.active_trade
        symbol        = trade.symbol
        trade_id      = trade.trade_id

        # Final price fallback
        if not exit_price:
            exit_price = LTPStore.get(symbol)
            if exit_price:
                write_audit_log(
                    f"[GTT_MONITOR] Using LTPStore as exit price: "
                    f"symbol={symbol} price={exit_price}"
                )

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

        # ── Clear in-memory state ──────────────────────────────────
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

        # ── Telegram notification ──────────────────────────────────
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