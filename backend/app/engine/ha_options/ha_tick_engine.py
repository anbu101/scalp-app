# backend/app/engine/ha_options/ha_tick_engine.py
"""
HA Options Tick Engine
======================
Receives live ticks forwarded from ZerodhaTickEngine,
builds 1-minute HA candles for each subscribed option symbol,
and routes entry/exit signals through HASignalEngine.

Architecture note
-----------------
Zerodha permits only ONE KiteTicker per session.
ZerodhaTickEngine owns that connection and forwards ticks to every
registered engine in BB_ENGINE_REGISTRY (and HA_ENGINE_REGISTRY).
This engine registers itself in HA_ENGINE_REGISTRY at startup so
ZerodhaTickEngine can call on_tick() for each subscribed token.

Option subscriptions:
  - Exactly 1 CE + 1 PE from the current weekly NIFTY selection.
  - Selections are loaded from the state files written by selection_engine.
  - The engine re-reads them every SELECTION_RELOAD_INTERVAL candles
    so UI option changes take effect without a restart.
"""

import time
import threading
from collections import defaultdict
from typing import Dict, Optional, Set
from datetime import datetime

from app.indicators.heikin_ashi import HeikinAshiConverter, HACandle
from app.indicators.ema import EMA
from app.engine.ha_options.ha_signal_engine import HASignalEngine, HATradeSignal
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ws_registry import get_ws_engines
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

TIMEFRAME_SEC           = 60      # 1-minute candles
EMA_PERIOD              = 20      # EMA20_Low
SELECTION_RELOAD_INTERVAL = 5     # re-check selection every N candles


# ──────────────────────────────────────────────────────────────────
# Per-symbol state
# ──────────────────────────────────────────────────────────────────

