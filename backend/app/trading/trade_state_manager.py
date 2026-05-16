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

    _REGISTRY = {}

    AVG_PRICE_WAIT_SEC = 3
    AVG_PRICE_POLL_INTERVAL = 0.5
    LTP_WAIT_SEC = 2.0
    LTP_POLL_INTERVAL = 0.2

    def __init__(
        self,
        strategy_id: str,
        name: str,
        executor: BaseOrderExecutor,
        state_file: Path,
        price_provider,
    ):
        self.name = name
        self.strategy_id = strategy_id
        self.executor = executor
        self.state_file = state_file

        self.active_trade: Optional[Trade] = None
        self.in_trade = False
        self.selection_locked = False

        # ── PAPER MODE SLOT TRACKING ──────────────────────────────────
        # active_trade is only set in LIVE mode. For PAPER mode we track
        # the paper_trade_id here so reconcile_with_broker() can detect
        # when the paper trade has closed and unlock the slot.
        # _paper_trade_id is intentionally NOT persisted to state file —
        # on restart the slot resets to unlocked which is safe for PAPER.
        self._paper_trade_id: Optional[str] = None

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
                sl_order_id, tp_mode
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
        )

        self.in_trade = True
        self.selection_locked = True

        self._log(
            f"[STATE_RESTORE] SLOT={self.name} TRADE={self.active_trade.trade_id}"
        )

        self._save_state()

    # ==================================================
    # TELEGRAM HELPERS
    # ==================================================

    def _send_entry_notification(self, trade: Trade):
        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": trade.symbol,
                "side": self.name,
                "entry_price": trade.buy_price,
                "quantity": trade.qty,
                "sl": trade.sl_price,
                "tp": trade.tp_price,
            })
        except Exception as e:
            self._log(f"[TELEGRAM][ENTRY_ERROR] {e}")

    def _send_exit_notification(self, trade_id: str):
        """
        Fetch authoritative data from DB after close_trade()
        """
        conn = get_conn()
        row = conn.execute(
            """
            SELECT symbol, entry_price, exit_price, qty, exit_reason
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ).fetchone()

        if not row:
            return

        symbol, entry_price, exit_price, qty, exit_reason = row
        net_pnl = (exit_price - entry_price) * qty if exit_price else 0

        payload = {
            "strategy_id": self.strategy_id,
            "mode": "live",
            "symbol": symbol,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": net_pnl,
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

            trade = Trade(**data)

            # --------------------------------------------------
            # NORMALIZE UNKNOWN STATES (recovery compatibility)
            # --------------------------------------------------
            if trade.state not in (
                STATE_BUY_PLACED,
                STATE_PROTECTED,
                STATE_CLOSED,
            ):
                self._log(
                    f"[STATE_NORMALIZE] SLOT={self.name} "
                    f"state={trade.state} -> {STATE_PROTECTED}"
                )
                trade.state = STATE_PROTECTED

            self.active_trade = trade

            self.in_trade = trade.state in (
                STATE_BUY_PLACED,
                STATE_PROTECTED,
            )

            self.selection_locked = self.in_trade

            self._log(
                f"[STATE_LOAD] SLOT={self.name} "
                f"state={trade.state} "
                f"in_trade={self.in_trade}"
            )

        except Exception as e:
            self._log(
                f"[STATE] LOAD FAILED SLOT={self.name} ERR={e}"
            )
            self.active_trade = None
            self.in_trade = False
            self.selection_locked = False

    def _save_state(self):

        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            if not self.active_trade:
                self.state_file.write_text("{}")
                return

            payload = json.dumps(
                asdict(self.active_trade),
                indent=2
            )

            self.state_file.write_text(payload)

        except Exception as e:
            self._log(
                f"[STATE] SAVE FAILED SLOT={self.name} ERR={e}"
            )

    # ==================================================
    # RECONCILIATION
    # ==================================================

    def reconcile_with_broker(self):

        # ── PAPER MODE SLOT RECONCILIATION ────────────────────────────
        # active_trade is never set in PAPER mode. We separately track
        # _paper_trade_id to know when a paper slot should be unlocked.
        # This runs every 10s via gtt_reconciliation_loop — zero overhead.
        if not self.active_trade:
            if self.in_trade and self._paper_trade_id:
                try:
                    from app.db.paper_trades_repo import get_paper_trade_by_id
                    trade = get_paper_trade_by_id(self._paper_trade_id)
                    if not trade or trade.get("state") != "OPEN":
                        self._log(
                            f"[PAPER_RECONCILE] Paper trade closed — "
                            f"unlocking slot SLOT={self.name} "
                            f"trade_id={self._paper_trade_id}"
                        )
                        self._paper_trade_id = None
                        self.in_trade = False
                        self.selection_locked = False
                        self._save_state()
                except Exception as e:
                    write_audit_log(
                        f"[PAPER_RECONCILE][ERROR] SLOT={self.name} ERR={e}"
                    )
            return

        # ── LIVE MODE RECONCILIATION (unchanged) ──────────────────────

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

        trade_id = self.active_trade.trade_id

        exit_ltp = LTPStore.get(self.active_trade.symbol) or 0.0

        exit_reason = "GTT_TP"

        if exit_ltp <= self.active_trade.sl_price:
            exit_reason = "GTT_SL"

        close_trade(
            trade_id=trade_id,
            exit_price=exit_ltp,
            exit_order_id=None,
            exit_reason=exit_reason,
        )

        self._send_exit_notification(trade_id)

        self.active_trade = None
        self.in_trade = False
        self.selection_locked = False
        self._save_state()

    # ==================================================
    # ENTRY
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

        trade_execution_mode = cfg.get("trade_execution_mode", "LIVE")

        # ==================================================
        # PAPER MODE
        # ==================================================

        if trade_execution_mode == "PAPER":
            try:
                from app.trading.paper_trade_recorder import PaperTradeRecorder

                paper_trade_id = PaperTradeRecorder.record_entry(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    token=token,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    candle_ts=candle_ts,
                )

                # ── LOCK SLOT ─────────────────────────────────────────
                # record_entry() returns None when blocked (duplicate side
                # guard, trade_on=False, etc.). Only lock when confirmed.
                # Without this lock, the slot stayed free after paper entry
                # and the same slot could be entered multiple times,
                # causing >2 CE trades simultaneously.
                if paper_trade_id:
                    self._paper_trade_id = paper_trade_id
                    self.in_trade = True
                    self.selection_locked = True
                    self._save_state()
                    self._log(
                        f"[PAPER] SLOT LOCKED SLOT={self.name} "
                        f"SYMBOL={symbol} trade_id={paper_trade_id}"
                    )
                else:
                    self._log(
                        f"[PAPER] ENTRY BLOCKED (record_entry returned None) "
                        f"SLOT={self.name} SYMBOL={symbol}"
                    )

                return

            except Exception as e:
                write_audit_log(
                    f"[PAPER][ERROR] RECORD FAILED SYMBOL={symbol} ERR={repr(e)}"
                )
                return

        # ==================================================
        # LIVE MODE
        # ==================================================

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
        )

        conn = get_conn()

        conn.execute(
            """
            UPDATE trades
            SET
                exit_time = strftime('%s','now'),
                exit_reason = 'BROKER_EXIT',
                state = 'CLOSED'
            WHERE slot = ?
            AND exit_time IS NULL
            """,
            (self.name,),
        )

        conn.commit()

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
        )

        self.active_trade = trade
        self.in_trade = True
        self._save_state()

        # 🔔 ENTRY TELEGRAM
        self._send_entry_notification(trade)

        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=filled_qty,
                sl_price=sl_price,
                tp_price=tp_price,
            )
        except Exception:
            self._force_exit("BROKER_EXIT")
            return

        self.active_trade.gtt_id = gtt_id
        self.active_trade.state = STATE_PROTECTED
        self._save_state()

        update_gtt(trade_id=trade.trade_id, gtt_id=gtt_id)

    # ==================================================
    # FORCE EXIT (SL/TP/ERROR)
    # ==================================================

    def _force_exit(self, reason: str):
        if not self.active_trade:
            return

        trade_id = self.active_trade.trade_id

        # --------------------------------------------------
        # Normalize exit reason to satisfy DB constraint
        # --------------------------------------------------
        allowed_reasons = {
            "TP",
            "SL",
            "MANUAL",
            "BROKER_EXIT",
            "GTT_TP",
            "GTT_SL",
        }

        safe_reason = reason if reason in allowed_reasons else "BROKER_EXIT"

        if safe_reason != reason:
            self._log(
                f"[EXIT_REASON_NORMALIZED] original={reason} → safe={safe_reason}"
            )

        try:
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

        self.active_trade = None
        self.in_trade = False
        self.selection_locked = False
        self._save_state()

    # ==================================================
    # SKIP
    # ==================================================

    def _skip(self, reason: str, symbol: str, price: float):
        self._log(f"[SKIP] SLOT={self.name} REASON={reason} SYMBOL={symbol}")
        update_signal(
            slot=self.name,
            symbol=symbol,
            action="SKIPPED",
            reason=reason,
            price=price,
        )