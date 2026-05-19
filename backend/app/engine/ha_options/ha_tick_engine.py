# backend/app/engine/ha_options/ha_tick_engine.py
"""
HA Options Tick Engine
======================

ARCHITECTURE (mirrors SCALP_V1):
  - ZerodhaTickEngine already subscribes the full NIFTY weekly universe
    (~40+ CE/PE strikes) and forwards every tick to HA_ENGINE_REGISTRY
    via on_tick(token, ltp, ts).

  - This engine builds HA candles and stores them in ha_candles for ALL
    option tokens in that universe — not just the 2 currently selected.

  - At candle close, signal evaluation only fires for the symbol that is
    currently selected (read live from SCALP_V1 selection files).

  WHY THIS MATTERS:
    Selection files change every 2 minutes (new strike chosen). If we only
    track the selected symbol, the new strike has zero HA history and needs
    20 candles (~20 min) for EMA to converge. With universe-wide storage
    every strike already has full history when selected.

FLOW:
  1. _reload_universe()        — discovers all option tokens from
                                  ZerodhaTickEngine.builders (already subscribed)
  2. on_tick()                 — builds 1-min OHLC bucket per symbol
  3. _on_candle_close()        — ALWAYS writes ha_candles row for every symbol
                                  ONLY evaluates signal for selected CE/PE
  4. _reload_selection()       — updates _selected_ce / _selected_pe from files
  5. _subscription_retry_loop — runs every 30s, calls both reload methods

EXIT DESIGN:
  TP → checked on EVERY TICK via check_tp_on_tick() in on_tick().
       Fires immediately when ltp >= tp_price.
       Checked for BOTH the currently selected symbol AND any symbol
       that has an active open trade (_active_trade_symbols).

  SL → checked on CANDLE CLOSE only via check_sl_on_close() in
       _sl_monitor_loop(). Only a candle that CLOSES below SL triggers
       an exit — intra-candle wicks are ignored.
       Also checked for BOTH selected and active-trade symbols.

  CRITICAL: Selection rotates every ~2 minutes (new strike chosen).
  The open trade may be on an OLDER strike that is no longer selected.
  _active_trade_symbols tracks ALL symbols with open trades so that
  TP/SL monitoring never stops even after the selection changes.
"""

import time
import threading
from typing import Dict, Optional, Set
from datetime import datetime

from app.indicators.heikin_ashi import HeikinAshiConverter, HACandle
from app.indicators.ema import EMA
from app.engine.ha_options.ha_signal_engine import (
    HASignalEngine,
    HAConditionEvaluator,
    HAEntrySignal,
)
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ws_registry import get_ws_engines
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY
from app.db.ha_candles_repo import (
    init_table,
    insert_ha_candle,
    fetch_recent_ha_candles,
)


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

TIMEFRAME_SEC          = 60    # 1-minute HA candles
EMA_PERIOD             = 20    # EMA(20) of HA Low — matches TradingView
WARMUP_CANDLES         = 100   # DB rows to replay on restart per symbol
SUBSCRIPTION_RETRY_SEC = 30    # universe discovery + selection reload interval


# ──────────────────────────────────────────────────────────────────
# Instruments_df helper (cached, used for token → symbol lookup only
# when ZerodhaTickEngine.strategies doesn't have it)
# ──────────────────────────────────────────────────────────────────

_instruments_df = None
_instruments_lock = threading.Lock()


def _load_instruments_df():
    global _instruments_df
    with _instruments_lock:
        if _instruments_df is None:
            try:
                from app.fetcher.zerodha_instruments import load_instruments_df
                _instruments_df = load_instruments_df()
                write_audit_log(
                    f"[HA][INSTRUMENTS] Loaded {len(_instruments_df)} rows"
                )
            except Exception as e:
                write_audit_log(f"[HA][INSTRUMENTS_ERROR] {e}")
        return _instruments_df


def _symbol_for_token(token: int, ws_engine) -> Optional[str]:
    """
    Resolve tradingsymbol for a token.
    Primary:  ZerodhaTickEngine.strategies (already in memory)
    Fallback: instruments_df lookup
    """
    strat = ws_engine.strategies.get(token)
    if strat and hasattr(strat, "symbol") and strat.symbol:
        return strat.symbol

    df = _load_instruments_df()
    if df is None or df.empty:
        return None

    rows = df[df["instrument_token"] == token]
    if rows.empty:
        return None

    return str(rows.iloc[0]["tradingsymbol"])


