# backend/app/engine/tma2/tma2_gtt_monitor.py
#
# ── TMA_V2 GTT BACKSTOP MONITOR ── (ic_gtt_monitor doctrine, single leg)
# ============================================================================
#   * NEVER mutates strategy state. Single handoff:
#     manager.on_backstop_sell_exit(exit_price=…, reason=…) — the manager
#     owns closing AND the same-minute hedge exit.
#   * NEVER closes on a fetch error. Network failure → log, wait, retry.
#   * GTT race (house learning): Zerodha flips status="triggered" BEFORE
#     orders[].result is populated. Fill resolution chain:
#       (1) gtt.orders[].result.order_id → get_order_fill()
#       (2) recent broker orders scan (BUY COMPLETE on the symbol)
#       (3) after FILL_CONFIRM_RETRIES sweeps: reason=BROKER_EXIT,
#           price ← LTPStore fresh / leg.sl conservative stamp
#   * MISSING GTT ≠ exited. GTT missing for MISSING_THRESHOLD consecutive
#     clean sweeps AND the broker position gone → BROKER_EXIT. Position
#     still open with the GTT missing → CRITICAL naked-short alert, NO
#     state change — the candle SL still guards; the human decides.
# ============================================================================

from __future__ import annotations

import threading
import time
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore

from app.engine.tma2.tma2_common import STRATEGY_ID


class TMA2GTTMonitor:

    POLL_INTERVAL = 20
    FILL_CONFIRM_RETRIES = 3
    MISSING_THRESHOLD = 3
    LTP_STALENESS_SEC = 30

    def __init__(self, executor, manager):
        self.executor = executor
        self.manager = manager
        self._running = False
        self._missing = 0
        self._pending = {}        # gtt_id -> fill-confirm retry count

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True,
                         name="TMAV2GTTMonitor").start()
        write_audit_log("[TMA2_GTT_MONITOR] started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._sweep()
            except Exception as e:
                write_audit_log(f"[TMA2_GTT_MONITOR][ERROR] {repr(e)}")
            time.sleep(self.POLL_INTERVAL)

    # ------------------------------------------------------------------
    def _sweep(self):
        g = getattr(self.manager, "group", None)
        if g is None or g.get("mode") != "LIVE":
            self._missing = 0
            return
        gid = (g.get("sell") or {}).get("gtt_id")
        if not gid:
            return   # unprotected leg: candle monitor is the sole guard

        try:
            gtts = self.executor.get_gtts()
        except Exception as e:
            write_audit_log(f"[TMA2_GTT_MONITOR][FETCH_FAIL] no action ERR={e}")
            return
        by_id = {str(x.get("id")): x for x in (gtts or [])}
        gtt = by_id.get(str(gid))
        sell = g["sell"]

        if gtt is not None:
            self._missing = 0
            status = gtt.get("status", "")
            if status not in ("triggered", "disabled"):
                self._pending.pop(gid, None)
                return
            price, oid = self._resolve_fill(gtt, sell["symbol"])
            if oid is None:
                retry = self._pending.get(gid, 0) + 1
                self._pending[gid] = retry
                write_audit_log(f"[TMA2_GTT_MONITOR] gtt={gid} triggered, fill "
                                f"unconfirmed retry={retry}/{self.FILL_CONFIRM_RETRIES}")
                if retry < self.FILL_CONFIRM_RETRIES:
                    return
                price = price or self._price_fallback(sell)
                reason = "BROKER_EXIT"
            else:
                reason = "SL"
            write_audit_log(f"[TMA2_GTT_MONITOR] CONFIRMED {sell['symbol']} "
                            f"reason={reason} price={price}")
            self._pending.pop(gid, None)
            self.manager.on_backstop_sell_exit(exit_price=price, reason=reason)
            return

        # GTT missing from a CLEAN fetch
        self._missing += 1
        write_audit_log(f"[TMA2_GTT_MONITOR] SL GTT {gid} missing "
                        f"({self._missing}/{self.MISSING_THRESHOLD})")
        if self._missing < self.MISSING_THRESHOLD:
            return
        if self._position_open(sell):
            write_audit_log(f"[TMA2_GTT_MONITOR][NAKED] {sell['symbol']} short "
                            f"open with NO GTT — alerting, not closing")
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({"message":
                    f"TMA_V2: {sell['symbol']} has an OPEN short but its SL "
                    f"GTT is GONE from Kite. Candle-SL still active; re-create "
                    f"the GTT or exit manually.", "severity": "error"})
            except Exception:
                pass
            self._missing = 0
            return
        price, _ = self._fill_from_orders(sell["symbol"])
        price = price or self._price_fallback(sell)
        write_audit_log(f"[TMA2_GTT_MONITOR] GTT gone AND position gone "
                        f"→ BROKER_EXIT @{price}")
        self._missing = 0
        self.manager.on_backstop_sell_exit(exit_price=price,
                                           reason="BROKER_EXIT")

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
                write_audit_log(f"[TMA2_GTT_MONITOR][FILL_READ_ERR] {oid} {e}")
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

    def _price_fallback(self, sell) -> float:
        try:
            res = LTPStore.get_with_timestamp(sell["symbol"])
            if res:
                ltp, ts = res
                if ltp and ltp > 0 and (time.time() - ts) <= self.LTP_STALENESS_SEC:
                    return float(ltp)
        except Exception:
            pass
        return float(sell.get("sl") or sell.get("entry") or 0.0)

    def _position_open(self, sell) -> bool:
        try:
            positions = self.executor.get_open_positions() or []
        except Exception:
            return True    # can't verify → assume open (never close blind)
        for p in positions:
            if p.get("tradingsymbol") == sell["symbol"] \
                    and int(p.get("quantity") or 0) != 0:
                return True
        return False