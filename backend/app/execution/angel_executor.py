# backend/app/execution/angel_executor.py
# ============================================================
# ACC2 BEGIN — Angel One order executor (secondary account)
#
# W1 SCOPE (probe-verified endpoints ONLY are implemented):
#   place_gtt_oco (LONG + SHORT), place_gtt_sl_only_long/short,
#   place_gtt_tp_only_long, cancel_gtt, cancel_gtt_verified,
#   get_gtts, resolve_symbol, LTP sanity read.
#
# Regular-order endpoints (place/modify/cancel order, order book,
# positions) are NOT yet live-verified (design doc §3.2). They raise
# AngelUnverifiedEndpointError — fail closed, no silent stubs. They
# get implemented in Wave 2 after the order-path probe.
#
# Probe-verified facts encoded here (2026-08-07):
#   - createRule: gttType "OCO" (camelCase); primary leg =
#     triggerprice/price, SL leg = stoplosstriggerprice/stoplossprice;
#     single transactiontype covers both legs (SELL = long protection,
#     BUY = short protection). Single-leg rules omit gttType and echo
#     back gttType "GENERIC".
#   - Success schema: status true + errorcode (lower). Failure schema:
#     success false + errorCode (camel). AG7002 = unregistered IP
#     (fatal config error, never retried).
#   - cancelRule requires {id, symboltoken, exchange}; ruleDetails
#     echoes full rule; statuses observed NEW / CANCELLED (~250ms).
#   - GTT expirydate is capped at CONTRACT expiry regardless of
#     timeperiod.
# ============================================================

import time
from typing import Dict, List, Optional, Tuple

import requests

from app.brokers.angel_manager import AngelManager
from app.config.trading_config import MAX_QTY_PER_ORDER
from app.event_bus.audit_logger import write_audit_log
from app.execution.base_executor import BaseOrderExecutor
from app.marketdata.angel_instrument_resolver import AngelInstrumentResolver
from app.marketdata.ltp_store import LTPStore

# ============================================================
# ACC2_W3 BEGIN — order-path implementation (2026-08-07)
# Payload shapes mirror the W2 probe (placeOrder body was accepted
# up to the IP gate) + SmartAPI docs. Facts already OBSERVED live:
#   - getRMS returns decimals as STRINGS ("0.0000") with nulls for
#     span/exposure fields -> parse with _f() coercion, never cast.
#   - getPosition returns data: null when flat -> null-guard to [].
#   - Failure schema on order path = success:false + camel errorCode
#     (same dual schema as GTT) -> shared parser holds.
# ORDER WRITES stay HARD-GATED behind ANGEL_ORDER_SCHEMA_VERIFIED
# until the Monday order-path probe confirms fill/order-book shapes.
# Flipping wiring live before that confirmation is forbidden.
# ============================================================
ANGEL_ORDER_SCHEMA_VERIFIED = False  # flip after W2 probe passes

ANGEL_BASE = "https://apiconnect.angelone.in"
EP_GTT_CREATE = ANGEL_BASE + "/rest/secure/angelbroking/gtt/v1/createRule"
EP_GTT_CANCEL = ANGEL_BASE + "/rest/secure/angelbroking/gtt/v1/cancelRule"
EP_GTT_DETAILS = ANGEL_BASE + "/rest/secure/angelbroking/gtt/v1/ruleDetails"
EP_GTT_LIST = ANGEL_BASE + "/rest/secure/angelbroking/gtt/v1/ruleList"
EP_LTP = ANGEL_BASE + "/rest/secure/angelbroking/order/v1/getLtpData"
# ACC2_W3 order-path endpoints
EP_PLACE = ANGEL_BASE + "/rest/secure/angelbroking/order/v1/placeOrder"
EP_CANCEL_ORDER = ANGEL_BASE + "/rest/secure/angelbroking/order/v1/cancelOrder"
EP_ORDERBOOK = ANGEL_BASE + "/rest/secure/angelbroking/order/v1/getOrderBook"
EP_POSITION = ANGEL_BASE + "/rest/secure/angelbroking/order/v1/getPosition"
EP_RMS = ANGEL_BASE + "/rest/secure/angelbroking/user/v1/getRMS"

TICK = 0.05


class AngelNotReadyError(RuntimeError):
    """Session not trade-ready. Callers degrade to PAPER (fail closed)."""


class AngelIPNotRegisteredError(RuntimeError):
    """AG7002 — machine IP not registered on the SmartAPI app.
    Fatal configuration error. NEVER retried."""


class AngelUnverifiedEndpointError(NotImplementedError):
    """Endpoint not yet live-verified (Wave 2). Fail closed."""


def _tick(px: float) -> float:
    return max(TICK, round(round(px / TICK) * TICK, 2))


