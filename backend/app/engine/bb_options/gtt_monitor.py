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
      3. Closes the trade in DB with the correct exit_reason
      4. Clears state.in_trade on the BBTradeStateManager
      5. Clears signal_engine.ce_in_trade / pe_in_trade
      6. Fires a Telegram notification

    CRITICAL SAFETY RULE:
    A network error on GTT fetch must NEVER close a trade.
    Only a confirmed "triggered" or "disabled" GTT status,
    or a verified broker position absence, may close a trade.
    """

    POLL_INTERVAL = 30  # seconds

    # How many consecutive times the GTT can be "missing" from the broker
    # list before we treat it as actually triggered. A single network error
    # or transient empty list is NOT enough — we require this many
    # consecutive clean (non-error) fetches where the GTT is absent,
    # PLUS a broker position check confirming the position is gone.
    MISSING_THRESHOLD = 3

    def __init__(self, executor, signal_engine, ce_state, pe_state, strategy_id):
        self.executor     = executor
        self.signal_engine = signal_engine
        self.ce_state     = ce_state
        self.pe_state     = pe_state
        self.strategy_id  = strategy_id
        self._running     = False

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
        - GTT found + status triggered  → close trade immediately (confirmed)
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
            # NETWORK ERROR — this is the fix for the original bug.
            # The old code called executor.get_gtts() which swallowed
            # the exception and returned [], making the GTT appear
            # "missing" and closing the trade immediately.
            write_audit_log(
                f"[GTT_MONITOR][FETCH_FAIL] Network error fetching GTTs — "
                f"trade NOT closed. side={side} GTT_ID={gtt_id} ERR={e}"
            )
            # Do NOT increment missing count on network errors.
            # The GTT may still be perfectly live at the broker.
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
            # GTT confirmed triggered/disabled by Zerodha
            # --------------------------------------------------
            orders = gtt.get("orders", [])
            exit_reason, exit_price, exit_order_id = self._resolve_exit(gtt, orders)

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
        # STEP 3: GTT not found in the broker list.
        # This can be a transient incomplete response or a genuine trigger.
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
        # This is the final safety net against false-positive closes.
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
                # Position is live at broker — the GTT list was incomplete.
                # This is the false-positive scenario from the original bug.
                write_audit_log(
                    f"[GTT_MONITOR] SAFETY OVERRIDE: Position still open at "
                    f"broker for {symbol}. GTT list was incomplete. "
                    f"Resetting miss count. Trade NOT closed."
                )
                self._missing_counts.pop(gtt_id, None)
                return

        except Exception as e:
            # Position check itself failed — do NOT close trade.
            # Safety-first: a missed close is recoverable; a false close is not.
            write_audit_log(
                f"[GTT_MONITOR] Position verification failed: {e}. "
                f"Trade NOT closed (safety first). Resetting miss count."
            )
            self._missing_counts.pop(gtt_id, None)
            return

        # --------------------------------------------------
        # Both GTT missing (3× confirmed) AND position gone at broker.
        # Safe to treat as triggered/exited.
        # --------------------------------------------------
        write_audit_log(
            f"[GTT_MONITOR] GTT_ID={gtt_id} confirmed missing ({count}×) "
            f"AND broker position gone for {symbol}. "
            f"Closing as BROKER_EXIT. side={side}"
        )
        self._handle_triggered(side, state, "BROKER_EXIT", exit_price=None)
        self._missing_counts.pop(gtt_id, None)

    # --------------------------------------------------
    # Zerodha OCO layout:
    #   orders[0] = SL leg   (lower trigger)
    #   orders[1] = TP leg   (upper trigger)
    # A triggered leg has result.order_id set.
    # --------------------------------------------------

    def _resolve_exit(self, gtt, orders):
        exit_reason   = "BROKER_EXIT"
        exit_price    = None
        exit_order_id = None
        triggered_idx = None

        for i, order in enumerate(orders):
            result = order.get("result") or {}
            if result.get("order_id"):
                triggered_idx = i
                exit_price    = result.get("average_price") or None
                exit_order_id = result.get("order_id")
                break

        if triggered_idx == 0:
            exit_reason = "GTT_SL"
        elif triggered_idx == 1:
            exit_reason = "GTT_TP"
        else:
            # No result yet — fallback: proximity heuristic for OCO
            trigger_values = gtt.get("trigger_values", [])
            condition      = gtt.get("condition", {})
            last_price     = condition.get("last_price", 0)
            if len(trigger_values) >= 2:
                dist_sl = abs(last_price - trigger_values[0])
                dist_tp = abs(last_price - trigger_values[1])
                exit_reason = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            elif len(trigger_values) == 1:
                exit_reason = "GTT_SL"

        return exit_reason, exit_price, exit_order_id

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

        if not exit_price:
            exit_price = LTPStore.get(symbol)

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
            from app.api.telegram_api import notify_manual_exit
            db_trade    = get_trade_by_id(trade_id)
            entry_price = db_trade.get("entry_price") if db_trade else None
            qty         = trade.qty
            safe_exit   = exit_price if exit_price is not None else entry_price
            pnl = (
                (safe_exit - entry_price) * qty
                if safe_exit is not None and entry_price is not None
                else None
            )
            notify_manual_exit({
                "strategy_id": self.strategy_id,
                "mode":        "live",
                "symbol":      symbol,
                "side":        side,
                "entry_price": entry_price,
                "exit_price":  safe_exit,
                "exit_reason": exit_reason,
                "pnl":         pnl,
            })
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR][TELEGRAM_FAIL] {e}")