# ──────────────────────────────────────────────────────────────────
# Per-symbol state
# ──────────────────────────────────────────────────────────────────

class SymbolState:
    """Holds the 1-min OHLC accumulator + HA converter + EMA for one symbol."""

    def __init__(self, symbol: str, token: int):
        self.symbol = symbol
        self.token  = token

        self.bucket_start: Optional[int] = None
        self._o = self._h = self._l = self._c = None

        self.ha_converter  = HeikinAshiConverter()
        self._ema_low      = EMA(EMA_PERIOD)
        self.ema_low_value: Optional[float] = None

        # 3-candle condition evaluator (signal logic)
        self.evaluator = HAConditionEvaluator()

        # Latest completed HA candle (SL monitor reads this)
        self.last_ha: Optional[HACandle] = None

    # ── DB warmup ────────────────────────────────────────────────

    def warmup_from_db(self):
        """Replay stored HA candles to restore HA converter + EMA state."""
        rows = fetch_recent_ha_candles(
            symbol=self.symbol,
            timeframe="1m",
            limit=WARMUP_CANDLES,
        )

        if not rows:
            return

        for row in rows:
            ha = self.ha_converter.update(
                ts=row["ts"],
                o=row["ha_open"],
                h=row["ha_high"],
                l=row["ha_low"],
                c=row["ha_close"],
            )
            ema_val = self._ema_low.update(ha.low)
            self.ema_low_value = ema_val
            self.evaluator.push(ha, ema_val)
            self.last_ha = ha

        write_audit_log(
            f"[HA][WARMUP] {self.symbol} — replayed {len(rows)} rows "
            f"ema20_low={f'{self.ema_low_value:.2f}' if self.ema_low_value else 'N/A'}"
        )

    # ── 1-min bucket accumulator ──────────────────────────────────

    def on_tick(self, ltp: float, ts: int) -> Optional[HACandle]:
        """
        Accumulate ticks into 1-min OHLC.
        Returns completed HACandle on bucket rollover, else None.
        """
        bucket_start = (ts // TIMEFRAME_SEC) * TIMEFRAME_SEC

        if self.bucket_start is None:
            self.bucket_start = bucket_start
            self._o = self._h = self._l = self._c = ltp
            return None

        if bucket_start == self.bucket_start:
            if ltp > self._h: self._h = ltp
            if ltp < self._l: self._l = ltp
            self._c = ltp
            return None

        # ── Bucket rollover ──────────────────────────────────────
        ha = self.ha_converter.update(
            ts=self.bucket_start,
            o=self._o, h=self._h, l=self._l, c=self._c,
        )
        ema_val = self._ema_low.update(ha.low)
        self.ema_low_value = ema_val
        self.last_ha = ha

        # Start next bucket
        self.bucket_start = bucket_start
        self._o = self._h = self._l = self._c = ltp

        return ha


# ──────────────────────────────────────────────────────────────────
# Tick engine
# ──────────────────────────────────────────────────────────────────

class HAOptionsTickEngine:

    STRATEGY_ID = "HA_V1"

    def __init__(self, executor, config: dict, trade_mode: str):
        self.executor   = executor
        self.config     = config
        self.trade_mode = trade_mode

        # Universe: ALL option tokens discovered from ZerodhaTickEngine
        self._states: Dict[str, SymbolState] = {}    # symbol → state
        self._token_map: Dict[int, str]      = {}    # token  → symbol

        # Currently selected symbols for signal evaluation (1 CE + 1 PE max)
        self._selected_ce: Optional[str] = None
        self._selected_pe: Optional[str] = None
        self._selection_lock = threading.Lock()

        # ── FIX: Track symbols with active open trades ─────────────
        # Selection rotates every ~2 minutes. After rotation, the old
        # strike is no longer _selected_ce/_selected_pe, so on_tick
        # would stop calling check_tp_on_tick for it.  By maintaining
        # this set we guarantee TP/SL monitoring continues regardless
        # of selection changes.
        # Updated immediately on entry (in _on_candle_close) and
        # refreshed from DB every SUBSCRIPTION_RETRY_SEC.
        self._active_trade_symbols: Set[str] = set()
        self._active_trade_lock = threading.Lock()
        # ───────────────────────────────────────────────────────────

        # Track which tokens we've already warmed up (avoid duplicate warmup)
        self._warmed_up: Set[str] = set()

        try:
            init_table()
            write_audit_log("[HA] ha_candles table initialised")
        except Exception as e:
            write_audit_log(f"[HA][DB_INIT_ERROR] {e}")

        self._signal_engine = HASignalEngine(
            max_trades_per_side=config.get("max_trades_per_side", 10)
        )

        from app.engine.ha_options.ha_trade_manager import HATradeManager
        self._trade_manager = HATradeManager(
            strategy_id=self.STRATEGY_ID,
            trade_mode=trade_mode,
            executor=executor,
            signal_engine=self._signal_engine,
            config=config,
        )

        if not any(isinstance(e, HAOptionsTickEngine) for e in HA_ENGINE_REGISTRY):
            HA_ENGINE_REGISTRY.append(self)
            write_audit_log(
                f"[HA] Registered in HA_ENGINE_REGISTRY "
                f"(size={len(HA_ENGINE_REGISTRY)})"
            )

        # First pass — WS may not be ready yet; retry loop handles the rest
        self._reload_universe()
        self._reload_selection()
        self._reload_active_trades()   # seed _active_trade_symbols from DB

        write_audit_log(
            f"[HA][ENGINE_READY] mode={trade_mode} "
            f"universe_size={len(self._token_map)} "
            f"selected_ce={self._selected_ce} "
            f"selected_pe={self._selected_pe} "
            f"active_trades={self._active_trade_symbols}"
        )

    # ── Public start ─────────────────────────────────────────────

    def start(self):
        threading.Thread(
            target=self._sl_monitor_loop,
            daemon=True,
            name="ha-sl-monitor",
        ).start()

        threading.Thread(
            target=self._subscription_retry_loop,
            daemon=True,
            name="ha-sub-retry",
        ).start()

        write_audit_log("[HA] Background threads started")

    # ── WS reconnect ─────────────────────────────────────────────

    def on_ws_reconnect(self):
        """Called by ZerodhaTickEngine._on_connect on every WS reconnect."""
        write_audit_log(
            "[HA][WS_RECONNECT] WS reconnected — universe will be "
            "re-discovered on next retry cycle"
        )

    # ── Tick dispatch (called by ZerodhaTickEngine._on_ticks) ────

    def on_tick(self, token: int, ltp: float, ts: int):
        symbol = self._token_map.get(token)
        if symbol is None:
            return

        LTPStore.update(symbol, ltp)

        # ── TP check on EVERY tick ────────────────────────────────
        # Check for BOTH currently selected symbols AND symbols with
        # an open trade.  The two sets can differ when selection
        # rotates to a new strike after a trade has been entered on
        # the old strike — without this union the old strike's TP
        # would never fire.
        with self._selection_lock:
            is_selected = (
                symbol == self._selected_ce or symbol == self._selected_pe
            )
        with self._active_trade_lock:
            has_active_trade = symbol in self._active_trade_symbols

        if is_selected or has_active_trade:
            try:
                self._trade_manager.check_tp_on_tick(symbol, ltp)
            except Exception as e:
                write_audit_log(f"[HA][TP_TICK_ERROR] {symbol} ERR={e}")

        state = self._states.get(symbol)
        if state is None:
            return

        ha_candle = state.on_tick(ltp, ts)
        if ha_candle is None:
            return

        # Candle completed — always store, conditionally evaluate signal
        self._on_candle_close(symbol, ha_candle, state)

    # ── Process completed HA candle ───────────────────────────────

    def _on_candle_close(self, symbol: str, ha: HACandle, state: SymbolState):
        """
        Always called for EVERY symbol in the universe.

        Step 1: Persist HA candle to DB (always — this is the "universe store")
        Step 2: Check if this symbol is currently selected CE or PE
        Step 3: If selected, evaluate entry signal
        """

        ema_val = state.ema_low_value

        # ── Step 1: Always write to DB ────────────────────────────
        try:
            insert_ha_candle(
                symbol=symbol,
                timeframe="1m",
                ts=ha.ts,
                ha_open=ha.open,
                ha_high=ha.high,
                ha_low=ha.low,
                ha_close=ha.close,
                ema20_low=ema_val,
                is_green=ha.is_green,
            )
        except Exception as e:
            write_audit_log(f"[HA][DB_INSERT_ERR] {symbol} ERR={e}")

        # ── Step 2: Is this symbol currently selected? ─────────────
        with self._selection_lock:
            is_selected_ce = symbol == self._selected_ce
            is_selected_pe = symbol == self._selected_pe

        if not is_selected_ce and not is_selected_pe:
            # Not selected — stored to DB, nothing more to do
            return

        side = "CE" if is_selected_ce else "PE"

        # Throttled per-candle log (only for selected symbols)
        write_audit_log(
            f"[HA][CANDLE] {symbol} side={side} "
            f"O={ha.open:.2f} H={ha.high:.2f} L={ha.low:.2f} C={ha.close:.2f} "
            f"{'GREEN' if ha.is_green else 'RED'} "
            f"EMA20L={f'{ema_val:.2f}' if ema_val else 'WARMING_UP'}"
        )

        # ── Step 3: Gate checks before signal evaluation ───────────

        if ema_val is None:
            write_audit_log(f"[HA][GATE] {symbol} — EMA not ready yet")
            return

        cfg           = load_strategy_config(self.STRATEGY_ID)
        session_start = cfg.get("session", {}).get("primary", {}).get("start", "09:15")
        session_end   = cfg.get("session", {}).get("primary", {}).get("end",   "15:20")
        now_str       = datetime.now().strftime("%H:%M")

        if now_str < session_start or now_str >= session_end:
            write_audit_log(
                f"[HA][GATE] {symbol} — outside session "
                f"({now_str} not in {session_start}–{session_end})"
            )
            return

        if not load_global_config().get("trade_on", False):
            write_audit_log(f"[HA][GATE] {symbol} — trade_on=FALSE")
            return

        side_mode = cfg.get("trade_side_mode", "BOTH")
        if side_mode != "BOTH" and side_mode != side:
            write_audit_log(
                f"[HA][GATE] {symbol} — side_mode={side_mode} blocks {side}"
            )
            return

        allowed, reason = self._signal_engine.can_enter(side)
        if not allowed:
            write_audit_log(f"[HA][GATE] {symbol} — {reason}")
            return

        # ── Evaluate entry conditions ─────────────────────────────
        signal: HAEntrySignal = state.evaluator.push(ha, ema_val)

        if not signal.should_enter:
            write_audit_log(
                f"[HA][NO_ENTRY] {symbol} side={side} "
                f"reject={signal.rejection}"
            )
            return

        if signal.sl_price is None:
            write_audit_log(
                f"[HA][SKIP] {symbol} — no red candle yet, SL unavailable"
            )
            return

        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
            write_audit_log(f"[HA][SKIP] {symbol} — LTP unavailable")
            return

        if signal.sl_price >= ltp:
            write_audit_log(
                f"[HA][SKIP] {symbol} — SL {signal.sl_price:.2f} >= LTP {ltp:.2f}"
            )
            return

        write_audit_log(
            f"[HA][SIGNAL_FIRED] {symbol} side={side} "
            f"cond={signal.condition} sl={signal.sl_price:.2f} ltp={ltp:.2f}"
        )

        self._signal_engine.confirm_entry(side)

        # Annotate DB row with signal
        try:
            insert_ha_candle(
                symbol=symbol,
                timeframe="1m",
                ts=ha.ts,
                ha_open=ha.open,
                ha_high=ha.high,
                ha_low=ha.low,
                ha_close=ha.close,
                ema20_low=ema_val,
                is_green=ha.is_green,
                signal_action=f"ENTER_{side}",
                signal_reason=signal.condition,
            )
        except Exception:
            pass

        success = self._trade_manager.enter(
            symbol=symbol,
            side=side,
            entry_ltp=ltp,
            sl_price=signal.sl_price,
        )

        if not success:
            write_audit_log(
                f"[HA][ENTRY_FAILED] {symbol} — rolling back confirm"
            )
            self._signal_engine.notify_exit(side)
        else:
            # ── FIX: Track this symbol as having an active trade ──
            # This ensures TP/SL monitoring continues even after the
            # selection rotates to a different strike.
            with self._active_trade_lock:
                self._active_trade_symbols.add(symbol)
            write_audit_log(
                f"[HA][ACTIVE_TRADE] {symbol} added to active_trade_symbols "
                f"(total={len(self._active_trade_symbols)})"
            )

    # ── SL monitor — candle close only ────────────────────────────

    def _sl_monitor_loop(self):
        """
        Checks SL on the last completed HA candle close.

        Monitors BOTH currently selected symbols AND symbols with active
        open trades.  This is critical: after selection rotates, the old
        strike is no longer in _selected_*, so without _active_trade_symbols
        its SL would never be checked.

        TP is intentionally NOT checked here — it fires on every tick in
        on_tick() via check_tp_on_tick(). This loop handles SL only.
        """
        last_checked: Dict[str, int] = {}

        while True:
            time.sleep(5)
            try:
                with self._selection_lock:
                    selected = set(filter(None, [
                        self._selected_ce,
                        self._selected_pe,
                    ]))

                with self._active_trade_lock:
                    active = set(self._active_trade_symbols)

                # Union: monitor selected AND any symbol with an open trade
                symbols_to_check = selected | active

                for symbol in symbols_to_check:
                    state = self._states.get(symbol)
                    if state is None or state.last_ha is None:
                        continue
                    # Only check each candle once (deduplicate by ts)
                    if last_checked.get(symbol) == state.last_ha.ts:
                        continue
                    last_checked[symbol] = state.last_ha.ts

                    # SL check on candle close — TP excluded here
                    self._trade_manager.check_sl_on_close(
                        symbol=symbol,
                        candle_close=state.last_ha.close,
                    )

            except Exception as e:
                write_audit_log(f"[HA][SL_MONITOR_ERROR] {e}")

    # ── Active trade symbols refresh (DB source of truth) ────────

    def _reload_active_trades(self):
        """
        Query DB for all open HA_V1 paper/live trades and update
        _active_trade_symbols.  Called at startup and every
        SUBSCRIPTION_RETRY_SEC to reconcile after exits.

        After a trade exits (TP or SL), the symbol stays in
        _active_trade_symbols until this refresh runs — during that
        window check_tp_on_tick is a no-op (no open trades found),
        so the extra calls are harmless.
        """
        try:
            from app.db.paper_trades_repo import get_all_open_paper_trades
            open_trades = get_all_open_paper_trades(self.STRATEGY_ID)
            fresh = {t["symbol"] for t in open_trades if t.get("symbol")}

            with self._active_trade_lock:
                old = set(self._active_trade_symbols)
                self._active_trade_symbols = fresh

            added   = fresh - old
            removed = old - fresh

            if added or removed:
                write_audit_log(
                    f"[HA][ACTIVE_TRADE_SYNC] "
                    f"added={added} removed={removed} current={fresh}"
                )

        except Exception as e:
            write_audit_log(f"[HA][ACTIVE_TRADE_SYNC_ERROR] {e}")

    # ── Universe discovery + subscription retry ───────────────────

    def _subscription_retry_loop(self):
        """
        Runs every SUBSCRIPTION_RETRY_SEC seconds.
        Discovers universe from ZerodhaTickEngine, updates selection,
        and reconciles active trade symbols from DB.
        """
        write_audit_log("[HA][SUB_RETRY] Loop started")

        while True:
            time.sleep(SUBSCRIPTION_RETRY_SEC)
            try:
                before = len(self._token_map)
                self._reload_universe()
                after  = len(self._token_map)

                if after != before:
                    write_audit_log(
                        f"[HA][SUB_RETRY] Universe grew "
                        f"{before} → {after} symbols"
                    )

                self._reload_selection()
                self._reload_active_trades()   # reconcile exits from DB

                write_audit_log(
                    f"[HA][SUB_RETRY] universe={after} "
                    f"selected_ce={self._selected_ce} "
                    f"selected_pe={self._selected_pe} "
                    f"active_trades={self._active_trade_symbols}"
                )

            except Exception as e:
                write_audit_log(f"[HA][SUB_RETRY_ERROR] {e}")

    # ── Universe discovery ────────────────────────────────────────

    def _reload_universe(self):
        """
        Discover all NIFTY option tokens from ZerodhaTickEngine.builders.
        """
        engines = get_ws_engines()
        if not engines:
            write_audit_log(
                "[HA][UNIVERSE] WS engine not ready — will retry in "
                f"{SUBSCRIPTION_RETRY_SEC}s"
            )
            return

        ws_engine = engines[0]
        all_tokens = list(ws_engine.builders.keys())

        if not all_tokens:
            write_audit_log("[HA][UNIVERSE] WS engine has no builders yet")
            return

        new_count = 0

        for token in all_tokens:
            if token in self._token_map:
                continue

            symbol = _symbol_for_token(token, ws_engine)
            if not symbol:
                continue

            if not (symbol.endswith("CE") or symbol.endswith("PE")):
                continue
            if "NIFTY" not in symbol:
                continue

            self._token_map[token] = symbol
            new_count += 1

            if symbol not in self._states:
                self._states[symbol] = SymbolState(symbol=symbol, token=token)

            if symbol not in self._warmed_up:
                self._warmed_up.add(symbol)
                self._states[symbol].warmup_from_db()

        if new_count:
            write_audit_log(
                f"[HA][UNIVERSE] Added {new_count} new symbols. "
                f"Total universe: {len(self._token_map)} options"
            )

    # ── Selection reload ──────────────────────────────────────────

    def _reload_selection(self):
        """
        Read SCALP_V1 selection files and update _selected_ce / _selected_pe.
        """
        from pathlib import Path
        import json

        state_dir = Path.home() / ".scalp-app" / "state"
        cfg       = load_strategy_config(self.STRATEGY_ID)
        prem_min  = cfg.get("option_premium", {}).get("min", 0)
        prem_max  = cfg.get("option_premium", {}).get("max", 9999)

        new_ce: Optional[str] = None
        new_pe: Optional[str] = None

        for suffix, label in [("ce", "CE"), ("pe", "PE")]:
            fpath = state_dir / f"SCALP_V1_selected_{suffix}.json"
            if not fpath.exists():
                continue

            try:
                rows = json.loads(fpath.read_text())
                if not rows:
                    continue

                chosen = next(
                    (r for r in rows if prem_min <= (r.get("ltp") or 0) <= prem_max),
                    rows[0],
                )

                symbol = (
                    chosen.get("tradingsymbol")
                    or chosen.get("symbol")
                )
                if not symbol:
                    continue

                if symbol not in self._states:
                    token = (
                        chosen.get("instrument_token")
                        or chosen.get("token")
                    )
                    if not token:
                        df = _load_instruments_df()
                        if df is not None and not df.empty:
                            rows_df = df[df["tradingsymbol"] == symbol]
                            if not rows_df.empty:
                                token = int(rows_df.iloc[0]["instrument_token"])

                    if token:
                        token = int(token)
                        self._token_map[token] = symbol
                        self._states[symbol] = SymbolState(
                            symbol=symbol, token=token
                        )
                        if symbol not in self._warmed_up:
                            self._warmed_up.add(symbol)
                            self._states[symbol].warmup_from_db()

                        write_audit_log(
                            f"[HA][SELECTION] {label} {symbol} added to universe "
                            f"(was not in ZerodhaTickEngine universe)"
                        )
                    else:
                        write_audit_log(
                            f"[HA][SELECTION] {label} {symbol} — "
                            f"token not resolvable, skipping"
                        )
                        continue

                if label == "CE":
                    new_ce = symbol
                else:
                    new_pe = symbol

            except Exception as e:
                write_audit_log(f"[HA][SELECTION_ERROR] {label}: {e}")

        with self._selection_lock:
            changed = (new_ce != self._selected_ce or new_pe != self._selected_pe)
            self._selected_ce = new_ce
            self._selected_pe = new_pe

        if changed:
            write_audit_log(
                f"[HA][SELECTION] Updated → CE={new_ce} PE={new_pe}"
            )

    # ── EOD ──────────────────────────────────────────────────────

    def eod_squareoff(self):
        try:
            self._trade_manager.eod_squareoff()
        except Exception as e:
            write_audit_log(f"[HA][EOD_ERROR] {repr(e)}")