# backend/app/engine/ha_options/ha_trade_manager.py
"""
HA Trade Manager
================
Handles all trade lifecycle for HA_V1.

EXIT DESIGN (CRITICAL):
  TP  → checked on EVERY TICK (live price).  As soon as ltp >= tp_price the
        trade is closed immediately, regardless of candle boundary.

  SL  → checked on CANDLE CLOSE only.  Wicks that cross SL mid-candle are
        ignored; only a candle that CLOSES at or below SL triggers an exit.

  This applies identically to PAPER and LIVE modes.

ENTRY FILL RESOLUTION (FIX):
  HA entry runs on the WS tick thread (via _on_candle_close), and HA's whole
  exit design is "TP on every tick".  The previous _enter_live() blocked that
  thread up to 120s polling for the fill — freezing TP/SL monitoring for the
  ENTIRE HA universe while one entry waited.  It also could not tell a slow
  fill apart from a rejected order, so a dead order would time out and then
  proceed to record a phantom position and monitor it.

  NEW (Option 1 — background confirm):
    enter() places the BUY, records the trade in self._live using the LIMIT
    price as the provisional entry, sets up monitoring, and returns True
    IMMEDIATELY — the tick thread is never blocked.  A background thread then
    confirms the true fill:
      COMPLETE → patch entry_price (in-memory + DB) for accurate P&L
      DEAD     → remove the phantom from self._live, close the DB row, and call
                 the engine's on_entry_dead(symbol, side) hook to roll back
                 monitoring + signal-engine state
      timeout  → leave the provisional entry, log RECONCILE_NEEDED (120s cap)

  SL/TP are FILL-INDEPENDENT:
    SL = the signal's red-candle low (fixed).
    TP = computed from the SIGNAL entry_ltp (NOT the fill) so it never moves
         when the fill lands.  Only the recorded entry_price is patched.

EXECUTION MODES:
  LIVE  → real broker orders.
  PAPER → simulated orders via PaperTradeRecorder.
  OFF   → strategy still collects ticks/candles/EMA, but NO NEW ENTRIES.
          Exits for an already-open trade still run.

LIVE ORDER DESIGN:
  Entry  → protected limit BUY at LTP × 1.03 (3% buffer).
  TP     → NO GTT.  Tick-level monitor handles TP via limit SELL.
  SL     → NO GTT.  Candle-close monitor places a limit SELL when close <= sl.
"""

import time
import uuid
import threading
from datetime import datetime
from typing import Optional, Dict

from app.risk.strategy_max_loss_guard import evaluate_strategy_risk
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore
from app.config.strategy_loader import load_strategy_config
from app.db.trades_repo import insert_trade, close_trade
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.db.paper_trades_repo import (
    get_open_paper_trades_by_side,
)
from app.engine.ha_options.ha_signal_engine import HASignalEngine
from app.event_bus.inapp_events import record_alert
from app.api.telegram_api import (
    notify_trade_entry,
    notify_sl_exit,
    notify_tp_exit,
    notify_manual_exit,
)


# Background entry-fill confirmation (LIVE entries).
# Never on the critical path — monitoring is already live by the time this
# runs; it only patches the recorded entry_price or rolls back a dead order.
_ENTRY_FILL_CONFIRM_CAP_S   = 120
_ENTRY_FILL_POLL_INTERVAL_S = 2

# Terminal "dead" Kite order statuses — order never opened a position.
_DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}


# ──────────────────────────────────────────────────────────────────
# Live trade record
# ──────────────────────────────────────────────────────────────────

class _LiveTrade:
    __slots__ = (
        "trade_id", "symbol", "side", "qty",
        "entry_price", "sl_price", "tp_price",
        "entry_order_id", "fill_confirmed",
    )

    def __init__(self, trade_id, symbol, side, qty,
                 entry_price, sl_price, tp_price,
                 entry_order_id=None, fill_confirmed=False):
        self.trade_id       = trade_id
        self.symbol         = symbol
        self.side           = side
        self.qty            = qty
        self.entry_price    = entry_price
        self.sl_price       = sl_price
        self.tp_price       = tp_price
        self.entry_order_id = entry_order_id
        self.fill_confirmed = fill_confirmed


