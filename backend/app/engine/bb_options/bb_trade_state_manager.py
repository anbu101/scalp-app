# backend/app/engine/bb_options/bb_trade_state_manager.py
#
# CHANGES vs previous version:
#
# - BBTrade gains `leg_number` (1 or 2) and `trade_id_leg2` is tracked
#   separately via active_trade_leg2.
# - State file format upgraded to {"leg1": {...}, "leg2": {...}}.
#   Old flat format (single trade) is read as leg1 for backward compat.
# - in_trade is True whenever either leg is open.
# - register_trade / register_trade_leg2 for two-leg entries.
# - clear_trade()      — clears BOTH legs (full exit).
# - clear_trade_leg1() — clears leg 1 only (TP1 hit, leg 2 still running).
# - _reconcile()       — handles partial-open state on startup.

from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path
import json
import time

from app.execution.base_executor import BaseOrderExecutor
from app.db.trades_repo import close_trade
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log


# --------------------------------------------------
# TRADE DATA CLASS
# --------------------------------------------------

@dataclass
class BBTrade:
    trade_id:    str
    symbol:      str
    qty:         int
    sl_price:    float
    tp_price:    float
    gtt_id:      Optional[str]
    entry_time:  float
    leg_number:  int = 1          # 1 = first leg, 2 = runner
    entry_price: float = 0.0      # stored for trailing-SL breakeven calc


# --------------------------------------------------
# STATE MANAGER
# --------------------------------------------------

