# app/engine/bb_options/gtt_monitor.py

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
    """

    POLL_INTERVAL = 30  # seconds

    def __init__(self, executor, signal_engine, ce_state, pe_state, strategy_id):
        self.executor = executor
        self.signal_engine = signal_engine
        self.ce_state = ce_state
        self.pe_state = pe_state
        self.strategy_id = strategy_id
        self._running = False

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
        try:
            gtts = self.executor.get_gtts()
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR][FETCH_FAIL] side={side} ERR={e}")
            return

        gtt = next((g for g in gtts if str(g.get("id")) == str(gtt_id)), None)

        if gtt is None:
            # GTT vanished from broker — triggered+expired or manually deleted
            write_audit_log(
                f"[GTT_MONITOR] GTT_ID={gtt_id} missing from broker "
                f"side={side} — treating as triggered"
            )
            self._handle_triggered(side, state, "BROKER_EXIT", exit_price=None)
            return

        status = gtt.get("status", "")
        if status not in ("triggered", "disabled"):
            return  # still live, nothing to do

        orders = gtt.get("orders", [])
        exit_reason, exit_price, exit_order_id = self._resolve_exit(gtt, orders)

        write_audit_log(
            f"[GTT_MONITOR] GTT FIRED "
            f"GTT_ID={gtt_id} side={side} status={status} "
            f"reason={exit_reason} price={exit_price} order_id={exit_order_id}"
        )

        self._handle_triggered(side, state, exit_reason, exit_price, exit_order_id)

    # --------------------------------------------------
    # Zerodha OCO layout:
    #   orders[0] = SL leg   (lower trigger)
    #   orders[1] = TP leg   (upper trigger)
    # A triggered leg has result.order_id set.
    # --------------------------------------------------

    def _resolve_exit(self, gtt, orders):
        # DB CHECK constraint: ('TP', 'SL', 'MANUAL', 'BROKER_EXIT', 'GTT_TP', 'GTT_SL')
        exit_reason = "BROKER_EXIT"
        exit_price = None
        exit_order_id = None
        triggered_idx = None

        for i, order in enumerate(orders):
            result = order.get("result") or {}
            if result.get("order_id"):
                triggered_idx = i
                exit_price = result.get("average_price") or None
                exit_order_id = result.get("order_id")
                break

        if triggered_idx == 0:
            # orders[0] is always the SL leg (single or OCO)
            exit_reason = "GTT_SL"
        elif triggered_idx == 1:
            # orders[1] is the TP leg (OCO only)
            exit_reason = "GTT_TP"
        else:
            # No result yet — fallback: proximity heuristic for OCO
            trigger_values = gtt.get("trigger_values", [])
            condition = gtt.get("condition", {})
            last_price = condition.get("last_price", 0)
            if len(trigger_values) >= 2:
                dist_sl = abs(last_price - trigger_values[0])
                dist_tp = abs(last_price - trigger_values[1])
                exit_reason = "GTT_SL" if dist_sl <= dist_tp else "GTT_TP"
            elif len(trigger_values) == 1:
                # Single-leg GTT — always SL
                exit_reason = "GTT_SL"

        return exit_reason, exit_price, exit_order_id

    # --------------------------------------------------

    def _handle_triggered(self, side: str, state, exit_reason: str, exit_price, exit_order_id=None):
        trade = state.active_trade
        symbol = trade.symbol
        trade_id = trade.trade_id

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
            write_audit_log(f"[GTT_MONITOR][CLOSE_FAIL] trade_id={trade_id} ERR={e}")
            return

        # ── Clear state ──────────────────────────────────
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

        # ── Telegram ─────────────────────────────────────
        try:
            from app.api.telegram_api import notify_manual_exit
            db_trade = get_trade_by_id(trade_id)
            entry_price = db_trade.get("entry_price") if db_trade else None
            qty = trade.qty
            # exit_price fallback: use entry if LTP unavailable so pnl is 0 not None
            safe_exit = exit_price if exit_price is not None else entry_price
            pnl = (
                (safe_exit - entry_price) * qty
                if safe_exit is not None and entry_price is not None
                else None
            )
            notify_manual_exit({
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "exit_price": safe_exit,
                "exit_reason": exit_reason,   # "GTT_SL" / "GTT_TP" / "BROKER_EXIT"
                "pnl": pnl,
            })
        except Exception as e:
            write_audit_log(f"[GTT_MONITOR][TELEGRAM_FAIL] {e}")