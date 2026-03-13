from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path
import json
import time

from app.execution.base_executor import BaseOrderExecutor
from app.db.trades_repo import close_trade
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log


@dataclass
class BBTrade:
    trade_id: str
    symbol: str
    qty: int
    sl_price: float
    tp_price: float
    gtt_id: Optional[str]
    entry_time: float


class BBTradeStateManager:

    def __init__(
        self,
        side: str,  # "CE" or "PE"
        strategy_id: str,
        executor: BaseOrderExecutor,
        state_file: Path,
    ):
        self.side = side
        self.strategy_id = strategy_id
        self.executor = executor
        self.state_file = state_file

        self.active_trade: Optional[BBTrade] = None
        self.in_trade = False

        self._load_state()
        self._reconcile()

    # --------------------------------------------------
    # STATE PERSISTENCE
    # --------------------------------------------------

    def _load_state(self):
        if not self.state_file.exists():
            return

        raw = self.state_file.read_text().strip()
        if not raw or raw == "{}":
            return

        try:
            self.active_trade = BBTrade(**json.loads(raw))
            self.in_trade = True
        except Exception as e:
            write_audit_log(f"[BB][STATE LOAD FAILED] {e}")

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.active_trade:
            self.state_file.write_text("{}")
        else:
            self.state_file.write_text(
                json.dumps(asdict(self.active_trade), indent=2)
            )

    # --------------------------------------------------
    # RECONCILIATION
    # --------------------------------------------------

    def _reconcile(self):

        if not self.active_trade:
            return

        start = time.time()
        while not LTPStore.has_any() and time.time() - start < 3:
            time.sleep(0.2)

        try:
            positions = self.executor.get_open_positions()
        except Exception as e:
            write_audit_log(f"[BB][RECON] Position fetch failed ERR={e}")
            return

        position_exists = False

        for p in positions:
            if (
                p.get("tradingsymbol") == self.active_trade.symbol
                and p.get("quantity", 0) != 0
            ):
                position_exists = True
                break

        if not position_exists:

            # --------------------------------------------------
            # Determine WHY the position closed.
            # Query the GTT so we can tag the exit correctly.
            # OCO layout:  orders[0] = SL leg, orders[1] = TP leg.
            # A triggered leg carries result.order_id + average_price.
            # --------------------------------------------------

            exit_reason   = "BROKER_EXIT"
            exit_price    = LTPStore.get(self.active_trade.symbol)
            exit_order_id = None
            gtt_id        = self.active_trade.gtt_id

            if gtt_id:
                try:
                    gtts = self.executor.get_gtts()
                    gtt  = next(
                        (g for g in gtts if str(g.get("id")) == str(gtt_id)),
                        None,
                    )
                    if gtt and gtt.get("status") in ("triggered", "disabled"):
                        for i, order in enumerate(gtt.get("orders", [])):
                            result = order.get("result") or {}
                            if result.get("order_id"):
                                exit_reason   = "SL_HIT" if i == 0 else "TP_HIT"
                                fill          = result.get("average_price")
                                if fill:
                                    exit_price = float(fill)
                                exit_order_id = result.get("order_id")
                                break
                except Exception as e:
                    write_audit_log(
                        f"[BB][RECON] GTT status fetch failed ERR={e}"
                    )

            write_audit_log(
                f"[BB][RECON] Position closed "
                f"SIDE={self.side} reason={exit_reason} price={exit_price}"
            )

            close_trade(
                trade_id=self.active_trade.trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id or (str(gtt_id) if gtt_id else None),
                exit_reason=exit_reason,
            )

            self.active_trade = None
            self.in_trade = False
            self._save_state()

    # --------------------------------------------------
    # REGISTER NEW TRADE
    # --------------------------------------------------

    def register_trade(
        self,
        trade_id: str,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
        gtt_id: str,
    ):
        self.active_trade = BBTrade(
            trade_id=trade_id,
            symbol=symbol,
            qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            gtt_id=gtt_id,
            entry_time=time.time(),
        )

        self.in_trade = True
        self._save_state()

    # --------------------------------------------------
    # CLEAR AFTER MANUAL EXIT
    # --------------------------------------------------

    def clear_trade(self):
        self.active_trade = None
        self.in_trade = False
        self._save_state()