# backend/app/engine/scalp_v2/scalp_v2_gtt_monitor.py
#
# SCALP_V2 — GTT Backstop Monitor
# ============================================================================
# PURPOSE (narrow, deliberately):
#   The tick stream (group_manager.on_tick) is the PRIMARY exit driver and
#   owns the 15s stagger window. This monitor is a SLOW BACKSTOP only. It
#   catches legs whose GTT fired (or whose broker position vanished) but the
#   tick path missed — e.g. stale/absent WS ticks, a WS gap, or an app
#   restart mid-group where in-memory leg state was rebuilt but a fill landed
#   while disconnected.
#
# HARD RULES (mirrors gtt_monitor.py safety doctrine):
#   1. Network error on GTT fetch must NEVER close a leg.
#   2. GTT status="triggered" alone is insufficient — confirm fill.
#   3. MISSING_THRESHOLD consecutive clean misses + position gone = close.
#   4. Position verification always runs before acting on a missing GTT.
#
# OWNERSHIP RULE (critical):
#   This monitor NEVER mutates group/leg state directly and NEVER places or
#   cancels orders for the stagger logic. When it confirms a leg has exited
#   at the broker, it calls group_manager.on_backstop_leg_exit(...) and lets
#   the group manager run its single, authoritative close + finalize path.
#   This prevents double-exits and keeps the FSM in one place.
#
# ISOLATION:
#   Reads only SCALP_V2's own group manager + the shared executor. Touches
#   no other strategy. BB's GTTMonitor is a separate instance; this does not
#   replace or modify it.
# ============================================================================

import time
import threading

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore


