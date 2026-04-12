"""
zerodha_executor.py
====================
Zerodha Order Executor with optional OCI relay.

When RELAY_URL is configured in relay_config.json:
  - place_order / place_gtt / cancel_gtt / cancel_order
    are POSTed to the OCI relay (which has the Zerodha-registered static IP)

When relay is not configured (or disabled):
  - Falls back to direct kite calls (original behaviour)

DIRECT FALLBACK:
  If a relay call raises ReadTimeout or ConnectionError, the executor
  automatically retries the SAME call directly via kite (bypassing the relay).
  This ensures orders are never dropped due to relay freezes during trading.
  The fallback is logged clearly so you can see it in audit logs.

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
from app.services.relay_deployer import _active_relay

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
        if cfg.get("enabled") and cfg.get("relays"):
            _relay_cfg = cfg
            hosts = [r.get("host") for r in cfg["relays"]]
            write_audit_log(
                f"[RELAY] Order relay ENABLED → {hosts}"
            )
        else:
            write_audit_log("[RELAY] relay_config.json found but disabled or incomplete")
    except Exception as e:
        write_audit_log(f"[RELAY] Failed to load relay_config.json ERR={e}")

    return _relay_cfg


def relay_is_active() -> bool:
    return _load_relay_config() is not None


def _invalidate_relay_cache():
    """
    Called by relay_deployer after writing a new relay_config.json
    so the executor picks it up without restarting the backend.
    """
    global _relay_cfg
    _relay_cfg = None
    write_audit_log("[RELAY] Config cache invalidated — will reload on next order")


# --------------------------------------------------
# RELAY HTTP CLIENT
# --------------------------------------------------

class RelayClient:
    """
    Thin HTTP client for the OCI order relay.
    Sends api_key + access_token with every request —
    the relay holds no credentials of its own.
    """

    TIMEOUT = 3  # seconds

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
# RELAY ERRORS THAT WARRANT A DIRECT FALLBACK
# --------------------------------------------------

# These are transient infrastructure failures — the relay is frozen or
# unreachable, but Zerodha itself is fine.  We retry directly so that
# orders are never dropped because of relay state.
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
    """
    Zerodha Order Executor (FINAL AUTHORITY)

    Order placement routing:
      1. If relay is configured and reachable  → relay (static IP)
      2. If relay times out / connection fails  → direct kite call (fallback)
      3. If relay not configured               → direct kite call

    The fallback is automatic and silent-safe: it logs clearly but never
    drops an order just because the relay is momentarily frozen.
    """

    def __init__(self, broker_manager: ZerodhaManager):
        self.broker_manager    = broker_manager
        self._instrument_cache: Dict[str, int] = {}

    # -------------------------
    # RELAY HELPER
    # -------------------------

    def _relay(self) -> Optional[RelayClient]:
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
    # DIRECT FALLBACK WRAPPER
    # -------------------------

    def _relay_call(self, relay_fn, direct_fn, op_name: str, symbol: str = ""):
        cfg = _load_relay_config()

        if not cfg or not cfg.get("relays"):
            return direct_fn()

        kite = self._kite()
        if not kite:
            return direct_fn()

        last_error = None

        ordered_relays = []

        primary = None
        secondary = []

        for r in cfg["relays"]:
            if r.get("is_primary"):
                primary = r
            else:
                secondary.append(r)

        # 🔥 ALWAYS TRY PRIMARY FIRST
        if primary:
            ordered_relays.append(primary)

        # 🔥 ONLY TRY SECONDARY IF PRIMARY FAILS
        ordered_relays.extend(secondary)


        last_error = None

        for relay_cfg in ordered_relays:
            host = relay_cfg.get("host")
            url  = relay_cfg.get("url")

            try:
                relay = RelayClient(
                    url=url,
                    secret=relay_cfg.get("secret"),
                    api_key=kite.api_key,
                    access_token=kite.access_token,
                )

                write_audit_log(f"[RELAY][{op_name}] Trying {host} (active={host == _active_relay})")

                result = relay_fn(relay)

                write_audit_log(f"[RELAY][{op_name}] SUCCESS via {host}")

                if host != _active_relay:
                    write_audit_log(f"[RELAY_FAILOVER][EXECUTOR] Using secondary relay → {host}")

                # 🔥 UPDATE ACTIVE RELAY ON SUCCESS (REAL GLOBAL UPDATE)
                import app.services.relay_deployer as relay_module
                relay_module._active_relay = host

                return result

            except _RELAY_TRANSIENT_ERRORS as e:
                write_audit_log(
                    f"[RELAY][{op_name}] {host} unreachable ({type(e).__name__})"
                )
                last_error = e
                continue

            except Exception as e:
                write_audit_log(
                    f"[RELAY][{op_name}] {host} error: {e}"
                )
                last_error = e
                continue

        # All relays failed → fallback to direct
        write_audit_log(
            f"[RELAY_FAILOVER][EXECUTOR] ALL RELAYS FAILED → DIRECT FALLBACK"
        )

        result = direct_fn()

        write_audit_log(
            f"[RELAY][{op_name}] DIRECT SUCCESS (fallback)"
        )

        return result

    # -------------------------
    # INTERNAL HELPERS
    # -------------------------

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
        SEBI market protection: LIMIT order 1% away from LTP.
        BUY  → cap at LTP * 1.01
        SELL → floor at LTP * 0.99
        Rounded to nearest 0.05 (NFO tick size).
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

        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
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

        write_audit_log(
            f"[ZERODHA-BUY] {symbol} qty={qty} ltp={ltp} limit={limit_price}"
        )

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

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="BUY",
            symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-BUY-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
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

        def _direct_gtt():
            result  = kite.place_gtt(**gtt_params)
            gtt_id  = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gtt_id)

        def _relay_gtt(relay):
            result  = relay.place_gtt(**gtt_params)
            gtt_id  = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gtt_id)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt,
            direct_fn=_direct_gtt,
            op_name="GTT",
            symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} {log_suffix}"
        )

        return gtt_id

    # -------------------------
    # SAFETY
    # -------------------------

    def cancel_order(self, order_id: str):
        kite = self._kite()
        if not kite:
            return

        self._relay_call(
            relay_fn=lambda r: r.cancel_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id,
            ),
            direct_fn=lambda: kite.cancel_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id,
            ),
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

    # -------------------------
    # MARKET SELL (EOD square-off)
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

        write_audit_log(
            f"[ZERODHA-SELL] {symbol} qty={qty} ltp={ltp} limit={limit_price}"
        )

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

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="SELL",
            symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-SELL-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )

        return str(order_id)