# backend/app/engine/pst/pst_order_executor.py
#
# ── PST ORDER EXECUTORS ── (paper + live behind one interface)
#
# PaperExecutor: returns the MODEL price it is given — the managers' paper
# logic (candle-close / TP-at-level fills, parity-proven against the
# backtest) is untouched.
#
# LiveExecutor: real Zerodha MIS orders on NFO. Doctrine:
#   * every broker interaction can fail; every failure returns None/False
#     and ALERTS — callers stay fail-closed (no position without a fill
#     price, no assumed exits)
#   * market fills are confirmed by polling order history for COMPLETE and
#     taking average_price (Zerodha sets status before fills propagate —
#     hence the retry loop, same doctrine as the GTT fill-price lesson)
#   * PST_SELL's premium-TP is a RESTING LIMIT BUY at the level — the live
#     realization of the backtest's fill-AT-level convention
#   * cancel-before-market on exits; a cancel that fails because the order
#     already COMPLETED is surfaced as ("COMPLETE", avg) so the caller
#     books the TP instead of double-exiting

from __future__ import annotations

import time
from typing import Optional, Tuple

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)


class PaperExecutor:
    is_paper = True

    def market(self, symbol: str, transaction_type: str, qty: int,
               model_price: Optional[float] = None) -> Tuple[Optional[float], Optional[str]]:
        return model_price, None

    def limit_buy(self, symbol: str, qty: int, price: float) -> Optional[str]:
        return None      # paper TP is simulated candle-side by the manager

    def status(self, order_id: str) -> Tuple[str, Optional[float]]:
        return "NONE", None

    def cancel_or_complete(self, order_id: str) -> Tuple[str, Optional[float]]:
        return "CANCELLED", None


class LiveExecutor:
    """RELAY-ROUTED live executor (2026-07-15 incident fix).

    The first version called kite.place_order() DIRECTLY — the order left the
    user's home connection (IP 27.5.76.209) and Zerodha rejected it: only the
    relay IPs are whitelisted (SEBI static-IP). ALL placements and cancels now
    delegate to the house ZerodhaOrderExecutor, which routes through the
    configured relays primary-first with the house fallback policy, uses the
    TRADE kite (not the data kite), and enforces the app-level trading
    kill-switch (TradingDisabledError → fail closed here). Order-status READS
    stay on the kite API directly — house precedent (get_orders does the
    same); the static-IP restriction applies to order placement."""

    is_paper = False

    def __init__(self, broker_manager, notify=None):
        self.bm = broker_manager
        self.notify = notify
        from app.execution.zerodha_executor import ZerodhaOrderExecutor
        self.house = ZerodhaOrderExecutor(broker_manager)

    def _kite(self):
        try:
            return self.bm.get_trade_kite()
        except Exception:
            return None

    def _alert(self, msg: str) -> None:
        write_audit_log(f"[PST][LIVE][ALERT] {msg}")
        if self.notify:
            try:
                self.notify(f"PST LIVE: {msg}")
            except Exception:
                pass

    def _fill_price(self, order_id: str, timeout_s: float = 8.0
                    ) -> Optional[float]:
        kite = self._kite()
        if kite is None:
            self._alert(f"order {order_id}: no trade kite for fill confirm")
            return None
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                hist = kite.order_history(order_id)
                last = hist[-1] if hist else {}
                st = last.get("status")
                if st == "COMPLETE":
                    avg = float(last.get("average_price") or 0)
                    if avg > 0:
                        return avg
                elif st in ("REJECTED", "CANCELLED"):
                    self._alert(f"order {order_id} {st}: "
                                f"{last.get('status_message', '')}")
                    return None
            except Exception:
                pass
            time.sleep(0.7)
        self._alert(f"order {order_id} fill confirmation timed out")
        return None

    def market(self, symbol: str, transaction_type: str, qty: int,
               model_price: Optional[float] = None
               ) -> Tuple[Optional[float], Optional[str]]:
        try:
            if transaction_type == "SELL":
                oid = self.house.place_market_sell(symbol, int(qty))
            else:
                # the house market-BUY primitive (named for exits; it is a
                # plain relay-routed MARKET BUY — PST uses it for hedge
                # entries and short buybacks alike)
                oid = self.house.place_buy_exit(symbol, int(qty), "PST")
        except Exception as e:
            self._alert(f"market {transaction_type} {symbol} x{qty} FAILED: {e}")
            return None, None
        if not oid:
            self._alert(f"market {transaction_type} {symbol} x{qty}: no order id")
            return None, None
        avg = self._fill_price(oid)
        if avg is None:
            return None, oid
        write_audit_log(f"[PST][LIVE] {transaction_type} {symbol} x{qty} "
                        f"filled @{avg:.2f} (order {oid}, relay-routed)")
        return avg, oid

    def limit_buy(self, symbol: str, qty: int, price: float) -> Optional[str]:
        kite = self._kite()
        if kite is None:
            self._alert(f"TP limit BUY {symbol}: no trade kite")
            return None
        kw = dict(variety=kite.VARIETY_REGULAR, exchange="NFO",
                  tradingsymbol=symbol, transaction_type="BUY",
                  quantity=int(qty), product="MIS",
                  order_type=kite.ORDER_TYPE_LIMIT,
                  price=round(float(price), 1))
        try:
            # no house LIMIT primitive — route through the SAME relay helper
            # the house methods use internally (house fallback policy applies)
            oid = self.house._relay_call(
                relay_fn=lambda r: r.place_order(**kw),
                direct_fn=lambda: kite.place_order(**kw),
                op_name="PST_TP_LIMIT", symbol=symbol)
            write_audit_log(f"[PST][LIVE] resting TP limit BUY {symbol} x{qty} "
                            f"@{price:.2f} (order {oid}, relay-routed)")
            return oid
        except Exception as e:
            self._alert(f"TP limit BUY {symbol} x{qty} @{price:.2f} FAILED: {e} "
                        f"— falling back to app-monitored TP")
            return None

    def status(self, order_id: str) -> Tuple[str, Optional[float]]:
        kite = self._kite()
        if kite is None:
            return "UNKNOWN", None
        try:
            hist = kite.order_history(order_id)
            last = hist[-1] if hist else {}
            st = last.get("status") or "UNKNOWN"
            avg = float(last.get("average_price") or 0) or None
            return st, avg
        except Exception:
            return "UNKNOWN", None

    def cancel_or_complete(self, order_id: str) -> Tuple[str, Optional[float]]:
        """Cancel via the relay-routed house path. Already filled →
        ("COMPLETE", avg) so the caller books the TP. Any other failure →
        ("FAILED", None): caller must NOT market-exit (double-close risk)."""
        try:
            self.house.cancel_order(order_id)
            return "CANCELLED", None
        except Exception:
            st, avg = self.status(order_id)
            if st == "COMPLETE" and avg:
                return "COMPLETE", avg
            if st in ("CANCELLED", "REJECTED"):
                return "CANCELLED", None
            self._alert(f"cancel of order {order_id} failed (status {st}) — "
                        f"NOT market-exiting this leg to avoid a double close")
            return "FAILED", None