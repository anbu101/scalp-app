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
from app.db.trades_repo import insert_trade, close_trade

from app.db.trades_repo import insert_trade, close_trade, update_gtt



# =========================
# Trade States
# =========================
STATE_IDLE = "IDLE"
STATE_BUY_PLACED = "BUY_PLACED"
STATE_PROTECTED = "PROTECTED"
STATE_CLOSED = "CLOSED"


# =========================
# Data Model
# =========================

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


# =========================
# Trade State Manager
# =========================

class TradeStateManager:
    """
    Controls trade lifecycle for ONE option slot.

    GTT_ONLY MODE:
    - BUY (NRML)
    - Wait for avg price (<= 3s)
    - Place OCO GTT (SL + TP)
    - No SL-M
    - No engine TP watcher
    """

    _REGISTRY = {}

    AVG_PRICE_WAIT_SEC = 3
    AVG_PRICE_POLL_INTERVAL = 0.5

    @classmethod
    def get(cls, name: str) -> "TradeStateManager":
        return cls._REGISTRY[name]

    @classmethod
    def snapshot(cls) -> dict:
        return {
            name: ("IN_TRADE" if mgr.in_trade else "ARMED")
            for name, mgr in cls._REGISTRY.items()
        }

    # -------------------------
    # Init
    # -------------------------

    def __init__(
        self,
        name: str,
        executor: BaseOrderExecutor,
        state_file: Path,
        price_provider,
    ):
        self.name = name
        self.executor = executor
        self.state_file = state_file
        self.price_provider = price_provider

        self.active_trade: Optional[Trade] = None
        self.in_trade: bool = False
        self.selection_locked: bool = False

        TradeStateManager._REGISTRY[name] = self

        self._load_state()
        self.reconcile_with_broker()

        self._log(
            f"[INIT] SLOT={self.name} "
            f"in_trade={self.in_trade} locked={self.selection_locked} "
            f"active_trade={'YES' if self.active_trade else 'NO'}"
        )

    # =========================
    # Logging
    # =========================

    def _log(self, message: str):
        print(message)
        write_audit_log(message)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(log_bus.publish(message))
        except RuntimeError:
            pass

    # =========================
    # Persistence
    # =========================

    def _load_state(self):
        if not self.state_file.exists():
            return

        raw = self.state_file.read_text().strip()
        if not raw or raw == "{}":
            return

        try:
            data = json.loads(raw)
            self.active_trade = Trade(**data)
            self.in_trade = self.active_trade.state != STATE_CLOSED
            self.selection_locked = self.in_trade
        except Exception as e:
            self._log(f"[STATE] LOAD FAILED SLOT={self.name} ERR={e}")

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.active_trade:
            self.state_file.write_text("{}")
            return

        self.state_file.write_text(
            json.dumps(asdict(self.active_trade), indent=2)
        )

    # =========================
    # Reconciliation (GTT ONLY)
    # =========================

    def reconcile_with_broker(self):
        if not self.active_trade:
            return

        trade = self.active_trade
        positions = self.executor.get_open_positions()

        # Check if position still exists
        for p in positions:
            if p.get("tradingsymbol") == trade.symbol and p.get("quantity", 0) != 0:
                return  # still open, nothing to do

        # -------------------------
        # POSITION IS CLOSED (GTT HIT)
        # -------------------------

        ltp = self.price_provider.get_ltp(trade.symbol)

        if ltp is not None and ltp <= trade.sl_price:
            reason = "GTT_SL"
        else:
            reason = "GTT_TP"

        self._log(
            f"[RECON] GTT EXIT SLOT={self.name} SYMBOL={trade.symbol} REASON={reason}"
        )

        close_trade(
            trade_id=trade.trade_id,
            exit_price=ltp,
            exit_order_id=None,
            exit_reason=reason,
        )

        self.active_trade = None
        self.in_trade = False
        self.selection_locked = False
        self._save_state()


    # =========================
    # Guards
    # =========================

    def can_enter(self) -> bool:
        return not self.in_trade and not self.selection_locked

    # =========================
    # Entry (GTT ONLY)
    # =========================

    def on_buy_signal(
        self,
        *,
        symbol: str,
        token: int,
        candle_ts: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,  # ignored – recomputed after avg price
    ):
        self._log(
            f"[SIGNAL] BUY SLOT={self.name} SYMBOL={symbol} ENTRY={entry_price}"
        )

        cfg = load_strategy_config()

        if check_max_loss():
            self._skip("MAX_LOSS_HIT", symbol, entry_price)
            return

        if not self.can_enter():
            self._skip("SLOT_LOCKED", symbol, entry_price)
            return

        if not cfg.get("trade_on", False):
            self._skip("TRADE_OFF", symbol, entry_price)
            return

        now = datetime.now()

        if not is_within_session(
            now,
            cfg["session"]["primary"]["start"],
            cfg["session"]["primary"]["end"],
        ):
            self._skip("OUTSIDE_SESSION", symbol, entry_price)
            return

        qty = cfg["quantity"]["lots"] * cfg["quantity"]["lot_size"]
        if qty <= 0:
            self._skip("INVALID_QTY", symbol, entry_price)
            return

        # -------------------------
        # BUY
        # -------------------------

        buy_id, avg_price, filled_qty = self.executor.place_buy(
            symbol=symbol,
            token=token,
            qty=qty,
        )

        if filled_qty <= 0:
            self._log(
                f"[ERROR] BUY failed SLOT={self.name} SYMBOL={symbol}"
            )
            return

        # -------------------------
        # AVG PRICE WAIT (<= 3s)
        # -------------------------

        used_fallback = False
        start = time.time()

        while avg_price <= 0 and time.time() - start < self.AVG_PRICE_WAIT_SEC:
            time.sleep(self.AVG_PRICE_POLL_INTERVAL)
            avg_price = self.executor.get_last_avg_price(buy_id)

        if avg_price <= 0:
            avg_price = entry_price
            used_fallback = True

        # -------------------------
        # TP RECOMPUTE
        # -------------------------

        rr = cfg.get("risk_reward_ratio", 1)
        tp_price = avg_price + (avg_price - sl_price) * rr

        # -------------------------
        # INSERT TRADE (IMMEDIATE)
        # -------------------------

        self.active_trade = Trade(
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

        insert_trade(
            trade_id=self.active_trade.trade_id,
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

        self.in_trade = True
        self.selection_locked = True
        self._save_state()

        self._log(
            f"[BUY] SLOT={self.name} SYMBOL={symbol} "
            f"PRICE={avg_price} SOURCE={'FALLBACK' if used_fallback else 'AVG'}"
        )

        # -------------------------
        # PLACE GTT (NEXT FILE)
        # -------------------------

        gtt_id = self.executor.place_gtt_oco(
            symbol=symbol,
            qty=filled_qty,
            sl_price=sl_price,
            tp_price=tp_price,
        )

        self.active_trade.gtt_id = gtt_id
        self.active_trade.state = STATE_PROTECTED
        self._save_state()

        update_gtt(
            trade_id=self.active_trade.trade_id,
            gtt_id=gtt_id,
        )

    # =========================
    # Close / Skip
    # =========================

    def _close_trade(self, reason: str):
        if not self.active_trade:
            return

        self.active_trade.state = STATE_CLOSED
        self.active_trade.exit_reason = reason
        self._save_state()

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
        self._log(
            f"[SKIP] SLOT={self.name} REASON={reason} SYMBOL={symbol}"
        )
        update_signal(
            slot=self.name,
            symbol=symbol,
            action="SKIPPED",
            reason=reason,
            price=price,
        )
