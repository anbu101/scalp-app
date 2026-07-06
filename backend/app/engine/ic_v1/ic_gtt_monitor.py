# backend/app/engine/ic_v1/ic_gtt_monitor.py
#
# IC_V1 — GTT Backstop Monitor
# ============================================================================
# Port of scalp_v2_gtt_monitor doctrine to IC's multi-GTT legs (freeze
# slicing means a short can carry SEVERAL SL GTTs at the same trigger):
#
#   * NEVER mutates strategy state. Single handoff:
#     group_manager.on_backstop_leg_exit(leg_id=…, exit_price=…, reason=…) —
#     the group manager owns closing AND the MTC consequence (GT9-pinned).
#   * NEVER closes on a fetch error. Network failure → log, wait, retry.
#   * GTT race (house learning): Zerodha flips status="triggered" BEFORE
#     orders[].result is populated. Fill resolution chain:
#       (1) gtt.orders[].result.order_result.order_id → get_order_fill()
#       (2) recent broker orders scan (BUY COMPLETE on the symbol)
#       (3) after FILL_CONFIRM_RETRIES sweeps: reason=BROKER_EXIT,
#           price ← LTPStore fresh / leg.sl as the conservative stamp
#   * A leg is handed off when its FIRST GTT confirms — with slices, sibling
#     GTTs share the same trigger and fire together at the broker; state
#     closes once, the group manager's _exiting latch absorbs the rest.
#   * MISSING GTT ≠ exited. Only ALL of a leg's GTTs missing for
#     MISSING_THRESHOLD consecutive sweeps AND the broker position gone →
#     BROKER_EXIT (someone exited manually in Kite / GTT deleted).
#     Position still open with GTTs missing → CRITICAL alert (naked short!)
#     but NO state change — the human decides.
# ============================================================================

import threading
import time
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore

from app.engine.ic_v1.ic_group_manager import STRATEGY_ID


