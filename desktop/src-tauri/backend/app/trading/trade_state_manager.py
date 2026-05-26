from dataclasses import dataclass, asdict, field
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
from app.risk.strategy_max_loss_guard import check_strategy_max_loss
from app.trading.signal_snapshot import update_signal
from app.db.trades_repo import insert_trade, close_trade, update_gtt
from app.db.sqlite import get_conn
from app.marketdata.ltp_store import LTPStore
from app.config.global_loader import load_global_config

# 🔔 TELEGRAM
from app.api.telegram_api import (
    notify_trade_entry,
    notify_tp_exit,
    notify_sl_exit,
    notify_manual_exit,
)

STATE_BUY_PLACED  = "BUY_PLACED"   # legacy long entry — kept for BB/HA compat
STATE_SELL_PLACED = "SELL_PLACED"  # new short entry for SCALP_V1
STATE_PROTECTED   = "PROTECTED"
STATE_CLOSED      = "CLOSED"


@dataclass
class Trade:
    trade_id:        str
    symbol:          str
    token:           int
    qty:             int
    buy_order_id:    str       # entry order id (buy for long, sell for short)
    buy_price:       float     # entry price (premium sold for short)
    gtt_id:          Optional[str]
    sl_price:        float
    tp_price:        float
    entry_time:      float
    state:           str
    candle_ts:       int
    exit_reason:     Optional[str] = None
    sl_order_id:     Optional[str] = None
    trade_direction: str = "LONG"   # "LONG" | "SHORT"


