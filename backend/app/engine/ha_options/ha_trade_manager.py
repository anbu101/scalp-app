# backend/app/engine/ha_options/ha_trade_manager.py
"""
HA Trade Manager
================
Handles all trade lifecycle operations for HA_V1:
  - Entry (LIVE via limit order + GTT OCO, PAPER via PaperTradeRecorder)
  - SL monitoring (close-based, triggered by ha_tick_engine)
  - EOD square-off delegation

Mirrors bb_trade_manager.py patterns exactly so the rest of the
system (DB, Telegram, GTT monitor) works without modification.
"""

import time
import uuid
from datetime import datetime
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore
from app.config.strategy_loader import load_strategy_config
from app.db.trades_repo import insert_trade, close_trade
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.db.paper_trades_repo import get_open_paper_trades_by_side
from app.engine.ha_options.ha_signal_engine import HASignalEngine

# Telegram
from app.api.telegram_api import (
    notify_trade_entry,
    notify_manual_exit,
)


class HATradeManager:

    def __init__(
        self,
        strategy_id: str,
        trade_mode: str,
        executor,
        signal_engine: HASignalEngine,
        config: dict,
    ):
        self.strategy_id      = strategy_id
        self._startup_mode    = trade_mode
        self.executor         = executor
        self.signal_engine    = signal_engine
        self.config           = config

        # Fallbacks from constructor config
        self._tp_rr_fallback  = float(config.get("risk_reward_ratio", 2.0))
        self._lot_count_fallback = int(config.get("quantity", {}).get("lots", 1))
        self._lot_size_fallback  = int(config.get("quantity", {}).get("lot_size", 65))

        # LIVE state per side (simple dict — one trade per side max)
        # Keyed by "CE" or "PE"
        # Value: { trade_id, symbol, qty, sl_price, tp_price, gtt_id }
        self._live_trades: dict = {}

    # ────────────────────────────────────────────────────────────
    # Live config helpers
    # ────────────────────────────────────────────────────────────

    def _live_mode(self) -> str:
        try:
            mode = load_strategy_config(self.strategy_id).get(
                "trade_execution_mode", self._startup_mode
            )
            if self._startup_mode == "PAPER" and mode == "LIVE":
                return "PAPER"
            return mode
        except Exception:
            return self._startup_mode

    def _live_rr(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "risk_reward_ratio", self._tp_rr_fallback
                ) or self._tp_rr_fallback
            )
        except Exception:
            return self._tp_rr_fallback

    def _live_lot_count(self) -> int:
        try:
            return int(
                load_strategy_config(self.strategy_id)
                .get("quantity", {})
                .get("lots", self._lot_count_fallback)
                or self._lot_count_fallback
            )
        except Exception:
            return self._lot_count_fallback

    def _live_lot_size(self) -> int:
        try:
            return int(
                load_strategy_config(self.strategy_id)
                .get("quantity", {})
                .get("lot_size", self._lot_size_fallback)
                or self._lot_size_fallback
            )
        except Exception:
            return self._lot_size_fallback

    # ────────────────────────────────────────────────────────────
    # ENTRY
    # ────────────────────────────────────────────────────────────

    def enter(
        self,
        symbol: str,
        side: str,
        entry_ltp: float,
        sl_price: float,
    ) -> bool:
        """
        Place entry order.  Returns True on confirmed entry, False on abort.
        """
        mode     = self._live_mode()
        rr       = self._live_rr()
        lot_cnt  = self._live_lot_count()
        lot_size = self._live_lot_size()
        qty      = lot_cnt * lot_size

        # TP = entry + (entry - SL) * RR
        risk     = abs(entry_ltp - sl_price)
        if risk <= 0:
            write_audit_log(f"[HA][ENTRY_ABORT] {symbol} zero risk — SL==entry")
            return False

        tp_price = entry_ltp + risk * rr
        # For PE the direction is reversed
        if side == "PE":
            tp_price = entry_ltp - risk * rr

        write_audit_log(
            f"[HA][ENTRY] {symbol} side={side} mode={mode} "
            f"entry={entry_ltp:.2f} sl={sl_price:.2f} tp={tp_price:.2f} "
            f"rr={rr} qty={qty}"
        )

        # ── PAPER ────────────────────────────────────────────────
        if mode == "PAPER":
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
                write_audit_log(f"[HA][PAPER][ENTRY_BLOCKED] {symbol}")
                return False
            write_audit_log(f"[HA][PAPER][ENTRY_OK] {symbol} id={paper_id}")
            return True

        # ── LIVE ─────────────────────────────────────────────────
        if side in self._live_trades:
            write_audit_log(f"[HA][LIVE][SKIP] {side} already in trade")
            return False

        try:
            order_id, avg_price, filled_qty = self.executor.place_buy(
                symbol=symbol, token=0, qty=qty
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][BUY_FAIL] {symbol} ERR={repr(e)}")
            return False

        if filled_qty <= 0:
            write_audit_log(f"[HA][LIVE][NO_FILL] {symbol}")
            return False

        # Best-effort avg price resolution
        if avg_price <= 0:
            start = time.time()
            while avg_price <= 0 and time.time() - start < 120:
                avg_price = self.executor.get_last_avg_price(order_id)
                if avg_price > 0:
                    break
                time.sleep(1)

        if avg_price <= 0:
            avg_price = LTPStore.get(symbol) or entry_ltp

        # Recalculate SL/TP from actual fill
        risk      = abs(avg_price - sl_price)
        tp_price  = avg_price + risk * rr if side == "CE" else avg_price - risk * rr

        # GTT OCO
        gtt_id = None
        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=filled_qty,
                sl_price=sl_price,
                tp_price=tp_price,
                last_price=avg_price,
            )
        except Exception as gtt_err:
            write_audit_log(
                f"[HA][LIVE][GTT_FAIL] {symbol} ERR={repr(gtt_err)} — "
                f"trade open without GTT protection"
            )

        # DB insert
        trade_id = str(uuid.uuid4())
        try:
            insert_trade(
                trade_id=trade_id,
                strategy_id=self.strategy_id,
                slot=side,
                symbol=symbol,
                token=0,
                entry_price=avg_price,
                qty=filled_qty,
                buy_order_id=order_id,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_mode="GTT",
            )
        except Exception as db_err:
            write_audit_log(f"[HA][LIVE][DB_INSERT_FAIL] {db_err}")

        # In-memory live state
        self._live_trades[side] = {
            "trade_id": trade_id,
            "symbol":   symbol,
            "qty":      filled_qty,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "gtt_id":   gtt_id,
            "entry_price": avg_price,
        }

        write_audit_log(
            f"[HA][LIVE][ENTRY_OK] {symbol} side={side} "
            f"fill={avg_price:.2f} sl={sl_price:.2f} tp={tp_price:.2f} "
            f"gtt={gtt_id}"
        )

        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode":        "live",
                "symbol":      symbol,
                "side":        side,
                "entry_price": avg_price,
                "quantity":    filled_qty,
                "sl":          sl_price,
                "tp":          tp_price,
            })
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][ENTRY_FAIL] {e}")

        return True

    # ────────────────────────────────────────────────────────────
    # SL CHECK (close-based)
    # ────────────────────────────────────────────────────────────

    def check_sl_on_candle_close(self, symbol: str, close_price: float):
        """
        Called by ha_tick_engine after every completed 1m candle.
        SL is evaluated against close price only — NOT intra-candle low.
        """
        side = "CE" if symbol.endswith("CE") else "PE"
        mode = self._live_mode()

        if mode == "PAPER":
            self._check_sl_paper(symbol, side, close_price)
        else:
            self._check_sl_live(symbol, side, close_price)

    def _check_sl_paper(self, symbol: str, side: str, close_price: float):
        open_trades = get_open_paper_trades_by_side(
            strategy_name=self.strategy_id,
            side=side,
        )
        for t in open_trades:
            if t["symbol"] != symbol:
                continue
            sl = t.get("sl_price") or 0
            tp = t.get("tp_price") or 0

            if sl > 0 and close_price <= sl:
                write_audit_log(
                    f"[HA][PAPER][SL_HIT] {symbol} close={close_price} sl={sl}"
                )
                PaperTradeRecorder.force_exit(
                    paper_trade_id=t["paper_trade_id"],
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="SL",
                )
                self.signal_engine.notify_exit(side)

            elif tp > 0 and close_price >= tp:
                write_audit_log(
                    f"[HA][PAPER][TP_HIT] {symbol} close={close_price} tp={tp}"
                )
                PaperTradeRecorder.force_exit(
                    paper_trade_id=t["paper_trade_id"],
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="TP",
                )
                self.signal_engine.notify_exit(side)

    def _check_sl_live(self, symbol: str, side: str, close_price: float):
        trade = self._live_trades.get(side)
        if not trade or trade["symbol"] != symbol:
            return

        sl = trade.get("sl_price", 0)

        # LIVE SL is managed by GTT — we only act here if GTT was not placed
        if trade.get("gtt_id"):
            return  # GTT is protecting; don't double-exit

        if sl > 0 and close_price <= sl:
            write_audit_log(
                f"[HA][LIVE][SL_HIT_NO_GTT] {symbol} close={close_price} sl={sl}"
            )
            self._exit_live(side, "SL")

    # ────────────────────────────────────────────────────────────
    # LIVE EXIT
    # ────────────────────────────────────────────────────────────

    def _exit_live(self, side: str, reason: str):
        trade = self._live_trades.get(side)
        if not trade:
            return

        symbol   = trade["symbol"]
        qty      = trade["qty"]
        trade_id = trade["trade_id"]
        gtt_id   = trade.get("gtt_id")

        # Cancel GTT to avoid race
        if gtt_id:
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(f"[HA][LIVE][GTT_CANCEL_WARN] {e}")

        # Market sell
        exit_order_id = None
        try:
            exit_order_id = self.executor.place_market_sell(symbol=symbol, qty=qty)
        except Exception as e:
            write_audit_log(f"[HA][LIVE][EXIT_FAIL] {symbol} ERR={repr(e)}")
            return

        exit_price = LTPStore.get(symbol)

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=reason,
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_CLOSE_FAIL] {e}")

        del self._live_trades[side]
        self.signal_engine.notify_exit(side)

        write_audit_log(
            f"[HA][LIVE][EXIT_OK] {symbol} side={side} "
            f"reason={reason} price={exit_price}"
        )

        try:
            notify_manual_exit({
                "strategy_id": self.strategy_id,
                "mode":        "live",
                "symbol":      symbol,
                "entry_price": trade.get("entry_price"),
                "exit_price":  exit_price,
                "exit_reason": reason,
                "pnl": (
                    (exit_price - trade["entry_price"]) * qty
                    if exit_price and trade.get("entry_price") else None
                ),
            })
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][EXIT_FAIL] {e}")

    # ────────────────────────────────────────────────────────────
    # EOD SQUARE-OFF
    # ────────────────────────────────────────────────────────────

    def eod_squareoff(self):
        mode = self._live_mode()

        if mode == "PAPER":
            # Paper EOD is handled globally by paper_trade_eod_job
            for side in ("CE", "PE"):
                self.signal_engine.notify_exit(side)
            write_audit_log(f"[HA][PAPER][EOD] Signal flags cleared")
            return

        for side in list(self._live_trades.keys()):
            write_audit_log(f"[HA][LIVE][EOD] Closing {side}")
            try:
                self._exit_live(side, "EOD_SQUARE_OFF")
            except Exception as e:
                write_audit_log(f"[HA][LIVE][EOD_FAIL] side={side} ERR={repr(e)}")