class ICGTTMonitor:

    POLL_INTERVAL        = 20
    FILL_CONFIRM_RETRIES = 3
    MISSING_THRESHOLD    = 3
    LTP_STALENESS_SEC    = 30

    def __init__(self, executor, group_manager):
        self.executor = executor
        self.gm = group_manager
        self._running = False
        self._missing = {}        # leg_id -> consecutive all-missing sweeps
        self._pending = {}        # gtt_id -> fill-confirm retry count

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True,
                         name="ICV1GTTMonitor").start()
        write_audit_log("[IC_GTT_MONITOR] started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._sweep()
            except Exception as e:
                write_audit_log(f"[IC_GTT_MONITOR][ERROR] {repr(e)}")
            time.sleep(self.POLL_INTERVAL)

    # ------------------------------------------------------------------
    def _sweep(self):
        core = self.gm.current_group()
        if core is None or self.gm.is_paper():
            return
        open_shorts = [l for l in core.open_legs() if l.is_short]
        if not open_shorts:
            return

        # one broker fetch per sweep (fetch error → touch NOTHING)
        try:
            gtts = self.executor.get_gtts()
        except Exception as e:
            write_audit_log(f"[IC_GTT_MONITOR][FETCH_FAIL] no action ERR={e}")
            return
        by_id = {str(g.get("id")): g for g in gtts}

        for leg in open_shorts:
            gids = list(self.gm.leg_runtime(leg.leg_id).get("gtt_ids") or [])
            if not gids:
                continue   # unprotected leg: engine tick-poll is sole guard
            self._check_leg(leg, gids, by_id)

    def _check_leg(self, leg, gids, by_id):
        found_any = False
        for gid in gids:
            gtt = by_id.get(str(gid))
            if gtt is None:
                continue
            found_any = True
            status = gtt.get("status", "")
            if status not in ("triggered", "disabled"):
                self._pending.pop(gid, None)
                continue

            # triggered → confirm a real fill before handing off
            price, oid = self._resolve_fill(gtt, leg.symbol)
            if oid is None:
                retry = self._pending.get(gid, 0) + 1
                self._pending[gid] = retry
                write_audit_log(f"[IC_GTT_MONITOR] gtt={gid} triggered, fill "
                                f"unconfirmed retry={retry}/{self.FILL_CONFIRM_RETRIES}")
                if retry < self.FILL_CONFIRM_RETRIES:
                    continue
                price = price or self._price_fallback(leg)
                reason = "BROKER_EXIT"
            else:
                reason = self._infer_reason(leg, price)

            write_audit_log(f"[IC_GTT_MONITOR] CONFIRMED {leg.leg_id} "
                            f"{leg.symbol} reason={reason} price={price}")
            self._pending.pop(gid, None)
            self._missing.pop(leg.leg_id, None)
            self.gm.on_backstop_leg_exit(leg_id=leg.leg_id,
                                         exit_price=price, reason=reason)
            return   # one handoff per leg; manager owns the rest

        if found_any:
            self._missing.pop(leg.leg_id, None)
            return

        # ALL of this leg's GTTs missing from a CLEAN fetch
        count = self._missing.get(leg.leg_id, 0) + 1
        self._missing[leg.leg_id] = count
        write_audit_log(f"[IC_GTT_MONITOR] {leg.leg_id} all GTTs missing "
                        f"({count}/{self.MISSING_THRESHOLD})")
        if count < self.MISSING_THRESHOLD:
            return

        if self._position_open(leg):
            # NAKED SHORT: protection deleted but position alive. Alert loudly,
            # change nothing — the tick poll still guards the SL level.
            write_audit_log(f"[IC_GTT_MONITOR][NAKED] {leg.symbol} position "
                            f"open with NO GTT — alerting, not closing")
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({"message":
                    f"IC_V1: {leg.symbol} ({leg.leg_id}) has an OPEN short "
                    f"position but its SL GTT(s) are GONE from Kite. Engine "
                    f"tick-SL still active; re-create the GTT or exit manually.",
                    "severity": "error"})
            except Exception:
                pass
            self._missing.pop(leg.leg_id, None)
            return

        price, _ = self._fill_from_orders(leg.symbol)
        price = price or self._price_fallback(leg)
        write_audit_log(f"[IC_GTT_MONITOR] {leg.leg_id} GTTs gone AND position "
                        f"gone → BROKER_EXIT @{price}")
        self._missing.pop(leg.leg_id, None)
        self.gm.on_backstop_leg_exit(leg_id=leg.leg_id,
                                     exit_price=price, reason="BROKER_EXIT")

    # ------------------------------------------------------------------
    # fill / price resolution (multi-level, house GTT-race chain)
    # ------------------------------------------------------------------
    def _resolve_fill(self, gtt: dict, symbol: str):
        for o in gtt.get("orders") or []:
            res = (o.get("result") or {})
            oid = ((res.get("order_result") or {}).get("order_id")) or res.get("order_id")
            if not oid:
                continue
            try:
                info = self.executor.get_order_fill(oid) or {}
                if (info.get("status") or "").upper() == "COMPLETE":
                    px = float(info.get("avg_price") or 0.0)
                    if px > 0:
                        return px, oid
            except Exception as e:
                write_audit_log(f"[IC_GTT_MONITOR][FILL_READ_ERR] {oid} {e}")
        return self._fill_from_orders(symbol)

    def _fill_from_orders(self, symbol: str):
        try:
            orders = self.executor.get_orders() or []
        except Exception:
            return None, None
        for o in reversed(orders):
            if (o.get("tradingsymbol") == symbol
                    and o.get("transaction_type") == "BUY"
                    and (o.get("status") or "").upper() == "COMPLETE"):
                px = float(o.get("average_price") or 0.0)
                if px > 0:
                    return px, o.get("order_id")
        return None, None

    def _price_fallback(self, leg) -> float:
        try:
            res = LTPStore.get_with_timestamp(leg.symbol)
            if res:
                ltp, ts = res
                if ltp and ltp > 0 and (time.time() - ts) <= self.LTP_STALENESS_SEC:
                    return float(ltp)
        except Exception:
            pass
        return float(leg.sl or leg.entry_price)

    def _infer_reason(self, leg, price: Optional[float]) -> str:
        """SL-only GTTs → SL. With a TP GTT in play, attribute by proximity."""
        if not leg.tp or not price:
            return "SL"
        return "TP" if abs(price - leg.tp) < abs(price - (leg.sl or price)) else "SL"

    def _position_open(self, leg) -> bool:
        try:
            positions = self.executor.get_open_positions() or []
        except Exception:
            return True    # can't verify → assume open (never close blind)
        for p in positions:
            if p.get("tradingsymbol") == leg.symbol and int(p.get("quantity") or 0) != 0:
                return True
        return False