class SymbolState:
    """Holds all per-symbol mutable state."""

    def __init__(self, symbol: str, token: int):
        self.symbol = symbol
        self.token  = token

        # Tick accumulation for current bucket
        self.bucket_start: Optional[int] = None
        self._o: Optional[float] = None
        self._h: Optional[float] = None
        self._l: Optional[float] = None
        self._c: Optional[float] = None

        # Heikin Ashi converter (stateful)
        self.ha_converter = HeikinAshiConverter()

        # EMA20 of HA lows
        self.ema_low = EMA(EMA_PERIOD)
        self.ema_low_value: Optional[float] = None

        # Most recent completed HA candle (for SL reference)
        self.last_ha: Optional[HACandle] = None

        # Last red HA candle low (used as SL for CE entries)
        self.last_red_ha_low: Optional[float] = None
        # Last green HA candle high (used as SL for PE entries)
        self.last_green_ha_high: Optional[float] = None

    def on_tick(self, ltp: float, ts: int) -> Optional[HACandle]:
        """
        Feed one live tick. Returns a completed HACandle when the
        1-minute bucket rolls over, otherwise None.
        """
        bucket_start = (ts // TIMEFRAME_SEC) * TIMEFRAME_SEC

        if self.bucket_start is None:
            # First tick ever
            self.bucket_start = bucket_start
            self._o = self._h = self._l = self._c = ltp
            return None

        if bucket_start == self.bucket_start:
            # Same bucket — update OHLC
            if ltp > self._h:
                self._h = ltp
            if ltp < self._l:
                self._l = ltp
            self._c = ltp
            return None

        # ── Bucket rollover ──────────────────────────────────────
        # Build raw OHLC candle, convert to HA
        raw_o = self._o
        raw_h = self._h
        raw_l = self._l
        raw_c = self._c

        ha = self.ha_converter.update(
            ts=self.bucket_start,
            o=raw_o, h=raw_h, l=raw_l, c=raw_c,
        )

        # Update EMA20 of HA lows
        ema_val = self.ema_low.update(ha.low)
        self.ema_low_value = ema_val

        # Track last red/green HA candle for SL reference
        if ha.is_red:
            self.last_red_ha_low = ha.low
        else:
            self.last_green_ha_high = ha.high

        self.last_ha = ha

        # Start new bucket
        self.bucket_start = bucket_start
        self._o = self._h = self._l = self._c = ltp

        return ha


# ──────────────────────────────────────────────────────────────────
# Tick engine
# ──────────────────────────────────────────────────────────────────

class HAOptionsTickEngine:

    STRATEGY_ID = "HA_V1"

    def __init__(self, executor, config: dict, trade_mode: str):
        self.executor    = executor
        self.config      = config
        self.trade_mode  = trade_mode

        # symbol → SymbolState
        self._states: Dict[str, SymbolState] = {}
        # token → symbol (for fast lookup in on_tick)
        self._token_map: Dict[int, str] = {}

        # Tracks which tokens we have asked WS to subscribe
        self._subscribed_tokens: Set[int] = set()

        # Candle counter — used for periodic selection reload
        self._candle_count: int = 0

        # Signal engine (shared across CE and PE)
        self._signal_engine = HASignalEngine(
            max_trades_per_side=config.get("max_trades_per_side", 10)
        )

        # Trade manager (created here, import deferred to avoid circular)
        from app.engine.ha_options.ha_trade_manager import HATradeManager

        self._trade_manager = HATradeManager(
            strategy_id=self.STRATEGY_ID,
            trade_mode=trade_mode,
            executor=executor,
            signal_engine=self._signal_engine,
            config=config,
        )

        # Register in global registry so ZerodhaTickEngine forwards ticks
        if not any(isinstance(e, HAOptionsTickEngine) for e in HA_ENGINE_REGISTRY):
            HA_ENGINE_REGISTRY.append(self)

        # Load initial selection + subscribe tokens
        self._reload_selection()

        write_audit_log(
            f"[HA][ENGINE_READY] mode={trade_mode} "
            f"tokens={list(self._token_map.keys())}"
        )

    # ── Public start ─────────────────────────────────────────────

    def start(self):
        """Start any background threads (SL monitor)."""
        threading.Thread(
            target=self._sl_monitor_loop,
            daemon=True,
            name="ha-sl-monitor",
        ).start()
        write_audit_log("[HA] SL monitor thread started")

    # ── WS reconnect notification ─────────────────────────────────

    def on_ws_reconnect(self):
        """Called by ZerodhaTickEngine._on_connect on every WS reconnect."""
        # Re-subscribe our tokens after reconnect
        self._subscribe_tokens(list(self._token_map.keys()))
        write_audit_log("[HA][WS_RECONNECT] Re-subscribed tokens")

    # ── Tick entry point (called by ZerodhaTickEngine) ────────────

    def on_tick(self, token: int, ltp: float, ts: int):
        symbol = self._token_map.get(token)
        if symbol is None:
            return

        LTPStore.update(symbol, ltp)

        state = self._states.get(symbol)
        if state is None:
            return

        ha_candle = state.on_tick(ltp, ts)
        if ha_candle is None:
            return  # still building current bucket

        self._process_ha_candle(symbol, ha_candle, state)

    # ── Process completed HA candle ───────────────────────────────

    def _process_ha_candle(self, symbol: str, ha: HACandle, state: SymbolState):
        self._candle_count += 1

        # Periodic selection reload
        if self._candle_count % SELECTION_RELOAD_INTERVAL == 0:
            self._reload_selection()

        ema_val = state.ema_low_value

        # Determine side from symbol
        if symbol.endswith("CE"):
            side = "CE"
        elif symbol.endswith("PE"):
            side = "PE"
        else:
            return

        write_audit_log(
            f"[HA][CANDLE] {symbol} "
            f"O={ha.open} H={ha.high} L={ha.low} C={ha.close} "
            f"{'GREEN' if ha.is_green else 'RED'} "
            f"EMA20L={ema_val:.2f if ema_val else 'N/A'}"
        )

        # Only evaluate signal for the correct side
        # (signal engine tracks both, but we want per-symbol granularity)
        if ema_val is None:
            write_audit_log(f"[HA][SKIP] {symbol} EMA not ready")
            return

        # ── Session gate ─────────────────────────────────────────
        cfg          = load_strategy_config(self.STRATEGY_ID)
        session_start = cfg.get("session", {}).get("primary", {}).get("start", "09:15")
        session_end   = cfg.get("session", {}).get("primary", {}).get("end",   "15:20")
        now_str       = datetime.now().strftime("%H:%M")
        if now_str < session_start or now_str >= session_end:
            return

        # ── Global trade_on gate ─────────────────────────────────
        if not load_global_config().get("trade_on", False):
            return

        signal: HATradeSignal = self._signal_engine.update(ha, ema_val)

        write_audit_log(
            f"[HA][SIGNAL] {symbol} action={signal.action} "
            f"reason={signal.reason} reject={signal.rejection_reason}"
        )

        if signal.action in (f"ENTER_{side}",):
            # SL = most recent red HA candle low (for CE)
            #      most recent green HA candle high (for PE)
            if side == "CE":
                sl_price = state.last_red_ha_low
            else:
                sl_price = state.last_green_ha_high

            if not sl_price:
                write_audit_log(f"[HA][SKIP] {symbol} no SL reference candle yet")
                self._signal_engine.notify_exit(side)
                return

            ltp = LTPStore.get(symbol)
            if not ltp or ltp <= 0:
                write_audit_log(f"[HA][SKIP] {symbol} LTP unavailable")
                self._signal_engine.notify_exit(side)
                return

            # Confirm entry in signal engine first
            self._signal_engine.confirm_entry(side)

            success = self._trade_manager.enter(
                symbol=symbol,
                side=side,
                entry_ltp=ltp,
                sl_price=sl_price,
            )

            if not success:
                self._signal_engine.notify_exit(side)

    # ── SL monitor (close-based, runs every 5s) ───────────────────

    def _sl_monitor_loop(self):
        """
        Checks SL against the latest closed candle close price.
        Runs every 5 seconds but only acts when a new candle has closed
        (tracked by last_ha timestamp change).

        This is a safety net for PAPER mode — LIVE mode relies on GTT.
        """
        last_checked: Dict[str, int] = {}   # symbol → last ha.ts checked

        while True:
            time.sleep(5)
            try:
                for symbol, state in list(self._states.items()):
                    ha = state.last_ha
                    if ha is None:
                        continue
                    if last_checked.get(symbol) == ha.ts:
                        continue  # already checked this candle

                    last_checked[symbol] = ha.ts
                    self._trade_manager.check_sl_on_candle_close(symbol, ha.close)

            except Exception as e:
                write_audit_log(f"[HA][SL_MONITOR_ERROR] {e}")

    # ── Selection management ──────────────────────────────────────

    def _reload_selection(self):
        """
        Load the persisted NIFTY CE/PE selection (written by selection_engine)
        and subscribe/update tokens as needed.
        
        HA_V1 uses exactly 1 CE and 1 PE (first of each from the saved list).
        """
        from pathlib import Path
        import json

        state_dir = Path.home() / ".scalp-app" / "state"
        ce_file   = state_dir / "SCALP_V1_selected_ce.json"
        pe_file   = state_dir / "SCALP_V1_selected_pe.json"

        new_symbols: Dict[int, str] = {}

        for fpath, label in [(ce_file, "CE"), (pe_file, "PE")]:
            if not fpath.exists():
                continue
            try:
                rows = json.loads(fpath.read_text())
                if not rows:
                    continue
                # Take only the FIRST option for HA (1 CE, 1 PE)
                row    = rows[0]
                symbol = row.get("symbol") or row.get("tradingsymbol")
                token  = row.get("instrument_token") or row.get("token")
                if symbol and token:
                    new_symbols[int(token)] = symbol
            except Exception as e:
                write_audit_log(f"[HA] Selection reload error ({label}): {e}")

        if not new_symbols:
            write_audit_log("[HA] No selection available yet — waiting")
            return

        # Add new symbols / avoid removing symbols with open trades
        for token, symbol in new_symbols.items():
            if token not in self._token_map:
                self._token_map[token] = symbol
                if symbol not in self._states:
                    self._states[symbol] = SymbolState(symbol=symbol, token=token)
                    write_audit_log(f"[HA] Tracking new symbol: {symbol} token={token}")

        # Subscribe any unsubscribed tokens
        to_sub = [t for t in new_symbols if t not in self._subscribed_tokens]
        if to_sub:
            self._subscribe_tokens(to_sub)

    def _subscribe_tokens(self, tokens: list):
        if not tokens:
            return
        engines = get_ws_engines()
        if not engines:
            write_audit_log("[HA] WS engine not ready — deferring subscription")
            return
        try:
            engines[0].subscribe_additional_tokens(tokens)
            for t in tokens:
                self._subscribed_tokens.add(t)
            write_audit_log(f"[HA] Subscribed tokens: {tokens}")
        except Exception as e:
            write_audit_log(f"[HA] Token subscription error: {e}")

    # ── EOD square-off ────────────────────────────────────────────

    def eod_squareoff(self):
        try:
            self._trade_manager.eod_squareoff()
        except Exception as e:
            write_audit_log(f"[HA][EOD][ERROR] {repr(e)}")