class AngelOneExecutor(BaseOrderExecutor):

    def __init__(self, manager: AngelManager,
                 resolver: Optional[AngelInstrumentResolver] = None):
        self._mgr = manager
        self._resolver = resolver or AngelInstrumentResolver()
        self._relogin_attempted_at: float = 0.0

    # --------------------------------------------------
    # HTTP CORE
    # --------------------------------------------------

    def _post(self, url: str, payload: dict, op: str) -> dict:
        headers = self._mgr.auth_headers()
        if headers is None:
            raise AngelNotReadyError(f"[{op}] Angel session not trade-ready")
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            body = r.json()
        except Exception as e:
            write_audit_log(f"[ANGEL_EXEC][WARN] {op} network ERR={e}")
            raise

        if body.get("status") is True:
            return body

        code = str(body.get("errorCode") or body.get("errorcode") or "")
        msg = body.get("message")

        if code == "AG7002":
            write_audit_log(
                f"[ANGEL_EXEC][FATAL] {op} AG7002 unregistered IP")
            raise AngelIPNotRegisteredError(
                "Angel rejected the call: this machine's IP is not the one "
                "registered on the SmartAPI app.")

        # One intraday auto re-login on auth-class errors (D3), rate-limited
        # to once per 60s so error storms cannot loop logins.
        if code.startswith("AG8") or "token" in str(msg).lower():
            now = time.time()
            if now - self._relogin_attempted_at > 60:
                self._relogin_attempted_at = now
                if self._mgr.relogin_once():
                    headers = self._mgr.auth_headers()
                    if headers is not None:
                        r = requests.post(url, headers=headers,
                                          json=payload, timeout=15)
                        body = r.json()
                        if body.get("status") is True:
                            return body

        write_audit_log(f"[ANGEL_EXEC][WARN] {op} rejected code={code} msg={msg}")
        raise RuntimeError(f"[{op}] Angel rejected: {code} {msg}")

    # --------------------------------------------------
    # SYMBOLS / LTP
    # --------------------------------------------------

    def resolve_symbol(self, symbol: str) -> str:
        sym, _tok = self._resolver.resolve_from_kite_symbol(symbol)
        return sym

    def _resolve_symbol_token(self, symbol: str) -> Tuple[str, str]:
        return self._resolver.resolve_from_kite_symbol(symbol)

    def _resolve_ltp(self, symbol: str) -> Optional[float]:
        """Angel-side LTP, used ONLY for trigger-band sanity (D5)."""
        try:
            sym, tok = self._resolve_symbol_token(symbol)
            body = self._post(EP_LTP, {
                "exchange": "NFO", "tradingsymbol": sym, "symboltoken": tok,
            }, "LTP")
            return float(body["data"]["ltp"])
        except Exception:
            return None

    # --------------------------------------------------
    # GTT — VERIFIED SURFACE
    # --------------------------------------------------

    def _create_rule(self, payload: dict, op: str) -> str:
        body = self._post(EP_GTT_CREATE, payload, op)
        rule_id = str((body.get("data") or {}).get("id"))
        if not rule_id or rule_id == "None":
            raise RuntimeError(f"[{op}] createRule returned no id: {body}")
        write_audit_log(f"[ANGEL_EXEC] {op} rule_id={rule_id} "
                        f"sym={payload['tradingsymbol']} qty={payload['qty']}")
        return rule_id

    def place_gtt_oco(self, symbol: str, qty: int, sl_price: float,
                      tp_price: Optional[float], last_price: Optional[float] = None,
                      direction: str = "LONG") -> str:
        """
        direction LONG  -> protecting a long  -> SELL rule
                           (target leg above, SL leg below)
        direction SHORT -> protecting a short -> BUY rule
                           (buy-back target below, SL leg above)
        tp_price None   -> SL-only single-leg rule (GENERIC).
        """
        sym, tok = self._resolve_symbol_token(symbol)

        if tp_price is None:
            if direction == "LONG":
                return self.place_gtt_sl_only_long(symbol, qty, sl_price)
            return self.place_gtt_sl_only_short(symbol, qty, sl_price)

        sl = _tick(sl_price)
        tp = _tick(tp_price)

        if direction == "LONG":
            txn = "SELL"
            primary_trigger, primary_limit = tp, _tick(tp * 0.997)
            sl_trigger, sl_limit = sl, _tick(sl * 0.995)
        else:  # SHORT
            txn = "BUY"
            primary_trigger, primary_limit = tp, _tick(tp * 1.003)
            sl_trigger, sl_limit = sl, _tick(sl * 1.005)

        return self._create_rule({
            "tradingsymbol": sym, "symboltoken": tok, "exchange": "NFO",
            "producttype": "CARRYFORWARD", "transactiontype": txn,
            "qty": qty, "disclosedqty": 0, "timeperiod": 1,
            "triggerprice": primary_trigger, "price": primary_limit,
            "gttType": "OCO",
            "stoplosstriggerprice": sl_trigger, "stoplossprice": sl_limit,
        }, f"GTT_OCO_{direction}")

    def _single_leg(self, symbol: str, qty: int, trigger: float,
                    txn: str, op: str) -> str:
        sym, tok = self._resolve_symbol_token(symbol)
        t = _tick(trigger)
        limit = _tick(t * (0.997 if txn == "SELL" else 1.003))
        return self._create_rule({
            "tradingsymbol": sym, "symboltoken": tok, "exchange": "NFO",
            "producttype": "CARRYFORWARD", "transactiontype": txn,
            "qty": qty, "disclosedqty": 0, "timeperiod": 1,
            "triggerprice": t, "price": limit,
        }, op)

    def place_gtt_sl_only_long(self, symbol: str, qty: int,
                               sl_price: float) -> str:
        return self._single_leg(symbol, qty, sl_price, "SELL", "GTT_SL_LONG")

    def place_gtt_sl_only_short(self, symbol: str, qty: int,
                                sl_price: float) -> str:
        return self._single_leg(symbol, qty, sl_price, "BUY", "GTT_SL_SHORT")

    def place_gtt_tp_only_long(self, symbol: str, qty: int,
                               tp_price: float) -> str:
        return self._single_leg(symbol, qty, tp_price, "SELL", "GTT_TP_LONG")

    # ---------------- cancel + verify ----------------

    def _rule_details(self, rule_id: str) -> dict:
        body = self._post(EP_GTT_DETAILS, {"id": rule_id}, "GTT_DETAILS")
        return body.get("data") or {}

    def cancel_gtt(self, gtt_id: str):
        det = self._rule_details(str(gtt_id))
        self._post(EP_GTT_CANCEL, {
            "id": str(gtt_id),
            "symboltoken": det.get("symboltoken"),
            "exchange": det.get("exchange", "NFO"),
        }, "GTT_CANCEL")

    def cancel_gtt_verified(self, gtt_id: str, retries: int = 4) -> bool:
        try:
            self.cancel_gtt(gtt_id)
        except AngelIPNotRegisteredError:
            raise
        except Exception as e:
            write_audit_log(f"[ANGEL_EXEC][WARN] cancel_gtt failed ERR={e}")
        for _ in range(max(1, retries)):
            try:
                status = str(self._rule_details(str(gtt_id))
                             .get("status", "")).upper()
                if "CANCEL" in status:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        write_audit_log(
            f"[ANGEL_EXEC][WARN] GTT {gtt_id} cancel NOT verified")
        return False

    def get_gtts(self) -> List[Dict]:
        body = self._post(EP_GTT_LIST, {
            # Status vocabulary beyond NEW/CANCELLED to be confirmed in W2;
            # over-asking is harmless (unknown statuses ignored server-side).
            "status": ["NEW", "ACTIVE", "SENTTOEXCHANGE", "FORALL"],
            "page": 1, "count": 100,
        }, "GTT_LIST")
        data = body.get("data") or []
        return data if isinstance(data, list) else []

    # --------------------------------------------------
    # ACC2_W3 — REGULAR ORDERS (probe-shaped; writes gated)
    # --------------------------------------------------

    def _get(self, url: str, op: str) -> dict:
        headers = self._mgr.auth_headers()
        if headers is None:
            raise AngelNotReadyError(f"[{op}] Angel session not trade-ready")
        r = requests.get(url, headers=headers, timeout=15)
        try:
            body = r.json()
        except Exception as e:
            write_audit_log(f"[ANGEL_EXEC][WARN] {op} bad response ERR={e}")
            raise
        return body

    def _gate_order_write(self, op: str):
        if not ANGEL_ORDER_SCHEMA_VERIFIED:
            raise AngelUnverifiedEndpointError(
                f"AngelOneExecutor.{op}: order path not yet live-verified "
                f"(W2 probe pending). Refusing write — fail closed.")

    @staticmethod
    def _f(v) -> float:
        """getRMS/positions return decimals as strings, sometimes null."""
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def _order_ltp(self, symbol: str) -> float:
        # D5: Kite ticks first; Angel LTP as fallback sanity source.
        try:
            ltp = LTPStore.get(symbol)
        except Exception:
            ltp = None
        if not ltp or ltp <= 0:
            ltp = self._resolve_ltp(symbol)
        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")
        return float(ltp)

    def _place(self, txn: str, symbol: str, qty: int, limit_px: float,
               op: str) -> str:
        self._gate_order_write(op)
        if qty <= 0 or qty > MAX_QTY_PER_ORDER:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")
        sym, tok = self._resolve_symbol_token(symbol)
        body = self._post(EP_PLACE, {
            "variety": "NORMAL", "tradingsymbol": sym, "symboltoken": tok,
            "transactiontype": txn, "exchange": "NFO",
            "ordertype": "LIMIT", "producttype": "CARRYFORWARD",
            "duration": "DAY", "price": limit_px,
            "squareoff": "0", "stoploss": "0", "quantity": str(qty),
        }, op)
        order_id = str((body.get("data") or {}).get("orderid") or "")
        if not order_id:
            raise RuntimeError(f"[{op}] placeOrder returned no orderid")
        write_audit_log(f"[ANGEL-{op}] ORDER_ID={order_id} SYMBOL={sym} "
                        f"QTY={qty} LIMIT={limit_px}")
        return order_id

    # ---- entries / exits (contracts mirror ZerodhaOrderExecutor) ----

    def place_buy(self, symbol: str, token: int, qty: int):
        ltp = self._order_ltp(symbol)
        return (self._place("BUY", symbol, qty, _tick(ltp * 1.05), "BUY"),
                0.0, qty)

    def place_sell_entry(self, symbol: str, token: int, qty: int):
        ltp = self._order_ltp(symbol)
        return (self._place("SELL", symbol, qty, _tick(ltp * 0.95),
                            "SELL_ENTRY"), 0.0, qty)

    def place_buy_exit(self, symbol: str, qty: int, reason: str) -> str:
        ltp = self._order_ltp(symbol)
        write_audit_log(f"[ANGEL-BUY-EXIT] {symbol} reason={reason}")
        return self._place("BUY", symbol, qty, _tick(ltp * 1.05), "BUY_EXIT")

    def place_market_sell(self, symbol: str, qty: int) -> str:
        ltp = self._order_ltp(symbol)
        return self._place("SELL", symbol, qty, _tick(ltp * 0.95),
                           "MARKET_SELL")

    # ---- order status (reads; always allowed) ----

    def get_orders(self) -> List[Dict]:
        body = self._get(EP_ORDERBOOK, "ORDER_BOOK")
        data = body.get("data")
        return data if isinstance(data, list) else []   # null-guard

    def get_order_fill(self, order_id: str) -> Dict:
        """Contract-identical to ZerodhaOrderExecutor.get_order_fill:
        status is normalized to UPPER (Angel returns lowercase) so
        callers' Kite-style comparisons (COMPLETE/REJECTED/...) hold.
        found=False must be treated as pending, never rejected."""
        empty = {"status": None, "avg_price": 0.0,
                 "filled_qty": 0, "pending_qty": 0, "found": False}
        try:
            for o in self.get_orders():
                if str(o.get("orderid")) == str(order_id):
                    filled = int(self._f(o.get("filledshares")
                                         or o.get("filledquantity")))
                    total = int(self._f(o.get("quantity")))
                    return {
                        "status": str(o.get("status") or "").upper() or None,
                        "avg_price": self._f(o.get("averageprice")),
                        "filled_qty": filled,
                        "pending_qty": max(0, total - filled),
                        "found": True,
                    }
        except Exception as e:
            write_audit_log(f"[ANGEL_EXEC][WARN] order fill fetch ERR={e}")
        return empty

    def get_last_avg_price(self, order_id: str) -> float:
        return self.get_order_fill(order_id)["avg_price"]

    def cancel_order(self, order_id: str):
        self._gate_order_write("CANCEL_ORDER")
        self._post(EP_CANCEL_ORDER,
                   {"variety": "NORMAL", "orderid": str(order_id)},
                   "CANCEL_ORDER")

    # ---- positions / funds (reads; dashboard + recovery) ----

    def get_open_positions(self) -> List[Dict]:
        body = self._get(EP_POSITION, "POSITIONS")
        data = body.get("data")
        return data if isinstance(data, list) else []   # OBSERVED: null when flat

    def get_funds(self) -> Optional[Dict]:
        """For the D9 balance pill. OBSERVED: string decimals + nulls."""
        try:
            body = self._get(EP_RMS, "RMS")
            d = body.get("data") or {}
            return {
                "net": self._f(d.get("net")),
                "available_cash": self._f(d.get("availablecash")),
                "utilised_debits": self._f(d.get("utiliseddebits")),
            }
        except Exception as e:
            write_audit_log(f"[ANGEL_EXEC][WARN] getRMS ERR={e}")
            return None

    # ---- legacy ABC surface ----

    def place_sl(self, symbol: str, qty: int, sl_price: float) -> str:
        # Legacy path unused by current managers; GTT flow supersedes it.
        return self.place_gtt_sl_only_long(symbol, qty, sl_price)

    def place_exit(self, symbol: str, qty: int, reason: str):
        return self.place_market_sell(symbol, qty)

# ACC2_W3 END
# ACC2 END