# ──────────────────────────────────────────────────────────────────
# Trade manager
# ──────────────────────────────────────────────────────────────────

class HATradeManager:

    # Limit-order buffer above LTP for BUY entries.
    ENTRY_LIMIT_BUFFER = 1.03   # LTP × 1.03

    def __init__(
        self,
        strategy_id: str,
        trade_mode: str,
        executor,
        signal_engine: HASignalEngine,
        config: dict,
        engine=None,            # back-reference to HAOptionsTickEngine for hooks
    ):
        self.strategy_id   = strategy_id
        self._startup_mode = trade_mode
        self.executor      = executor
        self.signal_engine = signal_engine
        self.config        = config
        self._engine       = engine    # may be set later via attach_engine()

        # Fallbacks (constructor-time values)
        self._rr_fallback       = float(config.get("risk_reward_ratio", 2.0))
        self._lot_fallback      = int(config.get("quantity", {}).get("lots", 1))
        self._lot_size_fallback = int(config.get("quantity", {}).get("lot_size", 65))

        # Live trade state — keyed by side ("CE" / "PE")
        self._live: Dict[str, _LiveTrade] = {}

        # Guard: prevents double-exit when TP fires from two rapid ticks
        # before the first exit finishes placing the sell order.
        self._tp_exit_in_progress: set = set()   # set of sides currently exiting

    def attach_engine(self, engine):
        """Wire the tick engine back-reference (for on_entry_dead rollback)."""
        self._engine = engine

    # ── Live config readers ───────────────────────────────────────

    def _mode(self) -> str:
        """
        Returns the current effective trade mode from live config.
        Valid values: "LIVE", "PAPER", "OFF".
        """
        try:
            m = load_strategy_config(self.strategy_id).get(
                "trade_execution_mode", self._startup_mode
            )
            if m == "OFF":
                return "OFF"
            if self._startup_mode == "PAPER" and m == "LIVE":
                return "PAPER"
            return m
        except Exception:
            return self._startup_mode

    def is_off(self) -> bool:
        """True when the strategy is in OFF mode — new entries suppressed."""
        return self._mode() == "OFF"

    def _rr(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "risk_reward_ratio", self._rr_fallback
                ) or self._rr_fallback
            )
        except Exception:
            return self._rr_fallback

    def _qty(self) -> int:
        try:
            cfg = load_strategy_config(self.strategy_id)
            lots = int(cfg.get("quantity", {}).get("lots", self._lot_fallback) or self._lot_fallback)
            size = int(cfg.get("quantity", {}).get("lot_size", self._lot_size_fallback) or self._lot_size_fallback)
            return lots * size
        except Exception:
            return self._lot_fallback * self._lot_size_fallback

    def _apply_target_override(self, entry_price: float, rr_tp: float) -> float:
        """
        Returns the effective TP price.
        If target_override.enabled and points > 0, returns entry_price + points;
        otherwise returns the R:R computed value unchanged.
        """
        try:
            override = load_strategy_config(self.strategy_id).get(
                "target_override", {}
            )
            if override.get("enabled") and float(override.get("points", 0)) > 0:
                fixed_tp = entry_price + float(override["points"])
                write_audit_log(
                    f"[HA] target_override active: "
                    f"fixed_tp={fixed_tp:.2f} "
                    f"(entry={entry_price:.2f} + {override['points']} pts)"
                )
                return fixed_tp
        except Exception:
            pass
        return rr_tp

    # ── ENTRY ─────────────────────────────────────────────────────

    def enter(self, symbol: str, side: str, entry_ltp: float, sl_price: float) -> bool:
        """
        Place entry order.  Returns True once the order is PLACED and the trade
        is registered for monitoring (Option 1 — the true fill is confirmed in
        the background; True no longer means "filled", it means "placed and
        being monitored").

        TP calculation (FILL-INDEPENDENT — uses the SIGNAL entry_ltp):
          1. Fixed target override (target_override.enabled = True)
             TP = entry_ltp + override.points
          2. R:R ratio (default)
             TP = entry_ltp + (entry_ltp - sl_price) × RR
        """
        mode = self._mode()

        if mode == "OFF":
            write_audit_log(
                f"[HA][OFF][ENTRY_SUPPRESSED] {symbol} side={side} "
                f"— strategy is OFF, no entry taken"
            )
            return False

        rr   = self._rr()
        qty  = self._qty()

        risk_block = evaluate_strategy_risk(self.strategy_id)
        if risk_block:
            write_audit_log(
                f"[HA][RISK_BLOCK] {symbol} side={side} reason={risk_block}"
            )
            return False

        risk = entry_ltp - sl_price
        if risk <= 0:
            write_audit_log(
                f"[HA][ENTRY_ABORT] {symbol} invalid risk "
                f"entry={entry_ltp:.2f} sl={sl_price:.2f}"
            )
            return False

        # TP from SIGNAL entry (fill-independent), then optional override.
        rr_tp    = entry_ltp + risk * rr
        tp_price = self._apply_target_override(entry_ltp, rr_tp)

        write_audit_log(
            f"[HA][ENTRY] {symbol} side={side} mode={mode} "
            f"entry={entry_ltp:.2f} sl={sl_price:.2f} tp={tp_price:.2f} "
            f"rr={rr} qty={qty}"
        )

        if mode == "PAPER":
            return self._enter_paper(symbol, side, entry_ltp, sl_price, tp_price)
        else:
            return self._enter_live(symbol, side, entry_ltp, sl_price, tp_price, qty)

    # ── PAPER ENTRY ───────────────────────────────────────────────

    def _enter_paper(
        self, symbol: str, side: str,
        entry_ltp: float, sl_price: float, tp_price: float,
    ) -> bool:
        paper_id = PaperTradeRecorder.record_entry(
            strategy_id=self.strategy_id,
            symbol=symbol,
            token=0,
            entry_price=entry_ltp,
            sl_price=sl_price,
            tp_price=tp_price,
            candle_ts=int(datetime.now().timestamp()),
        )
        if not paper_id:
            write_audit_log(
                f"[HA][PAPER][BLOCKED] {symbol} already has open {side} trade"
            )
            return False
        write_audit_log(f"[HA][PAPER][OK] {symbol} id={paper_id}")
        return True

    # ── LIVE ENTRY (Option 1 — non-blocking) ──────────────────────

    def _enter_live(
        self, symbol: str, side: str,
        entry_ltp: float, sl_price: float, tp_price: float, qty: int,
    ) -> bool:
        if side in self._live:
            write_audit_log(f"[HA][LIVE][SKIP] {side} already has open trade")
            return False

        # Place protected limit BUY (3% above LTP)
        limit_price = round(round(entry_ltp * self.ENTRY_LIMIT_BUFFER / 0.05) * 0.05, 2)

        write_audit_log(
            f"[HA][LIVE][BUY] {symbol} ltp={entry_ltp:.2f} limit={limit_price:.2f}"
        )

        try:
            kite = self.executor.broker_manager.get_trade_kite()
            if not kite:
                raise RuntimeError("Broker not ready")

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
            order_id = self.executor._relay_call(
                relay_fn=lambda r: r.place_order(**order_params),
                direct_fn=lambda: kite.place_order(**order_params),
                op_name="HA_BUY",
                symbol=symbol,
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][BUY_FAIL] {symbol} ERR={repr(e)}")
            return False

        # ── Record trade IMMEDIATELY with provisional (limit) entry ──
        # SL/TP are fill-independent (SL=red low, TP from signal entry), so
        # monitoring is correct from this instant. The background thread will
        # patch entry_price to the true fill for P&L accuracy.
        trade_id = str(uuid.uuid4())
        try:
            insert_trade(
                trade_id=trade_id,
                strategy_id=self.strategy_id,
                slot=side,
                symbol=symbol,
                token=0,
                entry_price=limit_price,
                qty=qty,
                buy_order_id=order_id,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_mode="GTT",
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_FAIL] {e}")

        self._live[side] = _LiveTrade(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=limit_price,     # provisional
            sl_price=sl_price,
            tp_price=tp_price,
            entry_order_id=order_id,
            fill_confirmed=False,
        )

        write_audit_log(
            f"[HA][LIVE][ENTRY_PROVISIONAL] {symbol} side={side} "
            f"entry≈{limit_price:.2f} (limit; fill pending) "
            f"sl={sl_price:.2f} tp={tp_price:.2f} — monitoring live"
        )

        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "side": side,
                "entry_price": limit_price,
                "quantity": qty,
                "sl": sl_price,
                "tp": tp_price,
            })
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][ENTRY_FAIL] {e}")

        # ── Confirm fill in the background (never blocks the tick thread) ──
        self._spawn_fill_confirm(side, trade_id, order_id, symbol)

        return True

    # ── BACKGROUND FILL CONFIRMATION ──────────────────────────────

    def _spawn_fill_confirm(self, side, trade_id, order_id, symbol):
        threading.Thread(
            target=self._confirm_fill_worker,
            args=(side, trade_id, order_id, symbol),
            daemon=True,
            name=f"ha-fill-{side}-{trade_id[:8]}",
        ).start()

    def _confirm_fill_worker(self, side, trade_id, order_id, symbol):
        """
        Poll the order book for the true fill (≤120s).

        COMPLETE → patch entry_price (in-memory + DB) for accurate P&L.
        DEAD     → remove the phantom from self._live, close the DB row, and
                   roll back engine monitoring + signal state via on_entry_dead.
        timeout  → leave the provisional entry; log RECONCILE_NEEDED.

        Uses get_order_fill if available (distinguishes DEAD from pending);
        falls back to get_last_avg_price if the executor lacks it.
        """
        start = time.time()

        while time.time() - start < _ENTRY_FILL_CONFIRM_CAP_S:
            status = None
            avg    = 0.0
            try:
                if hasattr(self.executor, "get_order_fill"):
                    info   = self.executor.get_order_fill(order_id)
                    status = (info.get("status") or "").upper()
                    avg    = info.get("avg_price") or 0.0
                else:
                    avg = self.executor.get_last_avg_price(order_id) or 0.0
            except Exception as e:
                write_audit_log(f"[HA][FILL_POLL_ERR] {symbol} order_id={order_id} ERR={e}")
                time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)
                continue

            if status == "COMPLETE" or (status is None and avg > 0):
                if avg > 0:
                    self._apply_confirmed_fill(side, trade_id, symbol, float(avg))
                    return
                # COMPLETE but price not yet populated — wait one more cycle.

            elif status in _DEAD_ORDER_STATUSES:
                self._handle_dead_entry(side, trade_id, symbol, status)
                return

            time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)

        write_audit_log(
            f"[HA][FILL_TIMEOUT][RECONCILE_NEEDED] {symbol} side={side} "
            f"order_id={order_id} — true fill not confirmed in "
            f"{_ENTRY_FILL_CONFIRM_CAP_S}s; recorded entry remains the limit estimate."
        )
        record_alert(
            code="FILL_TIMEOUT",
            message=f"{symbol} ({side}): fill not confirmed in {_ENTRY_FILL_CONFIRM_CAP_S}s; recorded entry is the limit estimate and will be corrected on reconcile.",
            severity="info",
            strategy_id=self.strategy_id,
            symbol=symbol,
            mode="live",
        )

    def _apply_confirmed_fill(self, side, trade_id, symbol, fill_price: float):
        """Patch entry_price to the true fill — only if still the active trade."""
        trade = self._live.get(side)
        if trade is None or trade.trade_id != trade_id:
            write_audit_log(
                f"[HA][FILL_STALE] {symbol} side={side} trade_id={trade_id} "
                f"fill={fill_price:.2f} — trade no longer active, skipping"
            )
            return

        trade.entry_price    = fill_price
        trade.fill_confirmed = True
        try:
            from app.db.sqlite import get_conn
            conn = get_conn()
            conn.execute(
                "UPDATE trades SET entry_price = ? WHERE trade_id = ? AND exit_time IS NULL",
                (fill_price, trade_id),
            )
            conn.commit()
        except Exception as e:
            write_audit_log(f"[HA][FILL_DB_UPDATE_FAIL] {symbol} trade_id={trade_id} ERR={e}")

        write_audit_log(
            f"[HA][LIVE][FILL_CONFIRMED] {symbol} side={side} "
            f"entry updated → {fill_price:.2f}"
        )

    def _handle_dead_entry(self, side, trade_id, symbol, status):
        """
        Entry order never opened a position. Remove the phantom from in-memory
        state, close the DB row, and roll back engine monitoring + signal state.
        No-op if the trade is no longer the active one.
        """
        trade = self._live.get(side)
        if trade is None or trade.trade_id != trade_id:
            write_audit_log(
                f"[HA][DEAD_ENTRY_STALE] {symbol} side={side} trade_id={trade_id} "
                f"status={status} — trade no longer active"
            )
            return

        write_audit_log(
            f"[HA][DEAD_ENTRY] {symbol} side={side} trade_id={trade_id} "
            f"status={status} — removing phantom, rolling back monitoring"
        )

        # Remove in-memory trade (stops TP/SL checks immediately).
        self._live.pop(side, None)

        # Close the DB row as a no-fill abort.
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=None,
                exit_order_id=None,
                exit_reason="ENTRY_REJECTED",
            )
        except Exception as e:
            write_audit_log(f"[HA][DEAD_ENTRY][DB_CLOSE_FAIL] {symbol} trade_id={trade_id} ERR={e}")

        # Roll back signal-engine state directly.
        try:
            self.signal_engine.notify_exit(side)
        except Exception:
            pass

        # Roll back engine monitoring (remove from _active_trade_symbols).
        if self._engine is not None:
            try:
                self._engine.on_entry_dead(symbol, side)
            except Exception as e:
                write_audit_log(f"[HA][DEAD_ENTRY][ENGINE_HOOK_FAIL] {symbol} ERR={e}")

        # Alert.
        record_alert(
            code="DEAD_ENTRY",
            message=f"{symbol} ({side}): buy order {status.lower()} — phantom removed, monitoring rolled back. No position opened.",
            severity="error",
            strategy_id=self.strategy_id,
            symbol=symbol,
            mode="live",
        )
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({
                "message": (
                    f"HA_V1 entry order {status} for {symbol} ({side})\n"
                    f"trade_id={trade_id} — no position opened.\n"
                    f"Phantom removed, monitoring rolled back."
                ),
                "severity": "warning",
            })
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════
    # TP  —  checked on EVERY TICK (live price)
    # ══════════════════════════════════════════════════════════════

    def check_tp_on_tick(self, symbol: str, ltp: float):
        """
        Called by ha_tick_engine on EVERY incoming tick for this symbol.
        Exits the trade immediately when ltp >= tp_price.
        """
        LTPStore.update(symbol, ltp)

        mode = self._mode()
        side = "CE" if symbol.endswith("CE") else "PE"

        if mode == "PAPER":
            self._check_paper_tp_tick(symbol, side, ltp)
        elif mode == "LIVE":
            self._check_live_tp_tick(symbol, side, ltp)
        else:  # OFF — manage whatever is actually open
            if side in self._live:
                self._check_live_tp_tick(symbol, side, ltp)
            else:
                self._check_paper_tp_tick(symbol, side, ltp)

    def _check_paper_tp_tick(self, symbol: str, side: str, ltp: float):
        open_trades = get_open_paper_trades_by_side(
            strategy_name=self.strategy_id,
            side=side,
        )
        for t in open_trades:
            if t["symbol"] != symbol:
                continue
            tp = t.get("tp_price") or 0
            if tp <= 0:
                continue
            if HASignalEngine.tp_hit(ltp, tp):
                write_audit_log(
                    f"[HA][PAPER][TP_TICK] {symbol} ltp={ltp:.2f} tp={tp:.2f}"
                )
                PaperTradeRecorder.force_exit(
                    paper_trade_id=t["paper_trade_id"],
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="TP",
                )
                self.signal_engine.notify_exit(side)
                break

    def _check_live_tp_tick(self, symbol: str, side: str, ltp: float):
        trade = self._live.get(side)
        if not trade or trade.symbol != symbol:
            return
        if side in self._tp_exit_in_progress:
            return
        if HASignalEngine.tp_hit(ltp, trade.tp_price):
            write_audit_log(
                f"[HA][LIVE][TP_TICK] {symbol} ltp={ltp:.2f} tp={trade.tp_price:.2f}"
            )
            self._tp_exit_in_progress.add(side)
            try:
                self._exit_live(side, "TP", exit_price_hint=ltp)
            finally:
                self._tp_exit_in_progress.discard(side)

    # ══════════════════════════════════════════════════════════════
    # SL  —  checked on CANDLE CLOSE only
    # ══════════════════════════════════════════════════════════════

    def check_sl_on_close(self, symbol: str, candle_close: float):
        """
        Called by ha_tick_engine after EVERY completed 1-minute candle.
        Exits when candle_close <= sl_price.
        """
        LTPStore.update(symbol, candle_close)

        mode = self._mode()
        side = "CE" if symbol.endswith("CE") else "PE"

        if mode == "PAPER":
            self._check_paper_sl_close(symbol, side, candle_close)
        elif mode == "LIVE":
            self._check_live_sl_close(symbol, side, candle_close)
        else:  # OFF — manage whatever is actually open
            if side in self._live:
                self._check_live_sl_close(symbol, side, candle_close)
            else:
                self._check_paper_sl_close(symbol, side, candle_close)

    def _check_paper_sl_close(self, symbol: str, side: str, candle_close: float):
        open_trades = get_open_paper_trades_by_side(
            strategy_name=self.strategy_id,
            side=side,
        )
        for t in open_trades:
            if t["symbol"] != symbol:
                continue
            sl = t.get("sl_price") or 0
            if sl <= 0:
                continue
            if HASignalEngine.sl_hit(candle_close, sl):
                write_audit_log(
                    f"[HA][PAPER][SL_CLOSE] {symbol} "
                    f"close={candle_close:.2f} sl={sl:.2f}"
                )
                PaperTradeRecorder.force_exit(
                    paper_trade_id=t["paper_trade_id"],
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="SL",
                )
                self.signal_engine.notify_exit(side)
                break

    def _check_live_sl_close(self, symbol: str, side: str, candle_close: float):
        trade = self._live.get(side)
        if not trade or trade.symbol != symbol:
            return
        if side in self._tp_exit_in_progress:
            return
        if HASignalEngine.sl_hit(candle_close, trade.sl_price):
            write_audit_log(
                f"[HA][LIVE][SL_CLOSE] {symbol} "
                f"close={candle_close:.2f} sl={trade.sl_price:.2f}"
            )
            self._exit_live(side, "SL", exit_price_hint=candle_close)

    # ══════════════════════════════════════════════════════════════
    # Legacy shim
    # ══════════════════════════════════════════════════════════════

    def check_sl_tp_on_close(self, symbol: str, candle_close: float):
        """DEPRECATED shim. Prefer check_sl_on_close() + check_tp_on_tick()."""
        self.check_sl_on_close(symbol, candle_close)

    # ══════════════════════════════════════════════════════════════
    # Shared live exit
    # ══════════════════════════════════════════════════════════════

    def _exit_live(
        self,
        side: str,
        reason: str,
        exit_price_hint: Optional[float] = None,
    ):
        trade = self._live.get(side)
        if not trade:
            return

        symbol   = trade.symbol
        qty      = trade.qty
        trade_id = trade.trade_id

        # Fetch fresh LTP via REST for the limit sell price.
        ltp = None
        try:
            data_kite = self.executor.broker_manager.get_data_kite()
            if data_kite:
                q = data_kite.ltp(f"NFO:{symbol}")
                rest_ltp = q.get(f"NFO:{symbol}", {}).get("last_price")
                if rest_ltp and rest_ltp > 0:
                    ltp = rest_ltp
        except Exception as e:
            write_audit_log(f"[HA][LIVE][EXIT_LTP_REST_FAIL] {e}")

        if not ltp or ltp <= 0:
            ltp = exit_price_hint or LTPStore.get(symbol) or trade.entry_price

        if reason == "TP":
            limit_price = round(round(ltp * 0.997 / 0.05) * 0.05, 2)
        else:
            limit_price = round(round(ltp * 0.97 / 0.05) * 0.05, 2)

        exit_order_id = None
        try:
            kite = self.executor.broker_manager.get_trade_kite()
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
            exit_order_id = self.executor._relay_call(
                relay_fn=lambda r: r.place_order(**order_params),
                direct_fn=lambda: kite.place_order(**order_params),
                op_name=f"HA_SELL_{reason}",
                symbol=symbol,
            )
            write_audit_log(
                f"[HA][LIVE][SELL_PLACED] {symbol} "
                f"reason={reason} limit={limit_price:.2f} order={exit_order_id}"
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][EXIT_SELL_FAIL] {symbol} ERR={repr(e)}")
            # Don't clear state — will retry on next tick / candle
            return

        # Get actual fill price (wait up to 60s)
        exit_price = ltp
        deadline = time.time() + 60
        while time.time() < deadline:
            ap = self.executor.get_last_avg_price(exit_order_id)
            if ap and ap > 0:
                exit_price = ap
                break
            time.sleep(1)

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=reason,
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_CLOSE_FAIL] {e}")

        del self._live[side]
        self.signal_engine.notify_exit(side)

        write_audit_log(
            f"[HA][LIVE][EXIT_OK] {symbol} side={side} "
            f"reason={reason} fill={exit_price:.2f}"
        )

        try:
            pnl = (exit_price - trade.entry_price) * qty
            payload = {
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "entry_price": trade.entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
            }
            if reason == "SL":
                notify_sl_exit(payload)
            elif reason in ("TP", "EOD_SQUARE_OFF"):
                notify_tp_exit(payload)
            else:
                payload["exit_reason"] = reason
                notify_manual_exit(payload)
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][EXIT_FAIL] {e}")

    # ── EOD square-off ────────────────────────────────────────────

    def eod_squareoff(self):
        """End-of-day square-off. Routes by what is actually open."""
        mode = self._mode()

        if mode == "PAPER" or (mode == "OFF" and not self._live):
            for side in ("CE", "PE"):
                self.signal_engine.notify_exit(side)
            write_audit_log(f"[HA][{mode}][EOD] Signal flags cleared")
            return

        for side in list(self._live.keys()):
            write_audit_log(f"[HA][LIVE][EOD] Closing {side}")
            try:
                self._exit_live(side, "EOD_SQUARE_OFF")
            except Exception as e:
                write_audit_log(
                    f"[HA][LIVE][EOD_FAIL] side={side} ERR={repr(e)}"
                )