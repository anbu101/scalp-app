from dataclasses import dataclass, asdict, field
from typing import Optional
import time
import json
import threading
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
from app.event_bus.inapp_events import record_alert
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

# Background entry-fill confirmation (SHORT entries) — Option A.
# Place SELL, then in the background poll the order book: on COMPLETE place the
# GTT and record the true fill; on DEAD release the slot; if still unfilled at
# the cancel cap, cancel the order (signal is stale after one candle). The GTT
# is never placed before a confirmed fill, so it cannot open an unintended
# position. The brief window between fill and GTT is covered by the engine's
# tick-driven SL/TP exit.
# Option A: confirm fill FIRST, then place GTT. A SCALP signal is valid for one
# candle (~60s), so an unfilled entry is cancelled at this cap rather than left
# resting into a stale fill. The GTT is placed ONLY after a confirmed fill, so
# it can never open an unintended position on an order that didn't fill.
_ENTRY_FILL_CANCEL_S        = 50    # cancel unfilled SELL entry after 50s
_ENTRY_FILL_POLL_INTERVAL_S = 2

# Terminal "dead" order statuses per Kite Connect — order never resulted in a
# position.
_DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}


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
        gtt_id    = self.active_trade.gtt_id

        # ── Prefer the TRUE GTT fill over an LTP snapshot ──────────────
        # The position is flat because a GTT (SL or TP) fired at the broker.
        # That triggered GTT carries the actual executed average_price and tells
        # us which leg (SL vs TP) fired — both far more accurate than guessing
        # the exit from a cached LTP. Mirrors BB's _recon_close_leg extraction.
        # Falls back to LTP-snapshot + sl/tp geometry only if the GTT result
        # can't be read (e.g. GTT already purged from the book).
        exit_price  = None
        exit_reason = None
        exit_oid    = None

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
                            # GTT order index → leg. For SHORT the OCO order
                            # list is [TP(buy@lower), SL(buy@upper)]; for LONG
                            # it is [SL(sell@lower), TP(sell@upper)].
                            if direction == "SHORT":
                                reason_map = {0: "GTT_TP", 1: "GTT_SL"}
                            else:
                                reason_map = {0: "GTT_SL", 1: "GTT_TP"}
                            exit_reason = reason_map.get(i, "BROKER_EXIT")
                            fill = result.get("average_price")
                            if fill and float(fill) > 0:
                                exit_price = float(fill)
                                exit_oid   = str(result.get("order_id"))
                            break
            except Exception as e:
                self._log(f"[RECON][GTT_FILL_READ_FAIL] {self.active_trade.symbol} ERR={e}")

        # ── Fallback: LTP snapshot + direction-aware reason inference ──
        if exit_price is None or exit_reason is None:
            exit_ltp = LTPStore.get(self.active_trade.symbol) or 0.0
            if exit_price is None:
                exit_price = exit_ltp
            if exit_reason is None:
                if direction == "SHORT":
                    exit_reason = "GTT_SL" if exit_ltp >= self.active_trade.sl_price else "GTT_TP"
                else:
                    exit_reason = "GTT_SL" if exit_ltp <= self.active_trade.sl_price else "GTT_TP"

        close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_order_id=exit_oid,
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
    # SHORT ENTRY — SCALP_V1
    #
    # FLOW (fill-resolution fix):
    #   1. Place SELL limit entry → returns (sell_id, limit_price, qty).
    #   2. Record entry_price = limit_price IMMEDIATELY (the protected limit,
    #      e.g. ltp*0.99). Mark in_trade, insert DB row, notify.
    #   3. Place the inverted GTT OCO using the SIGNAL's sl_price / tp_price
    #      DIRECTLY. These are already final: StrategyEngine computed them as
    #      entry + risk*rr and ALREADY applied the max_sl_points cap. We must
    #      NOT recompute here — recomputing would silently discard the Max_SL
    #      cap. Protection is therefore correct and immediate.
    #   4. Spawn a background thread that polls the order book for the true
    #      fill and UPDATEs entry_price for accurate P&L; on a dead order it
    #      tears down the GTT + DB row + slot. The GTT is already protecting
    #      the position, so this thread is never on the critical path.
    #
    # GTT levels NEVER depend on the fill, so there is no unprotected window
    # and no GTT churn.
    # ==================================================

    def on_sell_signal(
        self,
        *,
        symbol: str,
        token: int,
        candle_ts: int,
        entry_price: float,
        sl_price: float,   # ABOVE entry — already capped by max_sl upstream
        tp_price: float,   # BELOW entry — prev red low
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
        # Returns (order_id, limit_price, qty). limit_price is the protected
        # limit (ltp*0.99) — recorded as the provisional entry price.
        sell_id, limit_price, filled_qty = self.executor.place_sell_entry(
            symbol=broker_symbol,
            token=token,
            qty=qty,
        )

        if filled_qty <= 0:
            self.selection_locked = False
            return

        # Provisional entry = the limit price. The background thread upgrades
        # this to the true fill once the order reaches COMPLETE.
        provisional_entry = limit_price if (limit_price and limit_price > 0) else entry_price

        # ── SL/TP come straight from the signal (already max_sl-capped) ──
        # DO NOT recompute from the fill/limit — that would discard the
        # max_sl_points cap that StrategyEngine already applied.
        actual_sl = sl_price
        actual_tp = tp_price

        trade = Trade(
            trade_id=str(uuid.uuid4()),
            symbol=symbol,
            token=token,
            qty=filled_qty,
            buy_order_id=sell_id,        # field reused as entry_order_id
            buy_price=provisional_entry, # field reused as entry_price
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
            entry_price=provisional_entry,
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

        # ── NO GTT YET (Option A) ─────────────────────────────
        # We do NOT place the protective GTT until the SELL fill is confirmed.
        # Placing it now would risk the GTT triggering (and opening an
        # unintended LONG) if the SELL never fills but price hits a trigger.
        # The background thread places the GTT the instant the fill is COMPLETE.
        # The window between fill and GTT is covered by the engine's tick-exit.
        self._log(
            f"[SHORT][ENTRY_PROVISIONAL] SLOT={self.name} symbol={symbol} "
            f"entry≈{provisional_entry:.2f} (limit; fill + GTT pending) "
            f"tp={actual_tp:.2f} sl={actual_sl:.2f}"
        )

        # ── Background: confirm fill → place GTT; or cancel if unfilled ──
        self._spawn_fill_confirm(trade.trade_id, sell_id, symbol)

    # ==================================================
    # BACKGROUND FILL CONFIRMATION (SHORT entries) — Option A
    # ==================================================

    def _spawn_fill_confirm(self, trade_id: str, order_id: str, symbol: str):
        t = threading.Thread(
            target=self._confirm_fill_worker,
            args=(trade_id, order_id, symbol),
            daemon=True,
            name=f"scalp-fill-{self.name}-{trade_id[:8]}",
        )
        t.start()

    def _confirm_fill_worker(self, trade_id: str, order_id: str, symbol: str):
        """
        Poll the order book until the SELL entry is COMPLETE / DEAD, or until
        the 50s cancel cap.

        COMPLETE → place the GTT now (signal SL/TP, already max_sl-capped),
                   record the true fill, mark PROTECTED.
        DEAD     → never opened a position; close the DB row, release slot,
                   alert. (No GTT to cancel — none was placed.)
        unfilled at cap → cancel the order, then RE-CHECK status to handle a
                   fill that raced the cancel:
                     post-cancel COMPLETE → treat as fill (place GTT, protect)
                     post-cancel partial  → log + alert, leave for manual
                     clean cancel         → close row ENTRY_TIMEOUT, release slot
        """
        start = time.time()

        while time.time() - start < _ENTRY_FILL_CANCEL_S:
            try:
                info = self.executor.get_order_fill(order_id)
            except Exception as e:
                write_audit_log(
                    f"[SHORT][FILL_POLL_ERR] {symbol} order_id={order_id} ERR={e}"
                )
                time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)
                continue

            status = (info.get("status") or "").upper()
            avg    = info.get("avg_price") or 0.0

            if status == "COMPLETE":
                if avg > 0:
                    self._on_entry_filled(trade_id, symbol, float(avg))
                    return
                # COMPLETE but avg not yet populated — wait one more cycle.

            elif status in _DEAD_ORDER_STATUSES:
                self._on_entry_dead(trade_id, symbol, status)
                return

            time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)

        # ── Unfilled at cap → cancel, then resolve the race ──
        self._cancel_unfilled_entry(trade_id, order_id, symbol)

    # --------------------------------------------------
    # Terminal handlers
    # --------------------------------------------------

    def _on_entry_filled(self, trade_id: str, symbol: str, fill_price: float):
        """
        Fill confirmed. Record the true entry, then place the protective GTT.
        Guarded: no-op if this trade is no longer the active one.
        """
        at = self.active_trade
        if at is None or at.trade_id != trade_id:
            write_audit_log(
                f"[SHORT][FILL_STALE] {symbol} trade_id={trade_id} "
                f"fill={fill_price:.2f} — trade no longer active, skipping"
            )
            return

        # 1) Record the true fill (entry_price) for accurate (entry-exit) P&L.
        try:
            conn = get_conn()
            # Record the TRUE fill as entry_price unconditionally — even if the
            # trade already closed (fast scalps can hit GTT-TP before this
            # fill-confirm thread runs). entry_price is historical fact; the
            # old `exit_time IS NULL` guard caused the provisional limit price
            # to stick whenever the close raced ahead of fill confirmation.
            conn.execute(
                "UPDATE trades SET entry_price = ? WHERE trade_id = ?",
                (fill_price, trade_id),
            )
            conn.commit()
        except Exception as e:
            write_audit_log(
                f"[SHORT][FILL_DB_UPDATE_FAIL] {symbol} trade_id={trade_id} ERR={e}"
            )
        at.buy_price = fill_price
        self._save_state()

        write_audit_log(
            f"[SHORT][FILL_CONFIRMED] SLOT={self.name} {symbol} "
            f"trade_id={trade_id} entry={fill_price:.2f} — placing GTT"
        )

        # 2) Place the protective GTT NOW (signal SL/TP — fill-independent,
        #    already max_sl-capped). Only now is there a real short to protect.
        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=at.qty,
                sl_price=at.sl_price,
                tp_price=at.tp_price,
                direction="SHORT",
            )
        except Exception as e:
            self._log(
                f"[SHORT][GTT_FAIL] symbol={symbol} ERR={e} — "
                f"position is UNPROTECTED. Tick-exit / EOD will close it."
            )
            record_alert(
                code="GTT_FAIL",
                message=f"{symbol} ({self.name}): protective GTT failed — position UNPROTECTED, relies on tick/EOD exit.",
                severity="error",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode="live",
            )
            at.state = STATE_PROTECTED   # keep tradeable; EOD squareoff backstop
            self._save_state()
            return

        # Re-check the trade is still active (could have exited via tick during
        # the GTT round-trip); if so, cancel the GTT we just placed.
        if self.active_trade is None or self.active_trade.trade_id != trade_id:
            write_audit_log(
                f"[SHORT][GTT_RACE] {symbol} trade closed during GTT placement "
                f"— cancelling just-placed gtt_id={gtt_id}"
            )
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(f"[SHORT][GTT_RACE_CANCEL_WARN] {symbol} ERR={e}")
            return

        at.gtt_id = gtt_id
        at.state  = STATE_PROTECTED
        self._save_state()
        update_gtt(trade_id=trade_id, gtt_id=gtt_id)

        self._log(
            f"[SHORT][ENTRY_CONFIRMED] SLOT={self.name} symbol={symbol} "
            f"entry={fill_price:.2f} tp={at.tp_price:.2f} sl={at.sl_price:.2f} "
            f"gtt={gtt_id}"
        )

    def _on_entry_dead(self, trade_id: str, symbol: str, status: str):
        """SELL never opened a position. No GTT was placed. Release slot."""
        at = self.active_trade
        if at is None or at.trade_id != trade_id:
            write_audit_log(
                f"[SHORT][DEAD_ENTRY_STALE] {symbol} trade_id={trade_id} "
                f"status={status} — trade no longer active"
            )
            return

        write_audit_log(
            f"[SHORT][DEAD_ENTRY] SLOT={self.name} {symbol} trade_id={trade_id} "
            f"status={status} — no position opened, releasing slot"
        )

        self._close_entry_row(trade_id, symbol, "ENTRY_REJECTED")
        self.active_trade     = None
        self.in_trade         = False
        self.selection_locked = False
        self._save_state()

        self._alert_entry_aborted(symbol, status, "no position opened")

    def _cancel_unfilled_entry(self, trade_id: str, order_id: str, symbol: str):
        """
        Order still unfilled at the 50s cap. Cancel it, then re-check status to
        resolve a fill that raced the cancel (Kite quirk: fill can land between
        our last poll and the cancel taking effect).
        """
        at = self.active_trade
        if at is None or at.trade_id != trade_id:
            return  # already resolved elsewhere

        write_audit_log(
            f"[SHORT][ENTRY_TIMEOUT] SLOT={self.name} {symbol} order_id={order_id} "
            f"unfilled after {_ENTRY_FILL_CANCEL_S}s — cancelling"
        )

        try:
            self.executor.cancel_order(order_id)
        except Exception as e:
            write_audit_log(
                f"[SHORT][CANCEL_WARN] {symbol} order_id={order_id} ERR={e}"
            )

        # Give the cancel a moment to settle, then re-check.
        time.sleep(1.0)
        try:
            info = self.executor.get_order_fill(order_id)
        except Exception:
            info = {"status": None, "avg_price": 0.0, "filled_qty": 0}

        status     = (info.get("status") or "").upper()
        avg        = info.get("avg_price") or 0.0
        filled_qty = int(info.get("filled_qty") or 0)

        # Re-confirm still the active trade after the sleep.
        at = self.active_trade
        if at is None or at.trade_id != trade_id:
            return

        # Case 1: filled at the wire before cancel landed → protect it.
        if status == "COMPLETE" and filled_qty >= at.qty and avg > 0:
            write_audit_log(
                f"[SHORT][CANCEL_RACE_FILLED] {symbol} order filled before cancel "
                f"(fill={avg:.2f}) — protecting position"
            )
            self._on_entry_filled(trade_id, symbol, float(avg))
            return

        # Case 2: partial fill → DO NOT auto-handle. Log loudly + alert.
        if 0 < filled_qty < at.qty:
            write_audit_log(
                f"[SHORT][PARTIAL_FILL][MANUAL] {symbol} order_id={order_id} "
                f"filled_qty={filled_qty}/{at.qty} avg={avg:.2f} status={status} "
                f"— LEFT FOR MANUAL INTERVENTION. Position is a partial short "
                f"WITHOUT a GTT. Slot left locked to avoid auto-actions."
            )
            self._alert_entry_aborted(
                symbol, "PARTIAL_FILL",
                f"filled {filled_qty}/{at.qty} @~{avg:.2f}, NO GTT — handle manually"
            )
            # Intentionally leave active_trade / slot as-is so nothing automated
            # touches a partial short. Manual cleanup required.
            return

        # Case 3: clean cancel, nothing filled → release the slot.
        write_audit_log(
            f"[SHORT][ENTRY_CANCELLED] {symbol} order_id={order_id} "
            f"clean cancel (filled_qty={filled_qty}) — releasing slot"
        )
        record_alert(
            code="ENTRY_TIMEOUT",
            message=f"{symbol} ({self.name}): sell not filled in 50s — cancelled, no position.",
            severity="warning",
            strategy_id=self.strategy_id,
            symbol=symbol,
            mode="live",
        )
        self._close_entry_row(trade_id, symbol, "ENTRY_TIMEOUT")

    # --------------------------------------------------
    # Small shared helpers for the worker
    # --------------------------------------------------

    def _close_entry_row(self, trade_id: str, symbol: str, reason: str):
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=None,
                exit_order_id=None,
                exit_reason=reason,
            )
        except Exception as e:
            write_audit_log(
                f"[SHORT][ENTRY_ROW_CLOSE_FAIL] {symbol} trade_id={trade_id} "
                f"reason={reason} ERR={e}"
            )

    def _alert_entry_aborted(self, symbol: str, status: str, detail: str):
        # In-app alert (bell + toast + sound). PARTIAL_FILL is the only one that
        # needs manual action, so it is "error"; a plain rejection is "error"
        # too (no position taken); everything else is a "warning".
        sev = "error" if status in ("PARTIAL_FILL", "REJECTED", "CANCELLED", "LAPSED") else "warning"
        record_alert(
            code=("PARTIAL_FILL" if status == "PARTIAL_FILL" else "DEAD_ENTRY"),
            message=f"{symbol} ({self.name}): {status} — {detail}",
            severity=sev,
            strategy_id=self.strategy_id,
            symbol=symbol,
            mode="live",
        )
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({
                "message": (
                    f"SCALP entry {status} for {symbol} ({self.name})\n{detail}"
                ),
                "severity": "warning",
            })
        except Exception:
            pass

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

        symbol = self.active_trade.symbol
        qty    = self.active_trade.qty

        # ── CANCEL THE PROTECTIVE GTT FIRST (orphan-GTT guard) ──────────
        # _force_exit previously never cancelled the GTT: an MTM/EOD exit
        # bought back the short but left the OCO GTT (two armed BUYs) on a
        # flat position — if price later crossed either trigger, it fired an
        # unintended BUY. Cancel BEFORE the position-verify: if the GTT fires
        # first and wins the race, the verify below sees the flat position
        # and takes the ALREADY_FLAT path — no double order. Fully wrapped:
        # no failure here may ever block the flatten.
        gtt_id = self.active_trade.gtt_id
        if gtt_id:
            gone = True
            try:
                if hasattr(self.executor, "cancel_gtt_verified"):
                    gone = self.executor.cancel_gtt_verified(gtt_id)
                else:
                    self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                self._log(
                    f"[FORCE_EXIT][GTT_CANCEL_WARN] {symbol} gtt={gtt_id} "
                    f"ERR={e} — proceeding with exit"
                )
            if not gone:
                self._log(
                    f"[FORCE_EXIT][GTT_ORPHAN] {symbol} gtt={gtt_id} STILL ARMED "
                    f"after cancel — alerting; still flattening"
                )
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({
                        "message": (
                            f"SCALP_V1 GTT {gtt_id} for {symbol} could NOT be cancelled "
                            f"(still armed). Flattening the position now, but DELETE THIS "
                            f"GTT MANUALLY in Kite to avoid an unintended order."
                        ),
                        "severity": "error",
                    })
                except Exception:
                    pass
                
        # ── POSITION-VERIFY GUARD (prevents the GTT-vs-square-off phantom order) ──
        # Before sending ANY exit order, confirm the broker still holds this
        # position. A GTT (SL/TP) can fill at the broker a beat before our
        # in-memory state is reconciled; without this check, _force_exit would
        # place a SECOND order (for a SHORT: a BUY) against a position that is
        # already flat, leaving an unintended opposite position.
        #
        # FAIL-OPEN on uncertainty: if we cannot enumerate positions (API error),
        # we DO place the exit — a missed close is worse than a possible double.
        broker_flat = False
        try:
            positions = self.executor.get_open_positions() or []
            still_open = any(
                p.get("tradingsymbol") == symbol and p.get("quantity", 0) != 0
                for p in positions
            )
            broker_flat = not still_open
        except Exception as e:
            # Could not verify — assume still open and proceed (fail-open).
            self._log(f"[FORCE_EXIT][POS_VERIFY_FAIL] {symbol} ERR={e} — proceeding with exit")
            broker_flat = False

        if broker_flat:
            # Position already closed at the broker (almost always: the GTT
            # already fired). Do NOT send another order. Close the DB row using
            # the GTT/last price and release the slot.
            self._log(
                f"[FORCE_EXIT][ALREADY_FLAT] {symbol} broker shows no position — "
                f"GTT likely already closed it. Closing DB row only, NO order sent. "
                f"reason={safe_reason}"
            )
            try:
                close_trade(
                    trade_id=trade_id,
                    exit_price=LTPStore.get(symbol),
                    exit_order_id=None,
                    exit_reason=safe_reason,
                )
                self._send_exit_notification(trade_id)
            except Exception as e:
                self._log(f"[FORCE_EXIT][DB_CLOSE_FAIL] {symbol} ERR={e}")

            self.active_trade     = None
            self.in_trade         = False
            self.selection_locked = False
            self._save_state()
            return

        try:
            symbol = self.active_trade.symbol

            if direction == "SHORT":
                exit_id = self.executor.place_buy_exit(
                    symbol=symbol,
                    qty=self.active_trade.qty,
                    reason=safe_reason,
                )
            else:
                exit_id = self.executor.place_exit(
                    symbol=symbol,
                    qty=self.active_trade.qty,
                    reason=safe_reason,
                )

            # Poll the actual exit fill instead of recording an LTP snapshot.
            # The order book can lag a few seconds; try ~7.5s for a COMPLETE
            # average_price, then fall back to LTP only if it never confirms.
            exit_fill = 0.0
            for _ in range(15):  # 15 * 0.5s = 7.5s
                try:
                    info = self.executor.get_order_fill(exit_id)
                    if (info.get("status") or "").upper() == "COMPLETE" and (info.get("avg_price") or 0) > 0:
                        exit_fill = float(info["avg_price"])
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            exit_price = exit_fill if exit_fill > 0 else (LTPStore.get(symbol) or 0.0)

            if exit_fill <= 0:
                self._log(
                    f"[FORCE_EXIT][FILL_UNCONFIRMED] {symbol} order_id={exit_id} "
                    f"— using LTP snapshot {exit_price} (verify against contract note)"
                )

            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
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