class BBTradeStateManager:

    def __init__(
        self,
        side:        str,              # "CE" or "PE"
        strategy_id: str,
        executor:    BaseOrderExecutor,
        state_file:  Path,
    ):
        self.side        = side
        self.strategy_id = strategy_id
        self.executor    = executor
        self.state_file  = state_file

        # Leg 1 (always present when in_trade)
        self.active_trade:      Optional[BBTrade] = None
        # Leg 2 (only set when multiple_targets=True and leg 2 still open)
        self.active_trade_leg2: Optional[BBTrade] = None

        self._load_state()
        self._reconcile()

    # --------------------------------------------------
    # PUBLIC: in_trade (derived)
    # --------------------------------------------------

    @property
    def in_trade(self) -> bool:
        return self.active_trade is not None or self.active_trade_leg2 is not None

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
            data = json.loads(raw)

            # --------------------------------------------------
            # BACKWARD COMPAT: old flat format (single BBTrade)
            # detected by the presence of "trade_id" at root.
            # --------------------------------------------------
            if "trade_id" in data:
                self.active_trade = _parse_trade(data, default_leg=1)
                write_audit_log(
                    f"[BB][STATE_LOAD] {self.side} leg1 (legacy format) "
                    f"trade_id={self.active_trade.trade_id}"
                )
                return

            # New format: {"leg1": {...}, "leg2": {...}}
            leg1_data = data.get("leg1")
            leg2_data = data.get("leg2")

            if leg1_data:
                self.active_trade = _parse_trade(leg1_data, default_leg=1)
                write_audit_log(
                    f"[BB][STATE_LOAD] {self.side} leg1 "
                    f"trade_id={self.active_trade.trade_id}"
                )

            if leg2_data:
                self.active_trade_leg2 = _parse_trade(leg2_data, default_leg=2)
                write_audit_log(
                    f"[BB][STATE_LOAD] {self.side} leg2 "
                    f"trade_id={self.active_trade_leg2.trade_id}"
                )

        except Exception as e:
            write_audit_log(f"[BB][STATE_LOAD_FAILED] {self.side} ERR={e}")

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.active_trade and not self.active_trade_leg2:
            self.state_file.write_text("{}")
            return

        payload = {}
        if self.active_trade:
            payload["leg1"] = asdict(self.active_trade)
        if self.active_trade_leg2:
            payload["leg2"] = asdict(self.active_trade_leg2)

        self.state_file.write_text(json.dumps(payload, indent=2))

    # --------------------------------------------------
    # RECONCILIATION (on startup)
    # Checks open broker positions and closes any DB rows
    # for legs that are no longer open.
    # --------------------------------------------------

    def _reconcile(self):
        if not self.active_trade and not self.active_trade_leg2:
            return

        # Wait briefly for LTPStore to seed from WS
        start = time.time()
        while not LTPStore.has_any() and time.time() - start < 3:
            time.sleep(0.2)

        try:
            positions = self.executor.get_open_positions()
        except Exception as e:
            write_audit_log(f"[BB][RECON] Position fetch failed ERR={e}")
            return

        # Sum of broker qty for this symbol across all positions
        symbol = (
            self.active_trade.symbol
            if self.active_trade
            else self.active_trade_leg2.symbol
        )

        broker_qty = sum(
            abs(p.get("quantity", 0))
            for p in positions
            if p.get("tradingsymbol") == symbol and p.get("quantity", 0) != 0
        )

        expected_qty = (
            (self.active_trade.qty      if self.active_trade      else 0) +
            (self.active_trade_leg2.qty if self.active_trade_leg2 else 0)
        )

        if broker_qty == 0:
            # No position at broker — close all open legs
            write_audit_log(
                f"[BB][RECON] No broker position for {symbol} — "
                f"closing all open legs. SIDE={self.side}"
            )
            self._recon_close_leg(self.active_trade)
            self._recon_close_leg(self.active_trade_leg2)
            self.active_trade      = None
            self.active_trade_leg2 = None
            self._save_state()

        elif self.active_trade and not self.active_trade_leg2:
            # Single-leg trade: position exists, nothing to do
            write_audit_log(
                f"[BB][RECON] Position confirmed SIDE={self.side} "
                f"qty={broker_qty}"
            )

        elif self.active_trade and self.active_trade_leg2:
            # Two-leg trade
            leg2_qty = self.active_trade_leg2.qty
            leg1_qty = self.active_trade.qty

            if broker_qty == leg2_qty and broker_qty < expected_qty:
                # Leg 1 closed while app was down (TP1 likely hit)
                write_audit_log(
                    f"[BB][RECON] TWO-LEG: only leg2 qty={leg2_qty} found "
                    f"at broker — leg1 must have exited. Closing leg1 in DB."
                )
                self._recon_close_leg(self.active_trade, exit_reason="BROKER_EXIT")
                self.active_trade = None
                self._save_state()

            elif broker_qty == 0:
                # Both closed (handled above already)
                pass

            else:
                write_audit_log(
                    f"[BB][RECON] TWO-LEG: position confirmed "
                    f"SIDE={self.side} broker_qty={broker_qty} "
                    f"expected={expected_qty}"
                )

    def _recon_close_leg(self, trade: Optional["BBTrade"], exit_reason: str = "BROKER_EXIT"):
        if not trade:
            return
        symbol    = trade.symbol
        exit_price = LTPStore.get(symbol)
        gtt_id    = trade.gtt_id

        # Try to get fill from GTT
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
                            reason_map = {0: "GTT_SL", 1: "GTT_TP"}
                            exit_reason = reason_map.get(i, "BROKER_EXIT")
                            fill = result.get("average_price")
                            if fill:
                                exit_price = float(fill)
                            break
            except Exception as e:
                write_audit_log(f"[BB][RECON] GTT fetch failed ERR={e}")

        write_audit_log(
            f"[BB][RECON] Closing leg{trade.leg_number} "
            f"trade_id={trade.trade_id} reason={exit_reason} price={exit_price}"
        )
        try:
            close_trade(
                trade_id=trade.trade_id,
                exit_price=exit_price,
                exit_order_id=str(gtt_id) if gtt_id else None,
                exit_reason=exit_reason,
            )
        except Exception as e:
            write_audit_log(f"[BB][RECON][CLOSE_FAIL] {e}")

    # --------------------------------------------------
    # REGISTER NEW TRADES
    # --------------------------------------------------

    def register_trade(
        self,
        trade_id:    str,
        symbol:      str,
        qty:         int,
        sl_price:    float,
        tp_price:    float,
        gtt_id:      Optional[str],
        entry_price: float = 0.0,
        leg_number:  int   = 1,
    ):
        """Register leg 1 (or the only leg in single-target mode)."""
        self.active_trade = BBTrade(
            trade_id=trade_id,
            symbol=symbol,
            qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            gtt_id=gtt_id,
            entry_time=time.time(),
            leg_number=leg_number,
            entry_price=entry_price,
        )
        self._save_state()

    def register_trade_leg2(
        self,
        trade_id:    str,
        symbol:      str,
        qty:         int,
        sl_price:    float,
        tp_price:    float,
        gtt_id:      Optional[str],
        entry_price: float = 0.0,
    ):
        """Register leg 2 (runner leg in multiple-targets mode)."""
        self.active_trade_leg2 = BBTrade(
            trade_id=trade_id,
            symbol=symbol,
            qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            gtt_id=gtt_id,
            entry_time=time.time(),
            leg_number=2,
            entry_price=entry_price,
        )
        self._save_state()

    def update_leg2_gtt(self, new_gtt_id: str, new_sl_price: float):
        """
        Called by GTTMonitor after placing a trailing-SL replacement GTT.
        Updates leg 2's GTT ID and SL price in memory and on disk.
        """
        if not self.active_trade_leg2:
            return
        self.active_trade_leg2.gtt_id   = new_gtt_id
        self.active_trade_leg2.sl_price = new_sl_price
        self._save_state()
        write_audit_log(
            f"[BB][STATE] Leg2 GTT updated gtt_id={new_gtt_id} "
            f"new_sl={new_sl_price}"
        )

    # --------------------------------------------------
    # CLEAR AFTER EXITS
    # --------------------------------------------------

    def clear_trade(self):
        """Full exit — both legs closed."""
        self.active_trade      = None
        self.active_trade_leg2 = None
        self._save_state()

    def clear_trade_leg1(self):
        """
        Partial exit — leg 1 closed (TP1 hit), leg 2 still running.
        in_trade remains True because active_trade_leg2 is still set.
        """
        self.active_trade = None
        self._save_state()
        write_audit_log(
            f"[BB][STATE] Leg1 cleared — leg2 still active SIDE={self.side}"
        )


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _parse_trade(data: dict, default_leg: int) -> BBTrade:
    """
    Safely construct a BBTrade from a dict, tolerating missing fields
    added in later versions (leg_number, entry_price).
    """
    return BBTrade(
        trade_id=data["trade_id"],
        symbol=data["symbol"],
        qty=int(data["qty"]),
        sl_price=float(data["sl_price"]),
        tp_price=float(data["tp_price"]),
        gtt_id=data.get("gtt_id"),
        entry_time=float(data.get("entry_time", time.time())),
        leg_number=int(data.get("leg_number", default_leg)),
        entry_price=float(data.get("entry_price", 0.0)),
    )