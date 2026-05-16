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

LIVE ORDER DESIGN:
  Entry  → protected limit BUY at LTP × 1.03 (3% buffer, see note below)
  TP     → NO GTT placed.  Tick-level monitor handles TP via limit SELL.
  SL     → NO GTT placed.  Candle-close monitor calls check_sl_on_close()
           which places a limit SELL when close <= sl_price.

  Why 3% limit buffer for entry?
    1-minute HA options can move 2-3% in a single tick at open.
    A 1% cap rejects too many fills.  3% satisfies SEBI market-protection
    while maximising fill probability.

  Why no GTT for anything?
    TP GTT triggers on any intra-candle tick — correct for TP.
    SL GTT also triggers on any tick — WRONG for SL (we need close-based).
    To avoid having two separate GTT paths with different semantics, we
    handle BOTH exits programmatically:
      TP → tick-level check  (fast, no GTT latency)
      SL → candle-close check (correct semantics)
"""

import time
import uuid
from datetime import datetime
from typing import Optional, Dict

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore
from app.config.strategy_loader import load_strategy_config
from app.db.trades_repo import insert_trade, close_trade
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.db.paper_trades_repo import (
    get_open_paper_trades_by_side,
)
from app.engine.ha_options.ha_signal_engine import HASignalEngine

from app.api.telegram_api import (
    notify_trade_entry,
    notify_sl_exit,
    notify_tp_exit,
    notify_manual_exit,
)


# ──────────────────────────────────────────────────────────────────
# Live trade record
# ──────────────────────────────────────────────────────────────────

class _LiveTrade:
    __slots__ = (
        "trade_id", "symbol", "side", "qty",
        "entry_price", "sl_price", "tp_price",
    )

    def __init__(self, trade_id, symbol, side, qty,
                 entry_price, sl_price, tp_price):
        self.trade_id    = trade_id
        self.symbol      = symbol
        self.side        = side
        self.qty         = qty
        self.entry_price = entry_price
        self.sl_price    = sl_price
        self.tp_price    = tp_price


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
    ):
        self.strategy_id   = strategy_id
        self._startup_mode = trade_mode
        self.executor      = executor
        self.signal_engine = signal_engine
        self.config        = config

        # Fallbacks (constructor-time values)
        self._rr_fallback       = float(config.get("risk_reward_ratio", 2.0))
        self._lot_fallback      = int(config.get("quantity", {}).get("lots", 1))
        self._lot_size_fallback = int(config.get("quantity", {}).get("lot_size", 65))

        # Live trade state — keyed by side ("CE" / "PE")
        self._live: Dict[str, _LiveTrade] = {}

        # Guard: prevents double-exit when TP fires from two rapid ticks
        # before the first exit finishes placing the sell order.
        # Set to True the moment _exit_* begins; cleared after order placed.
        self._tp_exit_in_progress: set = set()   # set of sides currently exiting

    # ── Live config readers ───────────────────────────────────────

    def _mode(self) -> str:
        try:
            m = load_strategy_config(self.strategy_id).get(
                "trade_execution_mode", self._startup_mode
            )
            # Can only downgrade LIVE→PAPER mid-session, not upgrade
            if self._startup_mode == "PAPER" and m == "LIVE":
                return "PAPER"
            return m
        except Exception:
            return self._startup_mode

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

    # ── ENTRY ─────────────────────────────────────────────────────

    def enter(self, symbol: str, side: str, entry_ltp: float, sl_price: float) -> bool:
        """
        Place entry order.  Returns True on confirmed entry.

        TP = entry_ltp + (entry_ltp - sl_price) × RR
        """
        mode = self._mode()
        rr   = self._rr()
        qty  = self._qty()

        risk = entry_ltp - sl_price
        if risk <= 0:
            write_audit_log(
                f"[HA][ENTRY_ABORT] {symbol} invalid risk "
                f"entry={entry_ltp:.2f} sl={sl_price:.2f}"
            )
            return False

        tp_price = entry_ltp + risk * rr

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

    # ── LIVE ENTRY ────────────────────────────────────────────────

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

        # Wait for fill (up to 120s, poll every 1s)
        avg_price = 0.0
        deadline  = time.time() + 120
        while time.time() < deadline:
            avg_price = self.executor.get_last_avg_price(order_id)
            if avg_price and avg_price > 0:
                break
            time.sleep(1)

        # Fallback if polling didn't resolve
        if avg_price <= 0:
            avg_price = LTPStore.get(symbol) or entry_ltp
            write_audit_log(
                f"[HA][LIVE][AVG_FALLBACK] {symbol} using ltp={avg_price:.2f}"
            )

        # Recalculate SL/TP from actual fill price
        risk     = avg_price - sl_price
        if risk <= 0:
            risk = entry_ltp - sl_price
        tp_price = avg_price + risk * self._rr()

        # DB insert (no GTT — exits are programmatic)
        trade_id = str(uuid.uuid4())
        try:
            insert_trade(
                trade_id=trade_id,
                strategy_id=self.strategy_id,
                slot=side,
                symbol=symbol,
                token=0,
                entry_price=avg_price,
                qty=qty,
                buy_order_id=order_id,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_mode="GTT",
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_FAIL] {e}")

        # In-memory state
        self._live[side] = _LiveTrade(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=avg_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )

        write_audit_log(
            f"[HA][LIVE][ENTRY_OK] {symbol} side={side} "
            f"fill={avg_price:.2f} sl={sl_price:.2f} tp={tp_price:.2f}"
        )

        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "side": side,
                "entry_price": avg_price,
                "quantity": qty,
                "sl": sl_price,
                "tp": tp_price,
            })
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][ENTRY_FAIL] {e}")

        return True

    # ══════════════════════════════════════════════════════════════
    # TP  —  checked on EVERY TICK (live price)
    # ══════════════════════════════════════════════════════════════

    def check_tp_on_tick(self, symbol: str, ltp: float):
        """
        Called by ha_tick_engine on EVERY incoming tick for this symbol.

        Exits the trade immediately when ltp >= tp_price.
        This is the SOLE TP exit path for both PAPER and LIVE.

        SL is intentionally NOT checked here — it is checked on candle
        close only (see check_sl_on_close).
        """
        mode = self._mode()
        side = "CE" if symbol.endswith("CE") else "PE"

        if mode == "PAPER":
            self._check_paper_tp_tick(symbol, side, ltp)
        else:
            self._check_live_tp_tick(symbol, side, ltp)

    # ── Paper TP on tick ──────────────────────────────────────────

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
                # Only one open trade per side — stop after first hit
                break

    # ── Live TP on tick ───────────────────────────────────────────

    def _check_live_tp_tick(self, symbol: str, side: str, ltp: float):
        trade = self._live.get(side)
        if not trade or trade.symbol != symbol:
            return

        # Guard: prevent concurrent double-exit
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
        TP is intentionally NOT checked here — it fires on every tick.

        This is the SOLE SL exit mechanism for both PAPER and LIVE.
        """
        mode = self._mode()
        side = "CE" if symbol.endswith("CE") else "PE"

        if mode == "PAPER":
            self._check_paper_sl_close(symbol, side, candle_close)
        else:
            self._check_live_sl_close(symbol, side, candle_close)

    # ── Paper SL on close ─────────────────────────────────────────

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

    # ── Live SL on close ──────────────────────────────────────────

    def _check_live_sl_close(self, symbol: str, side: str, candle_close: float):
        trade = self._live.get(side)
        if not trade or trade.symbol != symbol:
            return

        # Skip if a TP exit is already in flight for this side
        if side in self._tp_exit_in_progress:
            return

        if HASignalEngine.sl_hit(candle_close, trade.sl_price):
            write_audit_log(
                f"[HA][LIVE][SL_CLOSE] {symbol} "
                f"close={candle_close:.2f} sl={trade.sl_price:.2f}"
            )
            self._exit_live(side, "SL", exit_price_hint=candle_close)

    # ══════════════════════════════════════════════════════════════
    # Legacy shim — kept so any existing call sites don't break
    # ══════════════════════════════════════════════════════════════

    def check_sl_tp_on_close(self, symbol: str, candle_close: float):
        """
        DEPRECATED shim.  Prefer check_sl_on_close() + check_tp_on_tick().
        Only SL is checked here now.
        """
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
        # Never use a stale WS price for exits.
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

        # Fallback chain: hint → LTPStore → entry_price
        if not ltp or ltp <= 0:
            ltp = exit_price_hint or LTPStore.get(symbol) or trade.entry_price

        # For TP exits use a limit slightly BELOW current price to guarantee fill.
        # For SL exits (price is dropping) use a bigger discount to ensure fill.
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

        # DB close
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=reason,
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_CLOSE_FAIL] {e}")

        # Clear in-memory state AFTER DB write
        del self._live[side]
        self.signal_engine.notify_exit(side)

        write_audit_log(
            f"[HA][LIVE][EXIT_OK] {symbol} side={side} "
            f"reason={reason} fill={exit_price:.2f}"
        )

        # Telegram
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
        mode = self._mode()

        if mode == "PAPER":
            for side in ("CE", "PE"):
                self.signal_engine.notify_exit(side)
            write_audit_log(f"[HA][PAPER][EOD] Signal flags cleared")
            return

        for side in list(self._live.keys()):
            write_audit_log(f"[HA][LIVE][EOD] Closing {side}")
            try:
                self._exit_live(side, "EOD_SQUARE_OFF")
            except Exception as e:
                write_audit_log(
                    f"[HA][LIVE][EOD_FAIL] side={side} ERR={repr(e)}"
                )