class TradeStateManager:

    _REGISTRY = {}

    AVG_PRICE_WAIT_SEC    = 3
    AVG_PRICE_POLL_INTERVAL = 0.5
    LTP_WAIT_SEC          = 2.0
    LTP_POLL_INTERVAL     = 0.2

    def __init__(
        self,
        strategy_id: str,
        name: str,
        executor: BaseOrderExecutor,
        state_file: Path,
        price_provider,
    ):
        self.name        = name
        self.strategy_id = strategy_id
        self.executor    = executor
        self.state_file  = state_file

        self.active_trade:     Optional[Trade] = None
        self.in_trade          = False
        self.selection_locked  = False

        if strategy_id not in TradeStateManager._REGISTRY:
            TradeStateManager._REGISTRY[strategy_id] = {}

        TradeStateManager._REGISTRY[strategy_id][name] = self

        self._load_state()
        if not self.active_trade:
            self._restore_trade_from_db()
        self.reconcile_with_broker()

    # ==================================================
    # LOGGING
    # ==================================================

    def _log(self, msg: str):
        print(msg)
        write_audit_log(msg)
        try:
            asyncio.get_running_loop().create_task(log_bus.publish(msg))
        except RuntimeError:
            pass

    def _restore_trade_from_db(self):

        conn = get_conn()

        row = conn.execute(
            """
            SELECT trade_id, symbol, token, qty, buy_order_id,
                entry_price, sl_price, tp_price, entry_time,
                sl_order_id, tp_mode, trade_direction
            FROM trades
            WHERE strategy_id = ?
            AND slot = ?
            AND exit_time IS NULL
            LIMIT 1
            """,
            (self.strategy_id, self.name),
        ).fetchone()

        if not row:
            return

        # trade_direction may be None on old rows (before migration)
        direction = row["trade_direction"] if row["trade_direction"] else "LONG"

        self.active_trade = Trade(
            trade_id=row["trade_id"],
            symbol=row["symbol"],
            token=row["token"],
            qty=row["qty"],
            buy_order_id=row["buy_order_id"],
            buy_price=row["entry_price"],
            gtt_id=row["sl_order_id"],
            sl_price=row["sl_price"],
            tp_price=row["tp_price"],
            entry_time=row["entry_time"],
            state=STATE_PROTECTED,
            candle_ts=0,
            trade_direction=direction,
        )

        self.in_trade         = True
        self.selection_locked = True

        self._log(
            f"[STATE_RESTORE] SLOT={self.name} TRADE={self.active_trade.trade_id} "
            f"DIR={direction}"
        )

        self._save_state()

    # ==================================================
    # TELEGRAM HELPERS
    # ==================================================

    def _send_entry_notification(self, trade: Trade):
        try:
            notify_trade_entry({
                "strategy_id":    self.strategy_id,
                "mode":           "live",
                "symbol":         trade.symbol,
                "side":           self.name,
                "entry_price":    trade.buy_price,
                "quantity":       trade.qty,
                "sl":             trade.sl_price,
                "tp":             trade.tp_price,
                "trade_direction": trade.trade_direction,
            })
        except Exception as e:
            self._log(f"[TELEGRAM][ENTRY_ERROR] {e}")

    def _send_exit_notification(self, trade_id: str):
        conn = get_conn()
        row  = conn.execute(
            """
            SELECT symbol, entry_price, exit_price, qty,
                   exit_reason, trade_direction
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ).fetchone()

        if not row:
            return

        symbol, entry_price, exit_price, qty, exit_reason, direction = row
        direction  = direction or "LONG"

        # P&L direction-aware
        if direction == "SHORT":
            net_pnl = (entry_price - exit_price) * qty if exit_price else 0
        else:
            net_pnl = (exit_price - entry_price) * qty if exit_price else 0

        payload = {
            "strategy_id":    self.strategy_id,
            "mode":           "live",
            "symbol":         symbol,
            "entry_price":    entry_price,
            "exit_price":     exit_price,
            "pnl":            net_pnl,
            "trade_direction": direction,
        }

        try:
            if exit_reason in ("SL", "GTT_SL"):
                notify_sl_exit(payload)
            elif exit_reason in ("TP", "GTT_TP"):
                notify_tp_exit(payload)
            else:
                payload["exit_reason"] = exit_reason
                notify_manual_exit(payload)
        except Exception as e:
            self._log(f"[TELEGRAM][EXIT_ERROR] {e}")

    # ==================================================
    # PERSISTENCE
    # ==================================================

    def _load_state(self):

        if not self.state_file.exists():
            return

        try:
            raw = self.state_file.read_text().strip()

            if not raw or raw == "{}":
                return

            data = json.loads(raw)

            # Backward compat: old state files may not have trade_direction
            data.setdefault("trade_direction", "LONG")
            data.setdefault("exit_reason",     None)
            data.setdefault("sl_order_id",     None)

            trade = Trade(**data)

            # Normalise unknown states
            valid_states = {
                STATE_BUY_PLACED,
                STATE_SELL_PLACED,
                STATE_PROTECTED,
                STATE_CLOSED,
            }
            if trade.state not in valid_states:
                self._log(
                    f"[STATE_NORMALIZE] SLOT={self.name} "
                    f"state={trade.state} -> {STATE_PROTECTED}"
                )
                trade.state = STATE_PROTECTED

            self.active_trade = trade
            self.in_trade     = trade.state in (
                STATE_BUY_PLACED, STATE_SELL_PLACED, STATE_PROTECTED
            )
            self.selection_locked = self.in_trade

            self._log(
                f"[STATE_LOAD] SLOT={self.name} "
                f"state={trade.state} dir={trade.trade_direction} "
                f"in_trade={self.in_trade}"
            )

        except Exception as e:
            self._log(f"[STATE] LOAD FAILED SLOT={self.name} ERR={e}")
            self.active_trade     = None
            self.in_trade         = False
            self.selection_locked = False

    def _save_state(self):

        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            if not self.active_trade:
                self.state_file.write_text("{}")
                return

            payload = json.dumps(asdict(self.active_trade), indent=2)
            self.state_file.write_text(payload)

        except Exception as e:
            self._log(f"[STATE] SAVE FAILED SLOT={self.name} ERR={e}")

    # ==================================================
    # RECONCILIATION
    # Works for both LONG and SHORT positions:
    # position qty != 0 means still open regardless of direction.
    # P&L inference uses trade_direction.
    # ==================================================

    def reconcile_with_broker(self):
        if not self.active_trade:
            return

        if not LTPStore.has_any():
            return

        try:
            positions = self.executor.get_open_positions()
        except Exception:
            return

        if not positions:
            positions = []

        position_found = False

        for p in positions:
            if (
                p.get("tradingsymbol") == self.active_trade.symbol
                and p.get("quantity", 0) != 0
            ):
                position_found = True
                break

        if position_found:
            return

        trade_id  = self.active_trade.trade_id
        direction = self.active_trade.trade_direction or "LONG"
        exit_ltp  = LTPStore.get(self.active_trade.symbol) or 0.0

        # Infer exit reason based on direction
        if direction == "SHORT":
            # SHORT: SL = price above sl_price, TP = price below tp_price
            if exit_ltp >= self.active_trade.sl_price:
                exit_reason = "GTT_SL"
            else:
                exit_reason = "GTT_TP"
        else:
            # LONG (BB/HA): SL = price below sl, TP = price above tp
            if exit_ltp <= self.active_trade.sl_price:
                exit_reason = "GTT_SL"
            else:
                exit_reason = "GTT_TP"

        close_trade(
            trade_id=trade_id,
            exit_price=exit_ltp,
            exit_order_id=None,
            exit_reason=exit_reason,
        )

        self._send_exit_notification(trade_id)

        self.active_trade     = None
        self.in_trade         = False
        self.selection_locked = False
        self._save_state()

    # ==================================================
    # LONG ENTRY (PRESERVED UNCHANGED — BB / HA use this
    # path via their own trade managers, but keeping it
    # here ensures any direct callers continue working)
    # ==================================================

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
        cfg = load_strategy_config(self.strategy_id)

        if not load_global_config().get("trade_on", False):
            return self._skip("GLOBAL_TRADE_OFF", symbol, entry_price)

        if check_strategy_max_loss(self.strategy_id):
            return self._skip("MAX_LOSS_HIT", symbol, entry_price)

        if self.in_trade or self.selection_locked:
            return self._skip("SLOT_LOCKED", symbol, entry_price)

        session_cfg = cfg.get("session", {}).get("primary", {})

        if not is_within_session(
            datetime.now(),
            session_cfg.get("start"),
            session_cfg.get("end"),
        ):
            return self._skip("OUTSIDE_SESSION", symbol, entry_price)

        qty = cfg["quantity"]["lots"] * cfg["quantity"]["lot_size"]

        self.selection_locked = True

        broker_symbol = self.executor.resolve_symbol(symbol)

        buy_id, avg_price, filled_qty = self.executor.place_buy(
            broker_symbol,
            token,
            qty,
        )

        if filled_qty <= 0:
            self.selection_locked = False
            return

        if avg_price <= 0:
            avg_price = entry_price

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
            trade_direction="LONG",
        )

        self._clear_stale_db_slot()

        insert_trade(
            trade_id=trade.trade_id,
            strategy_id=self.strategy_id,
            slot=self.name,
            symbol=symbol,
            token=token,
            entry_price=avg_price,
            qty=filled_qty,
            buy_order_id=buy_id,
            sl_price=sl_price,
            tp_price=tp_price,
            tp_mode="GTT",
            trade_direction="LONG",
        )

        self.active_trade = trade
        self.in_trade     = True
        self._save_state()

        self._send_entry_notification(trade)

        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=filled_qty,
                sl_price=sl_price,
                tp_price=tp_price,
                direction="LONG",
            )
        except Exception:
            self._force_exit("BROKER_EXIT")
            return

        self.active_trade.gtt_id = gtt_id
        self.active_trade.state  = STATE_PROTECTED
        self._save_state()

        update_gtt(trade_id=trade.trade_id, gtt_id=gtt_id)

    # ==================================================
    # SHORT ENTRY — NEW for SCALP_V1
    #
    # Places a SELL entry order (short the option premium).
    # GTT OCO is inverted:
    #   - BUY back orders
    #   - lower trigger = TP  (premium fell = profit)
    #   - upper trigger = SL  (premium rose = loss)
    # ==================================================

    def on_sell_signal(
        self,
        *,
        symbol: str,
        token: int,
        candle_ts: int,
        entry_price: float,
        sl_price: float,   # ABOVE entry — bad for seller
        tp_price: float,   # BELOW entry — good for seller
    ):
        cfg = load_strategy_config(self.strategy_id)

        if not load_global_config().get("trade_on", False):
            return self._skip("GLOBAL_TRADE_OFF", symbol, entry_price)

        if check_strategy_max_loss(self.strategy_id):
            return self._skip("MAX_LOSS_HIT", symbol, entry_price)

        if self.in_trade or self.selection_locked:
            return self._skip("SLOT_LOCKED", symbol, entry_price)

        session_cfg = cfg.get("session", {}).get("primary", {})

        if not is_within_session(
            datetime.now(),
            session_cfg.get("start"),
            session_cfg.get("end"),
        ):
            return self._skip("OUTSIDE_SESSION", symbol, entry_price)

        qty = cfg["quantity"]["lots"] * cfg["quantity"]["lot_size"]

        self.selection_locked = True

        broker_symbol = self.executor.resolve_symbol(symbol)

        # ── SELL entry (short the option) ─────────────────────
        sell_id, avg_price, filled_qty = self.executor.place_sell_entry(
            symbol=broker_symbol,
            token=token,
            qty=qty,
        )

        if filled_qty <= 0:
            self.selection_locked = False
            return

        if avg_price <= 0:
            avg_price = entry_price

        # Recalculate SL/TP using actual fill price
        risk_distance = avg_price - tp_price   # distance from fill to original tp
        if risk_distance <= 0:
            # Fallback: use signal's tp directly
            actual_tp = tp_price
        else:
            actual_tp = tp_price

        # SL stays proportional to actual fill
        rr = cfg.get("risk_reward_ratio", 1.0)
        actual_sl = avg_price + risk_distance * rr

        trade = Trade(
            trade_id=str(uuid.uuid4()),
            symbol=symbol,
            token=token,
            qty=filled_qty,
            buy_order_id=sell_id,   # field reused as entry_order_id
            buy_price=avg_price,    # field reused as entry_price
            gtt_id=None,
            sl_price=actual_sl,
            tp_price=actual_tp,
            entry_time=time.time(),
            state=STATE_SELL_PLACED,
            candle_ts=candle_ts,
            trade_direction="SHORT",
        )

        self._clear_stale_db_slot()

        insert_trade(
            trade_id=trade.trade_id,
            strategy_id=self.strategy_id,
            slot=self.name,
            symbol=symbol,
            token=token,
            entry_price=avg_price,
            qty=filled_qty,
            buy_order_id=sell_id,
            sl_price=actual_sl,
            tp_price=actual_tp,
            tp_mode="GTT",
            trade_direction="SHORT",
        )

        self.active_trade = trade
        self.in_trade     = True
        self._save_state()

        self._send_entry_notification(trade)

        # ── Place inverted GTT OCO (BUY back orders) ──────────
        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=filled_qty,
                sl_price=actual_sl,
                tp_price=actual_tp,
                direction="SHORT",   # ← tells executor to invert triggers
            )
        except Exception as e:
            self._log(
                f"[SHORT][GTT_FAIL] symbol={symbol} ERR={e} — "
                f"position is UNPROTECTED. Will close on ST/EOD."
            )
            # Keep trade open but unprotected; EOD squareoff will close it
            self.active_trade.state = STATE_PROTECTED
            self._save_state()
            return

        self.active_trade.gtt_id = gtt_id
        self.active_trade.state  = STATE_PROTECTED
        self._save_state()

        update_gtt(trade_id=trade.trade_id, gtt_id=gtt_id)

        self._log(
            f"[SHORT][ENTRY_CONFIRMED] SLOT={self.name} symbol={symbol} "
            f"entry={avg_price:.2f} tp={actual_tp:.2f} sl={actual_sl:.2f} "
            f"gtt={gtt_id}"
        )

    # ==================================================
    # FORCE EXIT (SL / TP / ERROR)
    # Works for both LONG and SHORT:
    # - For SHORT: place_buy_exit() buys back the position
    # - For LONG:  place_exit() sells the position
    # ==================================================

    def _force_exit(self, reason: str):
        if not self.active_trade:
            return

        trade_id  = self.active_trade.trade_id
        direction = self.active_trade.trade_direction or "LONG"

        allowed_reasons = {
            "TP", "SL", "MANUAL", "BROKER_EXIT",
            "GTT_TP", "GTT_SL",
        }
        safe_reason = reason if reason in allowed_reasons else "BROKER_EXIT"

        if safe_reason != reason:
            self._log(
                f"[EXIT_REASON_NORMALIZED] original={reason} → safe={safe_reason}"
            )

        try:
            if direction == "SHORT":
                exit_id = self.executor.place_buy_exit(
                    symbol=self.active_trade.symbol,
                    qty=self.active_trade.qty,
                    reason=safe_reason,
                )
            else:
                exit_id = self.executor.place_exit(
                    symbol=self.active_trade.symbol,
                    qty=self.active_trade.qty,
                    reason=safe_reason,
                )

            close_trade(
                trade_id=trade_id,
                exit_price=LTPStore.get(self.active_trade.symbol),
                exit_order_id=exit_id,
                exit_reason=safe_reason,
            )

            self._send_exit_notification(trade_id)

        except Exception as e:
            self._log(f"[CRITICAL] EXIT FAILED {e}")

        self.active_trade     = None
        self.in_trade         = False
        self.selection_locked = False
        self._save_state()

    # ==================================================
    # HELPERS
    # ==================================================

    def _clear_stale_db_slot(self):
        """Force-close any orphaned open DB row for this slot."""
        conn = get_conn()
        conn.execute(
            """
            UPDATE trades
            SET
                exit_time   = strftime('%s','now'),
                exit_reason = 'BROKER_EXIT',
                state       = 'CLOSED'
            WHERE slot = ?
              AND strategy_id = ?
              AND exit_time IS NULL
            """,
            (self.name, self.strategy_id),
        )
        conn.commit()

    def _skip(self, reason: str, symbol: str, price: float):
        self._log(f"[SKIP] SLOT={self.name} REASON={reason} SYMBOL={symbol}")
        update_signal(
            slot=self.name,
            symbol=symbol,
            action="SKIPPED",
            reason=reason,
            price=price,
        )