class ScalpV2GTTMonitor:

    POLL_INTERVAL        = 30      # slow — tick path is primary
    MISSING_THRESHOLD    = 3
    FILL_CONFIRM_RETRIES = 3

    def __init__(self, executor, group_manager):
        self.executor      = executor
        self.group_manager = group_manager
        self._running      = False

        self._missing_counts: dict = {}   # gtt_id -> consecutive clean misses
        self._pending_fill:   dict = {}   # gtt_id -> fill-confirm retry count

    # --------------------------------------------------
    # LIFECYCLE
    # --------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="ScalpV2GTTMonitor")
        t.start()
        write_audit_log("[V2_GTT_MONITOR] Started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._check_all()
            except Exception as e:
                write_audit_log(f"[V2_GTT_MONITOR][ERROR] {e}")
            time.sleep(self.POLL_INTERVAL)

    # --------------------------------------------------
    # MAIN SWEEP
    # --------------------------------------------------

    def _check_all(self):
        group = self.group_manager.current_group()
        if group is None:
            return

        # Only LIVE legs have broker GTTs to reconcile. Paper legs are
        # tick-driven only; nothing to poll at the broker.
        if group.paper:
            return

        # Snapshot open legs (group manager may close some concurrently;
        # we re-check leg.open before acting).
        for leg in list(group.open_legs()):
            if not leg.gtt_id:
                # No GTT placed (placement failed at entry). Position-only
                # verification: if the short was never protected, we still
                # watch for the broker position disappearing.
                self._check_unprotected_leg(group, leg)
                continue
            self._check_leg_gtt(group, leg)

    # --------------------------------------------------
    # CHECK ONE LEG'S GTT
    # --------------------------------------------------

    def _check_leg_gtt(self, group, leg):
        gtt_id = leg.gtt_id

        # STEP 1: fetch GTT list (network error → never close)
        try:
            gtts = self.executor.get_gtts()
        except Exception as e:
            write_audit_log(
                f"[V2_GTT_MONITOR][FETCH_FAIL] leg NOT closed. "
                f"class={leg.trade_class} GTT_ID={gtt_id} ERR={e}"
            )
            return

        gtt = next((g for g in gtts if str(g.get("id")) == str(gtt_id)), None)

        # ---- GTT FOUND ----
        if gtt is not None:
            self._missing_counts.pop(gtt_id, None)

            status = gtt.get("status", "")
            if status not in ("triggered", "disabled"):
                self._pending_fill.pop(gtt_id, None)
                return   # still resting — nothing to do

            # Triggered/disabled → confirm a real fill before closing.
            orders = gtt.get("orders", [])
            exit_price, exit_order_id, reason = self._resolve_from_gtt(
                gtt, orders, leg.symbol, leg,
            )

            if exit_order_id is None:
                retry = self._pending_fill.get(gtt_id, 0) + 1
                self._pending_fill[gtt_id] = retry
                write_audit_log(
                    f"[V2_GTT_MONITOR] GTT_ID={gtt_id} triggered, fill unconfirmed "
                    f"class={leg.trade_class} retry={retry}/{self.FILL_CONFIRM_RETRIES}"
                )
                if retry < self.FILL_CONFIRM_RETRIES:
                    return
                reason = "BROKER_EXIT"

            write_audit_log(
                f"[V2_GTT_MONITOR] CONFIRMED EXIT class={leg.trade_class} "
                f"{leg.symbol} reason={reason} price={exit_price}"
            )
            self._notify_exit(group, leg, exit_price, reason)
            self._missing_counts.pop(gtt_id, None)
            self._pending_fill.pop(gtt_id, None)
            return

        # ---- GTT NOT FOUND (clean fetch) ----
        self._pending_fill.pop(gtt_id, None)
        count = self._missing_counts.get(gtt_id, 0) + 1
        self._missing_counts[gtt_id] = count

        write_audit_log(
            f"[V2_GTT_MONITOR] GTT_ID={gtt_id} not in broker list "
            f"class={leg.trade_class} misses={count}/{self.MISSING_THRESHOLD}"
        )

        if count < self.MISSING_THRESHOLD:
            return

        # STEP 2: threshold reached — verify broker position before closing.
        if self._position_still_open(leg):
            write_audit_log(
                f"[V2_GTT_MONITOR] SAFETY: position still open for {leg.symbol} "
                f"— resetting miss count (leg NOT closed)"
            )
            self._missing_counts.pop(gtt_id, None)
            return

        # GTT missing AND position gone → leg has exited at broker.
        write_audit_log(
            f"[V2_GTT_MONITOR] GTT missing ({count}x) AND position gone for "
            f"{leg.symbol} → closing as BROKER_EXIT class={leg.trade_class}"
        )
        exit_price, _ = self._fetch_fill_from_orders(leg.symbol)
        if exit_price is None:
            exit_price = self._resolve_price_fallback(leg.symbol)
        self._notify_exit(group, leg, exit_price, "BROKER_EXIT")
        self._missing_counts.pop(gtt_id, None)

    # --------------------------------------------------
    # UNPROTECTED LEG (no GTT) — position-only watch
    # --------------------------------------------------

    def _check_unprotected_leg(self, group, leg):
        # If a SHORT entry placed but GTT failed, the only broker signal is
        # the position itself. If it's gone, something closed it.
        if self._position_still_open(leg):
            return
        write_audit_log(
            f"[V2_GTT_MONITOR] Unprotected leg {leg.symbol} position gone "
            f"→ BROKER_EXIT class={leg.trade_class}"
        )
        exit_price, _ = self._fetch_fill_from_orders(leg.symbol)
        if exit_price is None:
            exit_price = self._resolve_price_fallback(leg.symbol)
        self._notify_exit(group, leg, exit_price, "BROKER_EXIT")

    # --------------------------------------------------
    # POSITION VERIFICATION
    # --------------------------------------------------

    def _position_still_open(self, leg) -> bool:
        """
        True if the broker still shows an open position for this leg's symbol
        of at least the leg qty. On any error, returns True (fail-safe — never
        close on uncertain data).
        """
        try:
            positions = self.executor.get_open_positions()
            broker_qty = sum(
                abs(p.get("quantity", 0))
                for p in positions
                if p.get("tradingsymbol") == leg.symbol and p.get("quantity", 0) != 0
            )
            return broker_qty >= leg.qty and leg.qty > 0
        except Exception as e:
            write_audit_log(
                f"[V2_GTT_MONITOR] position check failed {leg.symbol}: {e} "
                f"— treating as still open (fail-safe)"
            )
            return True

    # --------------------------------------------------
    # EXIT RESOLUTION (cloned chain from gtt_monitor.py)
    # --------------------------------------------------

    def _resolve_from_gtt(self, gtt, orders, symbol, leg) -> tuple:
        """Returns (exit_price, exit_order_id, reason). SHORT: BUY-back legs."""
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

        # SHORT GTT order layout (from executor.place_gtt_oco SHORT branch):
        #   index 0 → lower trigger = TP (buy back at profit)
        #   index 1 → upper trigger = SL (buy back to cut loss)
        reason = None
        if triggered_idx == 0:
            reason = "GTT_TP"
        elif triggered_idx == 1:
            reason = "GTT_SL"

        if exit_order_id is None:
            fb_price, fb_oid = self._fetch_fill_from_orders(symbol)
            if fb_price is not None:
                exit_price    = fb_price
                exit_order_id = fb_oid

        if reason is None:
            reason = self._infer_reason(gtt, exit_price, leg)

        if exit_price is None:
            exit_price = self._resolve_price_fallback(symbol)

        return exit_price, exit_order_id, (reason or "BROKER_EXIT")

    def _fetch_fill_from_orders(self, symbol: str) -> tuple:
        """SHORT close fills are BUY COMPLETE orders for this symbol."""
        try:
            broker_orders = self.executor.get_orders()
            completed_buys = [
                o for o in broker_orders
                if o.get("tradingsymbol") == symbol
                and o.get("transaction_type") == "BUY"
                and o.get("status") == "COMPLETE"
            ]
            if not completed_buys:
                return None, None
            completed_buys.sort(
                key=lambda x: x.get("exchange_timestamp") or "", reverse=True
            )
            best = completed_buys[0]
            avg  = best.get("average_price")
            return (float(avg) if avg else None), best.get("order_id")
        except Exception as e:
            write_audit_log(f"[V2_GTT_MONITOR] kite.orders() fallback failed: {e}")
            return None, None

    def _resolve_price_fallback(self, symbol: str):
        """LTPStore → REST (via group manager's premium reader)."""
        ltp = LTPStore.get(symbol)
        if ltp and ltp > 0:
            return float(ltp)
        try:
            return self.group_manager._live_premium(symbol)
        except Exception:
            return None

    def _infer_reason(self, gtt: dict, exit_price, leg) -> str:
        trigger_values = gtt.get("trigger_values") or []
        # SHORT triggers = [tp_lower, sl_upper]
        if exit_price and len(trigger_values) == 2:
            dist_tp = abs(exit_price - trigger_values[0])
            dist_sl = abs(exit_price - trigger_values[1])
            return "GTT_TP" if dist_tp <= dist_sl else "GTT_SL"
        if exit_price and leg.sl and leg.tp:
            dist_tp = abs(exit_price - leg.tp)
            dist_sl = abs(exit_price - leg.sl)
            return "GTT_TP" if dist_tp <= dist_sl else "GTT_SL"
        return "BROKER_EXIT"

    # --------------------------------------------------
    # HANDOFF TO GROUP MANAGER (single authoritative path)
    # --------------------------------------------------

    def _notify_exit(self, group, leg, exit_price, reason):
        """
        Defer ALL state transitions to the group manager. It re-checks
        leg.open under its own lock, records the exit, runs the stagger
        FSM (start window if this is the first leg / finalize if last),
        and prevents any double-exit.
        """
        try:
            self.group_manager.on_backstop_leg_exit(
                group_id=group.group_id,
                trade_class=leg.trade_class,
                exit_price=exit_price,
                reason=reason,
            )
        except Exception as e:
            write_audit_log(
                f"[V2_GTT_MONITOR][HANDOFF_FAIL] class={leg.trade_class} "
                f"{leg.symbol} ERR={e}"
            )