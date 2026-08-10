"""
zerodha_executor.py
====================
Zerodha Order Executor with optional OCI relay.

Changes vs previous version:
  - get_order_fill()    : NEW - single-call {status, avg_price, found} read of
                          the order book, used by BBTradeManager._resolve_fill()
                          to distinguish "still pending" from "filled (COMPLETE)"
                          from "dead (REJECTED/CANCELLED/LAPSED)". This fixes
                          recorded entry price drifting from the true broker fill
                          on slow BANKNIFTY limit fills, and prevents phantom
                          GTT/DB rows on rejected orders.
  - get_last_avg_price() : UNCHANGED - still used by exit-side _poll_for_fill().

Earlier changes (retained):
  - place_sell_entry()  : SELL entry for SCALP_V1 short positions
  - place_buy_exit()    : BUY back to close a short position
  - place_gtt_oco()     : direction="LONG"|"SHORT" param (default "LONG")
      LONG  -> existing SELL GTT OCO logic (BB/HA unchanged)
      SHORT -> inverted BUY GTT OCO logic (SCALP_V1 short)

All READ operations (positions, orders, GTT status) are always direct.

backend/app/execution/zerodha_executor.py
"""

from typing import Optional, List, Dict
import time
import json
import requests
from pathlib import Path

from kiteconnect import KiteConnect

from app.execution.base_executor import BaseOrderExecutor
from app.config.trading_config import MAX_QTY_PER_ORDER
from app.brokers.zerodha_manager import ZerodhaManager
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log


# --------------------------------------------------
# RELAY CONFIG
# --------------------------------------------------

_RELAY_CONFIG_PATH = Path.home() / ".scalp-app" / "relay_config.json"
_relay_cfg: Optional[dict] = None


def _load_relay_config() -> Optional[dict]:
    global _relay_cfg
    if _relay_cfg is not None:
        return _relay_cfg

    if not _RELAY_CONFIG_PATH.exists():
        return None

    try:
        cfg = json.loads(_RELAY_CONFIG_PATH.read_text())

        if not cfg.get("enabled"):
            return None

        if "relays" not in cfg and cfg.get("url"):
            cfg["relays"] = [{
                "url":          cfg["url"],
                "host":         cfg.get("host", cfg["url"]),
                "secret":       cfg.get("secret", ""),
                "ssh_username": cfg.get("ssh_username"),
                "ssh_key":      cfg.get("ssh_key"),
                "instance_id":  cfg.get("instance_id"),
                "is_primary":   True,
            }]
            write_audit_log("[RELAY] Migrated single-relay config to multi-relay format")

        if cfg.get("relays"):
            _relay_cfg = cfg
            hosts = [r.get("host") for r in cfg["relays"]]
            write_audit_log(f"[RELAY] Order relay ENABLED -> {hosts}")
        else:
            write_audit_log("[RELAY] relay_config.json found but no relay entries")

    except Exception as e:
        write_audit_log(f"[RELAY] Failed to load relay_config.json ERR={e}")

    return _relay_cfg


def relay_is_active() -> bool:
    return _load_relay_config() is not None


def _invalidate_relay_cache():
    global _relay_cfg
    _relay_cfg = None
    write_audit_log("[RELAY] Config cache invalidated")


# --------------------------------------------------
# RELAY HTTP CLIENT
# --------------------------------------------------

