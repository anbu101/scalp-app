from dataclasses import dataclass, asdict
from typing import Optional
import time
import json
from pathlib import Path
from datetime import datetime
import asyncio
import uuid

from app.execution.base_executor import BaseOrderExecutor
from app.config.strategy_loader import load_strategy_config
from app.utils.session_utils import is_within_session
from app.event_bus.log_bus import log_bus
from app.event_bus.audit_logger import write_audit_log
from app.risk.max_loss_guard import check_max_loss
from app.trading.signal_snapshot import update_signal
from app.db.trades_repo import insert_trade, close_trade, update_gtt
from app.db.db_lock import DB_LOCK
from app.marketdata.ltp_store import LTPStore   # ✅ AUTHORITATIVE


STATE_BUY_PLACED = "BUY_PLACED"
STATE_PROTECTED = "PROTECTED"
STATE_CLOSED = "CLOSED"


@dataclass
class Trade:
    trade_id: str
    symbol: str
    token: int
    qty: int
    buy_order_id: str
    buy_price: float
    gtt_id: Optional[str]
    sl_price: float
    tp_price: float
    entry_time: float
    state: str
    candle_ts: int
    exit_reason: Optional[str] = None
    sl_order_id: Optional[str] = None


class TradeStateManager:
    """
    🔒 SINGLE AUTHORITATIVE MONEY GATE
    """

    _REGISTRY = {}

    AVG_PRICE_WAIT_SEC = 3
    AVG_PRICE_POLL_INTERVAL = 0.5

    LTP_WAIT_SEC = 2.0
    LTP_POLL_INTERVAL = 0.2

    def __init__(
        self,
        name: str,
        executor: BaseOrderExecutor,
        state_file: Path,
        price_provider,   # retained for compatibility (unused)
    ):
        self.name = name
        self.executor = executor
        self.state_file = state_file

        self.active_trade: Optional[Trade] = None
        self.in_trade = False
        self.selection_locked = False

        TradeStateManager._REGISTRY[name] = self

        self._load_state()
        self.reconcile_with_broker()

        self._log(
            f"[INIT] SLOT={self.name} in_trade={self.in_trade} locked={self.selection_locked}"
        )

    # -------------------------
    # Logging
    # -------------------------

    def _log(self, msg: str):
        print(msg)
        write_audit_log(msg)
        try:
            asyncio.get_running_loop().create_task(log_bus.publish(msg))
        except RuntimeError:
            pass

    # -------------------------
    # Persistence
    # -------------------------

    def _load_state(self):
        if not self.state_file.exists():
            return

        raw = self.state_file.read_text().strip()
        if not raw or raw == "{}":
            return

        try:
            self.active_trade = Trade(**json.loads(raw))
            self.in_trade = self.active_trade.state in (STATE_BUY_PLACED, STATE_PROTECTED)
            self.selection_locked = self.in_trade
        except Exception as e:
            self._log(f"[STATE] LOAD FAILED SLOT={self.name} ERR={e}")

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.active_trade:
            self.state_file.write_text("{}")
        else:
            self.state_file.write_text(
                json.dumps(asdict(self.active_trade), indent=2)
            )

    # -------------------------
    # Reconciliation
    # -------------------------

    def reconcile_with_broker(self):
        if not self.active_trade:
            return

        if not LTPStore.has_any():
            self._log(
                f"[RECON] LTP unavailable → skip reconciliation SLOT={self.name}"
            )
            return

        try:
            positions = self.executor.get_open_positions()
        except Exception as e:
            self._log(
                f"[RECON][WARN] Positions fetch failed → skip SLOT={self.name} ERR={e}"
            )
            return

        if not positions:
            self._log(
                f"[RECON] Positions empty → skip SLOT={self.name}"
            )
            return

        for p in positions:
            if (
                p.get("tradingsymbol") == self.active_trade.symbol
                and p.get("quantity", 0) != 0
            ):
                return

        self._log(
            f"[RECON] Confirmed GTT exit SLOT={self.name} SYMBOL={self.active_trade.symbol}"
        )

        with DB_LOCK:
            close_trade(
                trade_id=self.active_trade.trade_id,
                exit_price=LTPStore.get(self.active_trade.symbol),
                exit_order_id=None,
                exit_reason="GTT_EXIT",
            )

        self.active_trade = None
        self.in_trade = False
        self.selection_locked = False
        self._save_state()

    # -------------------------
    # Entry
    # -------------------------

    def on_buy_signal(
        self,
        *,
        symbol: str,
        token: int,
        candle_ts: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ):
        cfg = load_strategy_config()

        if cfg["trade_on"] is not True:
            return self._skip("TRADE_OFF", symbol, entry_price)

        if check_max_loss():
            return self._skip("MAX_LOSS_HIT", symbol, entry_price)

        if self.in_trade or self.selection_locked:
            return self._skip("SLOT_LOCKED", symbol, entry_price)

        if not is_within_session(
            datetime.now(),
            cfg["session"]["primary"]["start"],
            cfg["session"]["primary"]["end"],
        ):
            return self._skip("OUTSIDE_SESSION", symbol, entry_price)

        qty = cfg["quantity"]["lots"] * cfg["quantity"]["lot_size"]
        if qty <= 0:
            return self._skip("INVALID_QTY", symbol, entry_price)

        self.selection_locked = True

        buy_id, avg_price, filled_qty = self.executor.place_buy(
            symbol, token, qty
        )

        if filled_qty <= 0:
            self.selection_locked = False
            self._log(f"[ERROR] BUY FAILED SLOT={self.name}")
            return

        start = time.time()
        while avg_price <= 0 and time.time() - start < self.AVG_PRICE_WAIT_SEC:
            time.sleep(self.AVG_PRICE_POLL_INTERVAL)
            avg_price = self.executor.get_last_avg_price(buy_id)

        if avg_price <= 0:
            avg_price = entry_price

        rr = cfg["risk_reward_ratio"]
        tp_price = avg_price + (avg_price - sl_price) * rr

        trade = Trade(
            trade_id=str(uuid.uuid4()),
            symbol=symbol,
            token=token,
            qty=filled_qty,
            buy_order_id=buy_id,
            buy_price=avg_price,
            gtt_id=None,
            sl_price=sl_price,
            tp_price=tp_price,
            entry_time=time.time(),
            state=STATE_BUY_PLACED,
            candle_ts=candle_ts,
        )

        with DB_LOCK:
            insert_trade(
                trade_id=trade.trade_id,
                slot=self.name,
                symbol=symbol,
                token=token,
                entry_price=avg_price,
                qty=filled_qty,
                buy_order_id=buy_id,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_mode="GTT",
            )

        self.active_trade = trade
        self.in_trade = True
        self._save_state()

        ltp = None
        start = time.time()
        while ltp is None and time.time() - start < self.LTP_WAIT_SEC:
            ltp = LTPStore.get(symbol)
            if ltp is None:
                time.sleep(self.LTP_POLL_INTERVAL)

        if ltp is None:
            self._force_exit("LTP_UNAVAILABLE")
            return

        if ltp <= sl_price:
            self._force_exit("SL_BREACHED")
            return

        if ltp >= tp_price:
            self._force_exit("TP_REACHED")
            return

        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=filled_qty,
                sl_price=sl_price,
                tp_price=tp_price,
            )
        except Exception as e:
            self._log(
                f"[FATAL] GTT FAILED SLOT={self.name} SYMBOL={symbol} ERR={repr(e)}"
            )
            self._force_exit("GTT_FAILED")
            return

        self.active_trade.gtt_id = gtt_id
        self.active_trade.state = STATE_PROTECTED
        self._save_state()

        with DB_LOCK:
            update_gtt(trade_id=trade.trade_id, gtt_id=gtt_id)

    # -------------------------
    # Emergency Exit
    # -------------------------

    def _force_exit(self, reason: str):
        try:
            exit_id = self.executor.place_exit(
                symbol=self.active_trade.symbol,
                qty=self.active_trade.qty,
                reason=reason,
            )

            with DB_LOCK:
                close_trade(
                    trade_id=self.active_trade.trade_id,
                    exit_price=None,
                    exit_order_id=exit_id,
                    exit_reason=reason,
                )

            self._log(
                f"[SAFETY] POSITION EXITED SLOT={self.name} REASON={reason}"
            )

        except Exception as e:
            self._log(
                f"[CRITICAL] EXIT FAILED SLOT={self.name} SYMBOL={self.active_trade.symbol} ERR={e}"
            )

        self.active_trade = None
        self.in_trade = False
        self.selection_locked = False
        self._save_state()

    # -------------------------
    # Close / Skip
    # -------------------------

    def _close_trade(self, reason: str):
        if not self.active_trade:
            return

        with DB_LOCK:
            close_trade(
                trade_id=self.active_trade.trade_id,
                exit_price=None,
                exit_order_id=None,
                exit_reason=reason,
            )

        self.active_trade = None
        self.in_trade = False
        self.selection_locked = False
        self._save_state()

    def _skip(self, reason: str, symbol: str, price: float):
        self._log(f"[SKIP] SLOT={self.name} REASON={reason} SYMBOL={symbol}")
        update_signal(
            slot=self.name,
            symbol=symbol,
            action="SKIPPED",
            reason=reason,
            price=price,
        )
