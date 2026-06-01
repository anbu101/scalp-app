"""
zerodha_executor.py
====================
Zerodha Order Executor with optional OCI relay.

Changes vs original (doc 169):
  - place_sell_entry()  : NEW — SELL entry for SCALP_V1 short positions
  - place_buy_exit()    : NEW — BUY back to close a short position
  - place_gtt_oco()     : added direction="LONG"|"SHORT" param (default "LONG")
      LONG  → existing SELL GTT OCO logic (BB/HA unchanged)
      SHORT → inverted BUY GTT OCO logic (SCALP_V1 short)
              trigger_values = [tp_lower, sl_upper]
              orders = BUY at tp_limit / BUY at sl_limit

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
            write_audit_log(f"[RELAY] Order relay ENABLED → {hosts}")
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
                write_audit_log(f"[RELAY][{op_name}] Skipping {host} — missing url/secret")
                continue

            try:
                relay  = RelayClient(url=url, secret=secret,
                                     api_key=kite.api_key,
                                     access_token=kite.access_token)
                result = relay_fn(relay)
                write_audit_log(f"[RELAY][{op_name}] {symbol} — SUCCESS via {host}")
                return result

            except _RELAY_TRANSIENT_ERRORS as e:
                write_audit_log(
                    f"[RELAY][{op_name}] {host} unreachable ({type(e).__name__}) — next relay"
                )
                continue
            except Exception as e:
                write_audit_log(f"[RELAY][{op_name}] {host} error: {e} — next relay")
                continue

        write_audit_log(
            f"[RELAY][{op_name}][ALL_FAILED] {symbol} — direct fallback"
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

    def _kite(self) -> Optional[KiteConnect]:
        if not self.broker_manager.is_trade_ready():
            write_audit_log("[ZERODHA_EXECUTOR] Not ready — refreshing from disk")
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

    # ── BUY entry (LONG — existing behaviour) ─────────

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

    # ── SELL entry (SHORT — NEW for SCALP_V1) ─────────

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

        ltp = self._resolve_ltp(symbol)
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

        write_audit_log(
            f"[ZERODHA-SELL-ENTRY] {symbol} qty={qty} ltp={ltp} limit={limit_price}"
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

    # ── BUY exit (close SHORT — NEW for SCALP_V1) ─────

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

    # ── AVG PRICE (read — always direct) ──────────────

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
        LONG  (default — BB/HA unchanged):
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

        # ── LONG GTT (existing logic — BB/HA) ─────────
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

        # ── SHORT GTT (NEW — SCALP_V1) ─────────────────
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
                    {   # lower trigger → TP hit → buy back at profit
                        "transaction_type": kite.TRANSACTION_TYPE_BUY,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": tp_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                    {   # upper trigger → SL hit → buy back to cut loss
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

    # ── existing methods preserved unchanged ──────────

    def cancel_order(self, order_id: str):
        kite = self._kite()
        if not kite:
            return
        self._relay_call(
            relay_fn=lambda r: r.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id),
            direct_fn=lambda: kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id),
            op_name="CANCEL_ORDER",
        )

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