class RelayClient:
    TIMEOUT = 10

    def __init__(self, url: str, secret: str, api_key: str, access_token: str):
        self.base_url     = url.rstrip("/")
        self.secret       = secret
        self.api_key      = api_key
        self.access_token = access_token
        self._headers     = {"Authorization": f"Bearer {secret}"}

    def _post(self, path: str, payload: dict) -> dict:
        payload["api_key"]      = self.api_key
        payload["access_token"] = self.access_token
        resp = requests.post(
            f"{self.base_url}{path}", json=payload,
            headers=self._headers, timeout=self.TIMEOUT,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Relay {path} failed HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def place_order(self, **kwargs) -> str:
        result = self._post("/relay/place_order", kwargs)
        return str(result["order_id"])

    def place_gtt(self, **kwargs) -> dict:
        return self._post("/relay/place_gtt", kwargs)

    def cancel_gtt(self, trigger_id: int):
        resp = requests.delete(
            f"{self.base_url}/relay/gtt/{trigger_id}",
            params={"api_key": self.api_key, "access_token": self.access_token},
            headers=self._headers, timeout=self.TIMEOUT,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Relay cancel_gtt failed HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def cancel_order(self, variety: str, order_id: str):
        self._post("/relay/cancel_order", {"variety": variety, "order_id": order_id})

    # ── TSG_ENTRY_REPEG ── order modify (D1). Modification is an
    # order-management action under the SEBI static-IP framework, so it
    # routes through the relay exactly like placement. Requires the relay
    # servers to be REDEPLOYED with the matching /relay/modify_order
    # endpoint; an old relay 404s, _relay_call falls to the next relay and
    # finally to direct (same degradation contract as every other op).
    def modify_order(self, **kwargs) -> str:
        result = self._post("/relay/modify_order", kwargs)
        return str(result.get("order_id", kwargs.get("order_id", "")))


_RELAY_TRANSIENT_ERRORS = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)


# --------------------------------------------------
# EXECUTOR
# --------------------------------------------------

class TradingDisabledError(RuntimeError):
    pass


class ZerodhaOrderExecutor(BaseOrderExecutor):

    def __init__(self, broker_manager: ZerodhaManager):
        self.broker_manager    = broker_manager
        self._instrument_cache: Dict[str, int] = {}

    # ── relay helpers ─────────────────────────────────

    def _relay(self) -> Optional[RelayClient]:
        return None  # multi-relay routing handled inside _relay_call

    def _relay_call(self, relay_fn, direct_fn, op_name: str, symbol: str = ""):
        cfg = _load_relay_config()
        if not cfg or not cfg.get("relays"):
            return direct_fn()

        kite = self._kite()
        if not kite:
            return direct_fn()

        relays   = cfg["relays"]
        ordered  = [r for r in relays if r.get("is_primary")] + \
                   [r for r in relays if not r.get("is_primary")]

        for relay_entry in ordered:
            host   = relay_entry.get("host", relay_entry.get("url", ""))
            url    = relay_entry.get("url", "")
            secret = relay_entry.get("secret", "")

            if not url or not secret:
                write_audit_log(f"[RELAY][{op_name}] Skipping {host} - missing url/secret")
                continue

            try:
                relay  = RelayClient(url=url, secret=secret,
                                     api_key=kite.api_key,
                                     access_token=kite.access_token)
                result = relay_fn(relay)
                write_audit_log(f"[RELAY][{op_name}] {symbol} - SUCCESS via {host}")
                return result

            except _RELAY_TRANSIENT_ERRORS as e:
                write_audit_log(
                    f"[RELAY][{op_name}] {host} unreachable ({type(e).__name__}) - next relay"
                )
                continue
            except Exception as e:
                write_audit_log(f"[RELAY][{op_name}] {host} error: {e} - next relay")
                continue

        write_audit_log(
            f"[RELAY][{op_name}][ALL_FAILED] {symbol} - direct fallback"
        )
        result = direct_fn()
        write_audit_log(f"[RELAY][{op_name}][DIRECT_OK] {symbol}")
        return result

    # ── internal helpers ──────────────────────────────

    def get_gtts(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        try:
            return kite.get_gtts()
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] GTT fetch failed ERR={e}")
            return []

    # ── GTT_FETCH_STRICT BEGIN ────────────────────────────────────
    def get_gtts_or_none(self) -> Optional[List[Dict]]:
        """
        STRICT variant of get_gtts(): returns None when the broker GTT list
        could not actually be read (session not ready, or the fetch raised),
        and a real list ([] included) ONLY on a successful read.

        get_gtts() returns [] on failure, which reconcile logic cannot
        distinguish from "no GTTs exist at the broker" — under a reconcile
        that treats a missing GTT as "it probably fired", that ambiguity is
        unsafe (2026-07-13 HA_V1 incident). New reconcile paths use this;
        existing get_gtts() consumers (BB/V2/V3/V4/IC) are untouched.
        """
        kite = self._kite()
        if not kite:
            write_audit_log(
                "[ZERODHA][WARN] STRICT GTT fetch skipped — session not ready"
            )
            return None
        try:
            return kite.get_gtts()
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] STRICT GTT fetch failed ERR={e}")
            return None
    # ── GTT_FETCH_STRICT END ──────────────────────────────────────

    def _kite(self) -> Optional[KiteConnect]:
        if not self.broker_manager.is_trade_ready():
            write_audit_log("[ZERODHA_EXECUTOR] Not ready - refreshing from disk")
            self.broker_manager.refresh()
        if not self.broker_manager.is_trade_ready():
            return None
        return self.broker_manager.get_trade_kite()

    def _ensure_trading_enabled(self):
        from app.config.global_loader import load_global_config
        if not load_global_config().get("trade_on", False):
            raise TradingDisabledError("TRADING_DISABLED (executor gate)")

    def resolve_symbol(self, symbol: str) -> str:
        return symbol

    def _get_lot_size(self, kite: KiteConnect, symbol: str) -> int:
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        try:
            instruments = kite.instruments("NFO")
        except Exception as e:
            raise RuntimeError(f"INSTRUMENT_FETCH_FAILED SYMBOL={symbol} ERR={e}")
        for inst in instruments:
            if inst.get("tradingsymbol") == symbol:
                lot_size = int(inst.get("lot_size") or 0)
                if lot_size > 0:
                    self._instrument_cache[symbol] = lot_size
                    return lot_size
        raise RuntimeError(f"LOT_SIZE_NOT_FOUND SYMBOL={symbol}")

    @staticmethod
    def _protected_limit_price(ltp: float, side: str) -> float:
        """SEBI protection: LIMIT 1% away from LTP, rounded to NFO tick 0.05."""
        raw = ltp * 1.01 if side == "BUY" else ltp * 0.99
        return round(round(raw / 0.05) * 0.05, 2)

    # ── TSG_ENTRY_REPEG ── D2/D3 entry pricing ────────
    #
    # WHY (2026-08-10 TSG L1 incident): a SELL entry priced at LTP−1% went
    # unfilled for 22s at the open — LTP was already stale by the time the
    # order hit the book (24650CE, ltp 75.75 → limit 75.00, best bid had
    # dropped below 75). Two fixes, both scoped to ENTRY pricing only:
    #   D2: price sells off BEST BID (buys off BEST ASK) from quote depth —
    #       the touch is live truth; LTP is the last trade, seconds stale
    #       in minute one. LTP remains the fallback when depth is empty.
    #   D3: tiered buffer — 1% is generous on a ₹4 hedge and razor-thin on
    #       a ₹75 short. Premiums >= HIGH_PREMIUM_THRESHOLD get 2%.
    # _protected_limit_price is deliberately UNTOUCHED: exits (place_buy_exit)
    # and every other caller keep today's exact behaviour.

    HIGH_PREMIUM_THRESHOLD = 25.0   # ₹; >= this → wide buffer
    ENTRY_BUFFER_LOW  = 0.01        # 1% for cheap legs (wings)
    ENTRY_BUFFER_HIGH = 0.02        # 2% for rich legs (shorts at open)

    @classmethod
    def _entry_limit_price(cls, ref: float, side: str) -> float:
        """Marketable ENTRY limit from a reference price (best bid for SELL,
        best ask for BUY; LTP fallback), tiered buffer, NFO tick 0.05."""
        buf = (cls.ENTRY_BUFFER_HIGH if ref >= cls.HIGH_PREMIUM_THRESHOLD
               else cls.ENTRY_BUFFER_LOW)
        raw = ref * (1.0 + buf) if side == "BUY" else ref * (1.0 - buf)
        return round(round(raw / 0.05) * 0.05, 2)

    def _resolve_entry_quote(self, symbol: str) -> dict:
        """Full-quote fetch for entry pricing: {"ltp", "bid", "ask"} —
        bid/ask are the best-depth touch, 0.0 when depth is unavailable
        (callers fall back to ltp). Never raises; degraded reads return
        whatever is available (fail-open to the old LTP-only behaviour)."""
        out = {"ltp": 0.0, "bid": 0.0, "ask": 0.0}
        try:
            data_kite = self.broker_manager.get_data_kite()
            if data_kite:
                q = (data_kite.quote(f"NFO:{symbol}") or {}).get(
                    f"NFO:{symbol}", {})
                out["ltp"] = float(q.get("last_price") or 0.0)
                depth = q.get("depth") or {}
                buys  = depth.get("buy") or []
                sells = depth.get("sell") or []
                if buys and float(buys[0].get("price") or 0) > 0:
                    out["bid"] = float(buys[0]["price"])
                if sells and float(sells[0].get("price") or 0) > 0:
                    out["ask"] = float(sells[0]["price"])
        except Exception as e:
            write_audit_log(f"[ZERODHA][ENTRY_QUOTE] depth fetch failed "
                            f"for {symbol}: {e}")
        if out["ltp"] <= 0:
            fallback = self._resolve_ltp(symbol)
            out["ltp"] = float(fallback or 0.0)
        return out

    def fresh_sell_entry_limit(self, symbol: str):
        """Re-peg price for a working SELL entry (D1 loop): best bid
        preferred, LTP fallback. Returns (limit, ref, src) or None."""
        q = self._resolve_entry_quote(symbol)
        ref = q["bid"] if q["bid"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._entry_limit_price(ref, "SELL"), ref,
                "bid" if q["bid"] > 0 else "ltp")

    def fresh_buy_entry_limit(self, symbol: str):
        """Re-peg price for a working BUY entry: best ask preferred."""
        q = self._resolve_entry_quote(symbol)
        ref = q["ask"] if q["ask"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._entry_limit_price(ref, "BUY"), ref,
                "ask" if q["ask"] > 0 else "ltp")
    # ── TSG_ENTRY_REPEG END (pricing helpers) ─────────

    def _resolve_ltp(self, symbol: str) -> Optional[float]:
        """REST-primary LTP fetch (for order placement)."""
        ltp = None
        try:
            data_kite = self.broker_manager.get_data_kite()
            if data_kite:
                quote = data_kite.ltp(f"NFO:{symbol}")
                rest_ltp = quote.get(f"NFO:{symbol}", {}).get("last_price")
                if rest_ltp and rest_ltp > 0:
                    ltp = rest_ltp
        except Exception as e:
            write_audit_log(f"[ZERODHA] REST LTP failed for {symbol}: {e}")

        if not ltp or ltp <= 0:
            ltp = LTPStore.get(symbol)

        return ltp

    # ── BUY entry (LONG - existing behaviour) ─────────

    def place_buy(self, symbol: str, token: int, qty: int):
        self._ensure_trading_enabled()

        if qty <= 0 or qty > MAX_QTY_PER_ORDER:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}")

        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
            try:
                quote = self.broker_manager.get_data_kite().ltp(f"NFO:{symbol}")
                ltp   = quote[f"NFO:{symbol}"]["last_price"]
            except Exception:
                ltp = None

        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price  = self._protected_limit_price(ltp, "BUY")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(f"[ZERODHA-BUY] {symbol} qty={qty} ltp={ltp} limit={limit_price}")

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="BUY", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-BUY-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return order_id, 0.0, qty

    # ── SELL entry (SHORT - NEW for SCALP_V1) ─────────

    def place_sell_entry(self, symbol: str, token: int, qty: int):
        """
        Short entry: SELL the option at a protected limit price.
        Uses REST LTP as primary source (same as place_buy).
        Returns (order_id, limit_price, qty).
        """
        self._ensure_trading_enabled()

        if qty <= 0 or qty > MAX_QTY_PER_ORDER:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}")

        # ── TSG_ENTRY_REPEG ── D2/D3: reference = best bid (touch), LTP
        # fallback; tiered buffer via _entry_limit_price. Shared callers
        # (IC / TMA / PST / SCALP short entries) inherit this — the change
        # is direction-safe: the limit is never LESS marketable than the
        # old LTP−1%.
        q = self._resolve_entry_quote(symbol)
        ltp = q["ltp"]
        ref = q["bid"] if q["bid"] > 0 else ltp
        if not ref or ref <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price  = self._entry_limit_price(ref, "SELL")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(
            f"[ZERODHA-SELL-ENTRY] {symbol} qty={qty} ltp={ltp} "
            f"bid={q['bid']} ref={ref} limit={limit_price}"
        )

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="SELL_ENTRY", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-SELL-ENTRY-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return str(order_id), limit_price, qty

    # ── BUY exit (close SHORT - NEW for SCALP_V1) ─────

    def place_buy_exit(self, symbol: str, qty: int, reason: str) -> str:
        """
        Buy back a short option position to close it.
        REST LTP primary, LTPStore fallback.
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_BUY_EXIT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY_FOR_BUY_EXIT SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"BUY_EXIT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = self._resolve_ltp(symbol)
        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for buy_exit {symbol}")

        limit_price  = self._protected_limit_price(ltp, "BUY")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(
            f"[ZERODHA-BUY-EXIT] {symbol} qty={qty} ltp={ltp} "
            f"limit={limit_price} reason={reason}"
        )

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="BUY_EXIT", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-BUY-EXIT-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return str(order_id)

    # ── AVG PRICE (read - always direct) ──────────────

    def get_last_avg_price(self, order_id: str) -> float:
        kite = self._kite()
        if not kite:
            return 0.0
        try:
            orders = kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return float(o.get("average_price") or 0.0)
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] Avg price fetch failed ERR={e}")
        return 0.0

    # ── ORDER FILL (read - always direct) ─────────────

    def get_order_fill(self, order_id: str) -> Dict:
        """
        Single-call order status + fill detail from the broker order book.

        Returns:
            {
              "status":      <str|None>,   # raw Kite status
              "avg_price":   <float>,      # average fill price (0.0 if none)
              "filled_qty":  <int>,        # quantity filled so far
              "pending_qty": <int>,        # quantity still working
              "found":       <bool>,       # order present in the book
            }

        status is the raw Kite order status, e.g.:
          "COMPLETE"                         -> filled; avg_price is valid
          "REJECTED" / "CANCELLED" / "LAPSED"-> dead; no position resulted
          "OPEN" / "TRIGGER PENDING" / etc.  -> still working
          None (found=False)                 -> order not in the book yet.
                                                The Kite order book can lag by
                                                seconds (occasionally minutes),
                                                so callers MUST treat this as
                                                "pending", never as "rejected".

        filled_qty / pending_qty let callers distinguish a clean cancel
        (filled_qty == 0) from a partial fill (0 < filled_qty < ordered qty)
        at a cancel boundary.
        """
        empty = {
            "status": None, "avg_price": 0.0,
            "filled_qty": 0, "pending_qty": 0, "found": False,
        }
        kite = self._kite()
        if not kite:
            return empty
        try:
            orders = kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return {
                        "status":      o.get("status"),
                        "avg_price":   float(o.get("average_price") or 0.0),
                        "filled_qty":  int(o.get("filled_quantity") or 0),
                        "pending_qty": int(o.get("pending_quantity") or 0),
                        "found":       True,
                    }
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] Order fill fetch failed ERR={e}")
        return empty

    # ── GTT OCO ── direction-aware ─────────────────────

    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
        last_price: float = None,
        direction: str = "LONG",   # "LONG" (BB/HA) or "SHORT" (SCALP_V1)
    ) -> str:
        """
        LONG  (default - BB/HA unchanged):
          trigger_values = [sl_lower, tp_upper]
          orders = SELL at sl_limit + SELL at tp_limit

        SHORT (SCALP_V1 short options):
          trigger_values = [tp_lower, sl_upper]
          orders = BUY at tp_limit + BUY at sl_limit
          (buy back the shorted premium)
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_GTT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_GTT")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"GTT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None:
            raise RuntimeError("LTP unavailable for GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger      = r(sl_price)
        tp_trigger      = r(tp_price) if tp_price and tp_price > 0 else None
        safe_last_price = round(ltp, 2)

        # ── LONG GTT (existing logic - BB/HA) ─────────
        if direction == "LONG":
            if sl_trigger <= 0:
                raise RuntimeError(f"GTT_INVALID_SL SL={sl_trigger}")

            sl_limit = r(sl_price * 0.995)

            if tp_trigger:
                if not (sl_trigger < safe_last_price < tp_trigger):
                    raise RuntimeError(
                        f"Invalid GTT band SL={sl_trigger} LAST={safe_last_price} TP={tp_trigger}"
                    )
                tp_limit   = r(tp_price * 0.997)
                gtt_params = dict(
                    trigger_type=kite.GTT_TYPE_OCO,
                    tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    trigger_values=[sl_trigger, tp_trigger],
                    last_price=safe_last_price,
                    orders=[
                        {
                            "transaction_type": kite.TRANSACTION_TYPE_SELL,
                            "quantity": qty,
                            "order_type": kite.ORDER_TYPE_LIMIT,
                            "price": sl_limit,
                            "product": kite.PRODUCT_NRML,
                        },
                        {
                            "transaction_type": kite.TRANSACTION_TYPE_SELL,
                            "quantity": qty,
                            "order_type": kite.ORDER_TYPE_LIMIT,
                            "price": tp_limit,
                            "product": kite.PRODUCT_NRML,
                        },
                    ],
                )
                log_suffix = f"LONG SL={sl_trigger}/{sl_limit} TP={tp_trigger}/{tp_limit}"
            else:
                if not (sl_trigger < safe_last_price):
                    raise RuntimeError(
                        f"Invalid GTT band SL={sl_trigger} LAST={safe_last_price}"
                    )
                gtt_params = dict(
                    trigger_type=kite.GTT_TYPE_SINGLE,
                    tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    trigger_values=[sl_trigger],
                    last_price=safe_last_price,
                    orders=[
                        {
                            "transaction_type": kite.TRANSACTION_TYPE_SELL,
                            "quantity": qty,
                            "order_type": kite.ORDER_TYPE_LIMIT,
                            "price": sl_limit,
                            "product": kite.PRODUCT_NRML,
                        },
                    ],
                )
                log_suffix = f"LONG SL={sl_trigger}/{sl_limit}"

        # ── SHORT GTT (NEW - SCALP_V1) ─────────────────
        else:
            # For short: sl_price > entry > tp_price
            # lower trigger = TP (buy back cheap = profit)
            # upper trigger = SL (buy back expensive = loss)
            if not tp_trigger:
                raise RuntimeError("SHORT GTT requires a valid tp_price")

            if not (tp_trigger < safe_last_price < sl_trigger):
                raise RuntimeError(
                    f"Invalid SHORT GTT band TP={tp_trigger} LAST={safe_last_price} SL={sl_trigger}"
                )

            # BUY limits: TP limit slightly above trigger (willing to pay a little more)
            #             SL limit slightly above trigger (must fill fast on stop)
            tp_limit = r(tp_trigger * 1.003)
            sl_limit = r(sl_trigger * 1.005)

            gtt_params = dict(
                trigger_type=kite.GTT_TYPE_OCO,
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NFO,
                trigger_values=[tp_trigger, sl_trigger],   # lower=TP, upper=SL
                last_price=safe_last_price,
                orders=[
                    {   # lower trigger -> TP hit -> buy back at profit
                        "transaction_type": kite.TRANSACTION_TYPE_BUY,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": tp_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                    {   # upper trigger -> SL hit -> buy back to cut loss
                        "transaction_type": kite.TRANSACTION_TYPE_BUY,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": sl_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                ],
            )
            log_suffix = (
                f"SHORT TP={tp_trigger}/{tp_limit} SL={sl_trigger}/{sl_limit}"
            )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gtt_id = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gtt_id)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gtt_id = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gtt_id)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt,
            direct_fn=_direct_gtt,
            op_name="GTT", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} {log_suffix}"
        )
        return gtt_id
    
    # ── TP-ONLY GTT (LONG) — HA_V1 backstop ────────────
    # ── HA_TP_GTT BEGIN ──
    def place_gtt_tp_only_long(
        self,
        symbol: str,
        qty: int,
        tp_price: float,
        last_price: float = None,
    ) -> str:
        """
        Place a SINGLE (non-OCO) GTT that SELLS the LONG option position when
        price rises to tp_price. This is HA_V1's broker-side TP backstop: it
        fires even if the app is blind (lost tick subscription, crashed, or
        killed), which is the failure mode that left a position naked past TP.

        WHY TP-ONLY (no SL leg): HA's SL is evaluated on CANDLE CLOSE only —
        intra-candle wicks below SL must NOT exit. A broker SL-GTT triggers on
        the live tick, so it would fire on a wick and violate HA's SL semantics.
        The app keeps candle-close SL; the broker only holds the tick-based TP.
        The two never conflict because they protect different exit conditions.

        Distinct from place_gtt_oco():
          * place_gtt_oco LONG requires sl_price > 0 (raises GTT_INVALID_SL
            otherwise) and its SINGLE path is SL-only. It cannot express a
            TP-only LONG GTT, so this is a separate, additive method. BB / HA-OCO
            / SCALP_V1 / V3 GTT paths are untouched.

        Returns the GTT id (str). Raises on invalid band / broker-not-ready so
        the caller can alert and fall back to app-side monitoring.
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_TP_GTT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_TP_GTT")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"TP_GTT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None or ltp <= 0:
            raise RuntimeError("LTP unavailable for TP GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        tp_trigger = r(tp_price)
        safe_last  = round(ltp, 2)

        if tp_trigger <= 0:
            raise RuntimeError(f"TP_GTT_INVALID_TP TP={tp_trigger}")

        # A GTT to SELL on the way UP requires the trigger ABOVE the last price.
        # (If price is already at/above TP, the app-side tick monitor should have
        # exited already; refuse rather than place a would-trigger-instantly GTT.)
        if not (safe_last < tp_trigger):
            raise RuntimeError(
                f"Invalid TP-GTT band LAST={safe_last} !< TP={tp_trigger}"
            )

        # Sell limit slightly below the trigger so it fills promptly on trigger
        # (same 0.997 factor the LONG-OCO TP leg uses).
        tp_limit = r(tp_price * 0.997)

        gtt_params = dict(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[tp_trigger],
            last_price=safe_last,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "price": tp_limit,
                    "product": kite.PRODUCT_NRML,
                },
            ],
        )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt, direct_fn=_direct_gtt,
            op_name="GTT_TP_ONLY", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"TP_ONLY_LONG TP={tp_trigger}/{tp_limit} last={safe_last}"
        )
        return gtt_id
    # ── HA_TP_GTT END ──

    # ── IC_V1_GTT BEGIN ──
    def place_gtt_sl_only_short(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        last_price: float = None,
        limit_buffer: float = 1.003,
    ) -> str:
        """
        Place a SINGLE (non-OCO) GTT that BUYS BACK a SHORT option position
        when price RISES to sl_price. IC_V1's broker-side SL: its shorts
        default to tp_val=0 and place_gtt_oco(direction="SHORT") hard-raises
        without a valid tp_price, so this is a separate, ADDITIVE method —
        BB / HA / SCALP_V1..V5 GTT paths are untouched.

        Used twice by IC_V1: initial 42% SL protection at entry, and the
        Move-To-Cost re-pin (new GTT at the survivor short's entry price).

        Returns the GTT id (str). Raises on invalid band / broker-not-ready
        so the caller can fall back (IC: market-out per locked decision D5).
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_SL_GTT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_SL_GTT")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"SL_GTT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None or ltp <= 0:
            raise RuntimeError("LTP unavailable for SL GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger = r(sl_price)
        safe_last  = round(ltp, 2)

        if sl_trigger <= 0:
            raise RuntimeError(f"SL_GTT_INVALID_SL SL={sl_trigger}")

        # A GTT to BUY BACK on the way UP requires the trigger ABOVE the last
        # price. If price is already at/through the stop, a resting GTT is the
        # wrong tool — refuse so the caller market-outs instead (IC D5).
        if not (safe_last < sl_trigger):
            raise RuntimeError(
                f"Invalid SL-GTT band LAST={safe_last} !< SL={sl_trigger}"
            )

        # Buy limit ABOVE the trigger so it fills on trigger. limit_buffer
        # defaults to the historical 1.003; IC_V2 passes a config-driven
        # wider buffer (gtt_limit_buffer_pct, default 5%) — gap defence
        # layer 1: a 0.3% limit rests off-market on any fast move and the
        # consumed GTT leaves the short naked (see ic_gtt_monitor escalation
        # for layer 2).
        sl_limit = r(sl_trigger * max(1.0, float(limit_buffer or 1.003)))

        gtt_params = dict(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[sl_trigger],
            last_price=safe_last,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_BUY,
                    "quantity": qty,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "price": sl_limit,
                    "product": kite.PRODUCT_NRML,
                },
            ],
        )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt, direct_fn=_direct_gtt,
            op_name="GTT_SL_ONLY_SHORT", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"SL_ONLY_SHORT SL={sl_trigger}/{sl_limit} last={safe_last}"
        )
        return gtt_id

    def place_gtt_sl_only_long(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        last_price: float = None,
        limit_buffer: float = 1.003,
    ) -> str:
        """
        ── IC_V2 (2026-07-26) ── Place a SINGLE (non-OCO) GTT that SELLS a
        LONG option position when price FALLS to sl_price. Used by IC_V1's
        ·ADJ adjustment legs (BUY with 25% SL, tp_val=0 by default —
        place_gtt_oco hard-raises without a valid tp_price, so this is a
        separate, ADDITIVE method mirroring place_gtt_sl_only_short).
        BB / HA / SCALP_V1..V5 / PST / TMA GTT paths are untouched.

        Sell limit slightly BELOW the trigger (limit_buffer divides) so the
        exit fills on trigger; the same triggered-but-unfilled escalation in
        ic_gtt_monitor covers the gap-down-past-buffer case.

        Returns the GTT id (str). Raises on invalid band / broker-not-ready
        so the caller can fall back (IC: tick-monitor-only + alert).
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_SL_GTT_LONG SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_SL_GTT_LONG")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"SL_GTT_LONG_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None or ltp <= 0:
            raise RuntimeError("LTP unavailable for SL GTT (long)")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger = r(sl_price)
        safe_last  = round(ltp, 2)

        if sl_trigger <= 0:
            raise RuntimeError(f"SL_GTT_LONG_INVALID_SL SL={sl_trigger}")

        # A GTT to SELL on the way DOWN requires the trigger BELOW the last
        # price. If price is already at/through the stop, a resting GTT is
        # the wrong tool — refuse so the caller falls back.
        if not (safe_last > sl_trigger):
            raise RuntimeError(
                f"Invalid SL-GTT-LONG band LAST={safe_last} !> SL={sl_trigger}"
            )

        buf = max(1.0, float(limit_buffer or 1.003))
        sl_limit = r(max(0.05, sl_trigger / buf))

        gtt_params = dict(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[sl_trigger],
            last_price=safe_last,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "price": sl_limit,
                    "product": kite.PRODUCT_NRML,
                },
            ],
        )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt, direct_fn=_direct_gtt,
            op_name="GTT_SL_ONLY_LONG", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"SL_ONLY_LONG SL={sl_trigger}/{sl_limit} last={safe_last}"
        )
        return gtt_id
    # ── IC_V1_GTT END ──

    # ── IC_V1_MARGIN BEGIN ──
    def get_basket_margin(self, basket: list) -> dict:
        """
        IC_V1 margin guard (locked decision D8). basket: list of
        {"symbol","qty","transaction_type"} for the full 4-leg condor.
        Returns {"required": float, "available": float}.

        RAISES on any broker/API failure — the CALLER treats an exception as
        advisory-fail-open (cannot-compute != shortfall). Only a clean read
        showing required > available blocks entry.
        """
        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_MARGIN")

        params = [
            {
                "exchange":         "NFO",
                "tradingsymbol":    o["symbol"],
                "transaction_type": o["transaction_type"],
                "variety":          "regular",
                "product":          "NRML",
                "order_type":       "MARKET",
                "quantity":         int(o["qty"]),
                "price":            0,
                "trigger_price":    0,
            }
            for o in basket
        ]

        res = kite.basket_order_margins(params, consider_positions=True)
        required = float(
            ((res or {}).get("final") or {}).get("total")
            or ((res or {}).get("initial") or {}).get("total")
            or 0.0
        )

        m = kite.margins("equity") or {}
        avail = m.get("available") or {}
        available = float(
            avail.get("live_balance")
            or avail.get("cash")
            or m.get("net")
            or 0.0
        )

        write_audit_log(
            f"[IC][MARGIN_CHECK] required={required:.0f} available={available:.0f}"
        )
        return {"required": required, "available": available}
    # ── IC_V1_MARGIN END ──

    # ── existing methods preserved unchanged ──────────

    def cancel_order(self, order_id: str, symbol: str = ""):
        # ── TSG_ENTRY_REPEG ── D4: the op log used to render as
        # "[RELAY][CANCEL_ORDER]  - SUCCESS" (blank subject) — grep-hostile
        # during the 2026-08-10 incident. symbol is optional so every
        # existing cancel_order(oid) call site keeps working; order_id is
        # always in the log subject either way.
        kite = self._kite()
        if not kite:
            return
        self._relay_call(
            relay_fn=lambda r: r.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id),
            direct_fn=lambda: kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id),
            op_name="CANCEL_ORDER",
            symbol=f"{symbol} order_id={order_id}".strip(),
        )

    # ── TSG_ENTRY_REPEG ── D1: relay-routed order modify ─────────────
    def modify_order(self, order_id: str, price: float,
                     symbol: str = "") -> Optional[str]:
        """Modify a working LIMIT order's price in place (re-peg). Keeps
        the same order_id on Kite — no cancel/re-place orphan window.
        Returns the order_id on success, None on failure (caller decides
        whether to keep waiting on the old price or abort)."""
        kite = self._kite()
        if not kite:
            return None
        params = dict(variety=kite.VARIETY_REGULAR,
                      order_id=order_id, price=price)
        try:
            self._relay_call(
                relay_fn=lambda r: r.modify_order(**params),
                direct_fn=lambda: kite.modify_order(**params),
                op_name="MODIFY_ORDER",
                symbol=f"{symbol} order_id={order_id} price={price}".strip(),
            )
            write_audit_log(
                f"[ZERODHA-MODIFY] ORDER_ID={order_id} SYMBOL={symbol} "
                f"NEW_LIMIT={price}")
            return str(order_id)
        except Exception as e:
            write_audit_log(
                f"[ZERODHA-MODIFY-FAIL] ORDER_ID={order_id} "
                f"SYMBOL={symbol} price={price} ERR={e}")
            return None

    def get_orders(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        return kite.orders()

    def get_open_positions(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        positions = kite.positions()
        return [p for p in positions.get("net", []) if p.get("quantity", 0) != 0]

    def place_sl(self, symbol: str, qty: int, sl_price: float) -> str:
        raise RuntimeError("place_sl() not supported in GTT-only mode")

    def place_exit(self, symbol: str, qty: int, reason: str) -> str:
        raise RuntimeError("place_exit() not supported in GTT-only mode")

    def cancel_gtt(self, gtt_id: str):
        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_GTT_CANCEL")
        try:
            self._relay_call(
                relay_fn=lambda r: r.cancel_gtt(int(gtt_id)),
                direct_fn=lambda: kite.delete_gtt(int(gtt_id)),
                op_name="CANCEL_GTT",
            )
            write_audit_log(f"[ZERODHA-GTT-CANCELLED] GTT_ID={gtt_id}")
        except Exception as e:
            write_audit_log(f"[ZERODHA-GTT-CANCEL-WARN] GTT_ID={gtt_id} ERR={e}")
            raise

    def cancel_gtt_verified(self, gtt_id: str, retries: int = 4) -> bool:
            """
            Cancel a GTT and VERIFY it is actually gone at the broker.

            Zerodha's delete_gtt can return success while the trigger remains
            armed (observed live). A live orphan GTT on a flat position can fire
            an unintended order, so we re-fetch get_gtts() and confirm the id is
            no longer ACTIVE.

            "Armed" means status == "active" ONLY. A "triggered" GTT has already
            fired (did its job) and merely lingers in the list briefly; it is
            NOT an orphan and must not be treated as one — doing so produced
            false CRITICALs whenever a normal SL fired.

            Returns True if the GTT is confirmed gone/spent, False only if it is
            still ACTIVE after all retries (caller alerts + treats the position
            as unprotected). Never raises.
            """
            target = str(gtt_id)

            def _still_armed() -> bool:
                # Only ACTIVE = armed-and-dangerous. "triggered" = already fired.
                for g in self.get_gtts():
                    if str(g.get("id")) == target:
                        return g.get("status") == "active"
                return False

            for attempt in range(retries + 1):
                try:
                    self.cancel_gtt(gtt_id)
                except Exception as e:
                    write_audit_log(f"[GTT_VERIFY] cancel attempt {attempt} raised ERR={e}")

                # Give the broker time to reflect the delete before checking.
                # Zerodha's GTT list is eventually consistent and can lag the
                # cancel by a few seconds, so the first check waits a bit longer.
                time.sleep(1.5)

                if not _still_armed():
                    write_audit_log(f"[GTT_VERIFY] CONFIRMED_GONE GTT_ID={target} attempt={attempt}")
                    return True

                write_audit_log(
                    f"[GTT_VERIFY] STILL_ARMED GTT_ID={target} attempt={attempt} — retrying"
                )

            write_audit_log(f"[GTT_VERIFY][CRITICAL] GTT_ID={target} STILL ARMED after {retries+1} attempts")
            return False

    def place_market_sell(self, symbol: str, qty: int) -> str:
        """EOD square-off for LONG positions (BB/HA). Uses REST LTP primary."""
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_SELL SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY_FOR_SELL SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"SELL_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = None
        try:
            data_kite = self.broker_manager.get_data_kite()
            if data_kite:
                quote   = data_kite.ltp(f"NFO:{symbol}")
                rest_ltp = quote.get(f"NFO:{symbol}", {}).get("last_price")
                if rest_ltp and rest_ltp > 0:
                    ltp = rest_ltp
                    write_audit_log(f"[ZERODHA-SELL] LTP from REST: {symbol} ltp={ltp}")
        except Exception as e:
            write_audit_log(f"[ZERODHA-SELL] REST LTP failed for {symbol}: {e}")

        if not ltp or ltp <= 0:
            ltp = LTPStore.get(symbol)
            if ltp and ltp > 0:
                write_audit_log(f"[ZERODHA-SELL] LTP from LTPStore: {symbol} ltp={ltp}")

        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price  = self._protected_limit_price(ltp, "SELL")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(f"[ZERODHA-SELL] {symbol} qty={qty} ltp={ltp} limit={limit_price}")

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="SELL", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-SELL-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return str(order_id)