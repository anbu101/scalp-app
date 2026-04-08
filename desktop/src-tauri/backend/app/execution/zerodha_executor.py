"""
zerodha_executor.py
====================
Zerodha Order Executor with optional OCI relay.

When RELAY_URL is configured in relay_config.json:
  - place_order / place_gtt / cancel_gtt / cancel_order
    are POSTed to the OCI relay (which has the Zerodha-registered static IP)

When relay is not configured (or disabled):
  - Falls back to direct kite calls (original behaviour)

All READ operations (positions, orders, LTP, GTT status) are ALWAYS direct —
Zerodha only validates the static IP for order placement endpoints.
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
        if cfg.get("enabled") and cfg.get("url") and cfg.get("secret"):
            _relay_cfg = cfg
            write_audit_log(
                f"[RELAY] Order relay ENABLED → {cfg['url']}"
            )
        else:
            write_audit_log("[RELAY] relay_config.json found but disabled or incomplete")
    except Exception as e:
        write_audit_log(f"[RELAY] Failed to load relay_config.json ERR={e}")

    return _relay_cfg


def relay_is_active() -> bool:
    return _load_relay_config() is not None


# --------------------------------------------------
# RELAY HTTP CLIENT
# --------------------------------------------------

class RelayClient:
    """
    Thin HTTP client for the OCI order relay.
    Sends api_key + access_token with every request —
    the relay holds no credentials of its own.
    """

    TIMEOUT = 10  # seconds

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
            f"{self.base_url}{path}",
            json=payload,
            headers=self._headers,
            timeout=self.TIMEOUT,
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
        result = self._post("/relay/place_gtt", kwargs)
        return result

    def cancel_gtt(self, trigger_id: int):
        resp = requests.delete(
            f"{self.base_url}/relay/gtt/{trigger_id}",
            params={
                "api_key":      self.api_key,
                "access_token": self.access_token,
            },
            headers=self._headers,
            timeout=self.TIMEOUT,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Relay cancel_gtt failed HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def cancel_order(self, variety: str, order_id: str):
        self._post("/relay/cancel_order", {
            "variety":  variety,
            "order_id": order_id,
        })


# --------------------------------------------------
# EXECUTOR
# --------------------------------------------------

class TradingDisabledError(RuntimeError):
    pass


class ZerodhaOrderExecutor(BaseOrderExecutor):
    """
    Zerodha Order Executor (FINAL AUTHORITY)

    HARD RULES:
    - Executor ENFORCES trade_on
    - Executor ENFORCES broker readiness (NO FORCED REFRESH)
    - Executor ENFORCES INSTRUMENT LOT SIZE (AUTHORITATIVE)
    - ORDER placement goes through OCI relay when relay_config.json is present
    """

    def __init__(self, broker_manager: ZerodhaManager):
        self.broker_manager    = broker_manager
        self._instrument_cache: Dict[str, int] = {}

    # -------------------------
    # RELAY HELPER
    # -------------------------

    def _relay(self) -> Optional[RelayClient]:
        """
        Returns a RelayClient if relay is configured, else None.
        Called only for order-placement methods.
        """
        cfg = _load_relay_config()
        if not cfg:
            return None

        kite = self._kite()
        if not kite:
            return None

        return RelayClient(
            url=cfg["url"],
            secret=cfg["secret"],
            api_key=kite.api_key,
            access_token=kite.access_token,
        )

    # -------------------------
    # INTERNAL HELPERS
    # -------------------------

    def get_gtts(self) -> List[Dict]:
        # READ — always direct, never relayed
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
            raise RuntimeError(
                f"INSTRUMENT_FETCH_FAILED SYMBOL={symbol} ERR={e}"
            )

        for inst in instruments:
            if inst.get("tradingsymbol") == symbol:
                lot_size = int(inst.get("lot_size") or 0)
                if lot_size <= 0:
                    break
                self._instrument_cache[symbol] = lot_size
                return lot_size

        raise RuntimeError(f"LOT_SIZE_NOT_FOUND SYMBOL={symbol}")

    # -------------------------
    # MARKET PROTECTION PRICE
    # -------------------------

    @staticmethod
    def _protected_limit_price(ltp: float, side: str) -> float:
        """
        Zerodha now requires market protection on all market orders (SEBI Apr 1).
        We place a LIMIT order at 1% away from LTP in the trade direction —
        tight enough to fill immediately in normal conditions, but with a
        price cap that satisfies the exchange requirement.

        BUY  → cap at LTP * 1.01  (willing to pay up to 1% above)
        SELL → floor at LTP * 0.99 (willing to sell down to 1% below)

        Price is rounded to nearest 0.05 (NFO tick size).
        """
        if side == "BUY":
            raw = ltp * 1.01
        else:
            raw = ltp * 0.99
        return round(round(raw / 0.05) * 0.05, 2)

    # -------------------------
    # BUY
    # -------------------------

    def place_buy(self, symbol: str, token: int, qty: int):
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")
        if qty > MAX_QTY_PER_ORDER:
            raise RuntimeError("Qty exceeds MAX_QTY_PER_ORDER")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY_IN_EXECUTOR SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol)
        if qty % lot_size != 0:
            raise RuntimeError(
                f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        # SEBI Apr 1: market orders require market protection.
        # Use LIMIT order at 1% above LTP to guarantee fill.
        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
            # fallback: fetch via REST if WS not yet populated
            try:
                quote = self.broker_manager.get_data_kite().ltp(f"NFO:{symbol}")
                ltp = quote[f"NFO:{symbol}"]["last_price"]
            except Exception:
                ltp = None

        if not ltp or ltp <= 0:
            raise RuntimeError(
                f"Cannot place protected limit order — LTP unavailable for {symbol}"
            )

        limit_price = self._protected_limit_price(ltp, "BUY")

        relay = self._relay()

        write_audit_log(
            f"[ZERODHA-BUY] {symbol} qty={qty} "
            f"ltp={ltp} limit={limit_price} "
            f"via={'RELAY' if relay else 'DIRECT'}"
        )

        if relay:
            order_id = relay.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_BUY,
                quantity=qty,
                order_type=kite.ORDER_TYPE_LIMIT,
                price=limit_price,
                product=kite.PRODUCT_NRML,
            )
        else:
            order_id = kite.place_order(
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
            f"[ZERODHA-BUY-PLACED] "
            f"ORDER_ID={order_id} SYMBOL={symbol} QTY={qty} "
            f"LIMIT={limit_price} via={'RELAY' if relay else 'DIRECT'}"
        )

        return order_id, 0.0, qty

    # -------------------------
    # AVG PRICE FETCH (READ — always direct)
    # -------------------------

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

    # -------------------------
    # GTT OCO
    # -------------------------

    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
        last_price: float = None,
    ) -> str:
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

        sl_trigger  = r(sl_price)
        tp_trigger  = r(tp_price) if tp_price and tp_price > 0 else None

        if sl_trigger <= 0:
            raise RuntimeError(
                f"GTT_INVALID_SL SL={sl_trigger} — sl_pct must be non-zero"
            )

        sl_limit        = r(sl_price * 0.995)
        safe_last_price = round(ltp, 2)

        relay = self._relay()

        if tp_trigger:
            if not (sl_trigger < safe_last_price < tp_trigger):
                raise RuntimeError(
                    f"Invalid GTT band SL={sl_trigger} LAST={safe_last_price} TP={tp_trigger}"
                )

            tp_limit = r(tp_price * 0.997)

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

            log_suffix = f"SL={sl_trigger}/{sl_limit} TP={tp_trigger}/{tp_limit}"

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

            log_suffix = f"SL={sl_trigger}/{sl_limit}"

        if relay:
            write_audit_log(
                f"[RELAY][GTT] {symbol} {log_suffix} → {_relay_cfg['url']}"
            )
            result  = relay.place_gtt(**gtt_params)
            gtt_id  = result.get("trigger_id", result)
        else:
            result  = kite.place_gtt(**gtt_params)
            gtt_id  = result.get("trigger_id", result) if isinstance(result, dict) else result

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"{log_suffix} via={'RELAY' if relay else 'DIRECT'}"
        )

        return str(gtt_id)

    # -------------------------
    # SAFETY
    # -------------------------

    def cancel_order(self, order_id: str):
        kite = self._kite()
        if not kite:
            return

        relay = self._relay()
        if relay:
            relay.cancel_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id,
            )
        else:
            kite.cancel_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id,
            )

    def get_orders(self) -> List[Dict]:
        # READ — always direct
        kite = self._kite()
        if not kite:
            return []
        return kite.orders()

    def get_open_positions(self) -> List[Dict]:
        # READ — always direct
        kite = self._kite()
        if not kite:
            return []
        positions = kite.positions()
        return [
            p for p in positions.get("net", [])
            if p.get("quantity", 0) != 0
        ]

    def place_sl(self, symbol: str, qty: int, sl_price: float) -> str:
        raise RuntimeError("place_sl() not supported in GTT-only mode")

    def place_exit(self, symbol: str, qty: int, reason: str) -> str:
        raise RuntimeError("place_exit() not supported in GTT-only mode")

    # -------------------------
    # GTT CANCEL
    # -------------------------

    def cancel_gtt(self, gtt_id: str):
        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_GTT_CANCEL")

        relay = self._relay()
        try:
            if relay:
                write_audit_log(
                    f"[RELAY][CANCEL_GTT] GTT_ID={gtt_id} → {_relay_cfg['url']}"
                )
                relay.cancel_gtt(int(gtt_id))
            else:
                kite.delete_gtt(int(gtt_id))

            write_audit_log(
                f"[ZERODHA-GTT-CANCELLED] GTT_ID={gtt_id} "
                f"via={'RELAY' if relay else 'DIRECT'}"
            )
        except Exception as e:
            write_audit_log(
                f"[ZERODHA-GTT-CANCEL-WARN] GTT_ID={gtt_id} ERR={e}"
            )
            raise

    # -------------------------
    # MARKET SELL
    # -------------------------

    def place_market_sell(self, symbol: str, qty: int) -> str:
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

        # SEBI Apr 1: market orders require market protection.
        # Use LIMIT order at 1% below LTP — will fill immediately
        # at market but satisfies the exchange protection requirement.
        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
            try:
                quote = self.broker_manager.get_data_kite().ltp(f"NFO:{symbol}")
                ltp = quote[f"NFO:{symbol}"]["last_price"]
            except Exception:
                ltp = None

        if not ltp or ltp <= 0:
            raise RuntimeError(
                f"Cannot place protected limit sell — LTP unavailable for {symbol}"
            )

        limit_price = self._protected_limit_price(ltp, "SELL")

        relay = self._relay()

        write_audit_log(
            f"[ZERODHA-SELL] {symbol} qty={qty} "
            f"ltp={ltp} limit={limit_price} "
            f"via={'RELAY' if relay else 'DIRECT'}"
        )

        if relay:
            order_id = relay.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=qty,
                order_type=kite.ORDER_TYPE_LIMIT,
                price=limit_price,
                product=kite.PRODUCT_NRML,
            )
        else:
            order_id = kite.place_order(
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
            f"[ZERODHA-SELL-PLACED] "
            f"ORDER_ID={order_id} SYMBOL={symbol} QTY={qty} "
            f"LIMIT={limit_price} via={'RELAY' if relay else 'DIRECT'}"
        )

        return str(order_id)