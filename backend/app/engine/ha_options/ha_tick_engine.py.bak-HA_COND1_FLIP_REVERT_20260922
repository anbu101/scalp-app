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

ENTRY FILL CONFIRMATION (Option 1):
  enter() returns True the moment the BUY order is PLACED — the true fill is
  confirmed by a background thread in HATradeManager (so the WS tick thread is
  never blocked, and HA TP/SL monitoring for the whole universe never stalls).
  If that background confirmation finds the order was REJECTED/CANCELLED/LAPSED,
  it calls back into on_entry_dead(symbol, side) to roll back monitoring +
  signal-engine state for the phantom position.

EXECUTION MODES (LIVE / PAPER / OFF):
  In OFF mode the engine still discovers the universe, builds HA candles,
  computes EMA, persists every candle, and keeps evaluator buffers warm. The
  ONLY difference is that NO new entry signal is acted on. Exits for any
  already-open position continue to run.

EXIT DESIGN:
  TP → checked on EVERY TICK via check_tp_on_tick() in on_tick().
  SL → checked on CANDLE CLOSE only via check_sl_on_close() in _sl_monitor_loop().
  Both monitored for the currently selected symbols AND any symbol with an
  active open trade (_active_trade_symbols), because selection rotates every
  ~2 minutes and an open trade may be on an older, no-longer-selected strike.
"""

import time
import threading
from typing import Dict, Optional, Set
from datetime import datetime
from app.risk.risk_mtm_guard import mtm_breach_ha
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

# ── ARB_WINDOW BEGIN ──────────────────────────────────────────────
# Default arbitration-window length (seconds). When the first HA signal of a
# minute fires while HA is globally flat, we hold it and arm a one-shot timer
# for this long; any same-minute signal from the other selected side joins the
# window; on timer expiry we elect the HIGHEST entry premium (symbol-string
# tie-break) and enter exactly one — matching the backtest's global single-trade
# arbitration. Overridable per-strategy via config "arbitration_window_sec".
ARBITRATION_WINDOW_SEC_DEFAULT = 2.0
# ── ARB_WINDOW END ────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# Instruments_df helper (cached, used for token → symbol lookup only
# when ZerodhaTickEngine.strategies doesn't have it)
# ──────────────────────────────────────────────────────────────────

_instruments_df = None
_instruments_lock = threading.Lock()

# ── HA_COND_FILTER BEGIN ── live entry-condition gate (paper + live).
# Identical fail-open contract to the backtest runner: absent / empty /
# all-invalid config value => ALL conditions enabled, so existing config files
# (which have no entry_conditions key) trade normally. Names are the exact
# strings HAConditionEvaluator emits on HAEntrySignal.condition.
_HA_ALL_CONDS = ("COND1", "COND2", "COND3")

def _resolve_enabled_conditions(cfg: dict) -> set:
    raw = (cfg or {}).get("entry_conditions") or []
    enabled = {str(x).strip().upper() for x in raw
               if str(x).strip().upper() in _HA_ALL_CONDS}
    return enabled if enabled else set(_HA_ALL_CONDS)
# ── HA_COND_FILTER END ──

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

        # ── Track symbols with active open trades ─────────────────
        # Selection rotates every ~2 minutes. After rotation, the old
        # strike is no longer _selected_*, so on_tick would stop calling
        # check_tp_on_tick for it. This set guarantees TP/SL monitoring
        # continues regardless of selection changes.
        self._active_trade_symbols: Set[str] = set()
        self._active_trade_lock = threading.Lock()
        # ───────────────────────────────────────────────────────────

        # Track which tokens we've already warmed up (avoid duplicate warmup)
        self._warmed_up: Set[str] = set()
        self._last_mtm_check_ts = 0.0   # ← ADD

        # ── ARB_WINDOW BEGIN ──────────────────────────────────────
        # Per-minute arbitration window state. _pending_arb holds the candidate
        # group for the minute bucket currently being contested:
        #   {"bucket_ts": int, "candidates": [ {side,symbol,entry_ltp,sl,cond} ]}
        # _arb_timer is the one-shot threading.Timer that fires the election.
        # The GLOBAL occupancy gate (pending OR open) lives in the trade manager
        # (ha_is_occupied / arm_pending / clear_pending) — this engine only owns
        # the candidate collection + the timer.
        self._pending_arb: Optional[dict] = None
        self._arb_timer: Optional[threading.Timer] = None
        self._arb_state_lock = threading.Lock()
        # ── ARB_WINDOW END ────────────────────────────────────────

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
            engine=self,        # back-reference for on_entry_dead rollback
        )
        # Ensure the back-reference is set even if a future constructor
        # signature drops the kwarg.
        try:
            self._trade_manager.attach_engine(self)
        except Exception:
            pass

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

    # ── Entry dead-order rollback hook ───────────────────────────

    def on_entry_dead(self, symbol: str, side: str):
        """
        Called by HATradeManager's background fill-confirm thread when an entry
        order turns out REJECTED/CANCELLED/LAPSED. Rolls back the monitoring
        state the engine set up when enter() returned True:
          - remove the symbol from _active_trade_symbols (stops TP/SL polling)
          - clear the signal-engine in-trade flag for the side

        The trade manager has already removed the in-memory _live trade and
        closed the DB row by the time this is called.
        """
        with self._active_trade_lock:
            self._active_trade_symbols.discard(symbol)

        try:
            self._signal_engine.notify_exit(side)
        except Exception:
            pass

        write_audit_log(
            f"[HA][ENTRY_DEAD_ROLLBACK] {symbol} side={side} "
            f"removed from active_trade_symbols, signal flag cleared "
            f"(active={len(self._active_trade_symbols)})"
        )

    # ── Tick dispatch (called by ZerodhaTickEngine._on_ticks) ────

    def on_tick(self, token: int, ltp: float, ts: int):
        symbol = self._token_map.get(token)
        if symbol is None:
            return

        LTPStore.update(symbol, ltp)

        # ── MTM risk square-off (throttled inside) ──
        self._maybe_mtm_squareoff()

        # ── TP check on EVERY tick ────────────────────────────────
        # Check for BOTH currently selected symbols AND symbols with an open
        # trade. The sets differ when selection rotates after a trade opened.
        # Runs in every mode including OFF — TP must still fire for a position
        # opened before the strategy was turned off.
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

        Step 1: Persist HA candle to DB (always — the "universe store")
        Step 2: Check if this symbol is currently selected CE or PE
        Step 3: If selected, evaluate entry signal

        OFF MODE: Steps 1 and the candle/EMA/evaluator updates ALWAYS run.
        Only the entry-signal action (Step 3) is suppressed via the OFF gate.
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

        # ── OFF GATE ───────────────────────────────────────────────
        if self._trade_manager.is_off():
            write_audit_log(
                f"[HA][GATE] {symbol} side={side} — mode=OFF, "
                f"entry suppressed (data/candles/indicators still running)"
            )
            return

        from app.risk.risk_mtm_guard import is_day_blocked
        if is_day_blocked(self.STRATEGY_ID):
            write_audit_log(f"[HA][GATE] {symbol} — MTM day-block, entry suppressed")
            return

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

        # ── MIN_SL_GATE BEGIN ─────────────────────────────────────
        # Reject entries whose SL distance (ltp - sl) is below the configured
        # floor. 0 = disabled. Guards against sub-rupee SLs where charges exceed
        # any realistic profit (the same gate exists in backtest_ha_runner so
        # live and backtest agree). Uses min_sl_points (shared key across the
        # SCALP strategies). Checked BEFORE the signal enters the arbitration
        # window — a sub-floor signal never becomes a candidate.
        try:
            _min_sl = abs(float(cfg.get("min_sl_points", 0) or 0))
        except Exception:
            _min_sl = 0.0
        if _min_sl > 0 and (ltp - float(signal.sl_price)) < _min_sl:
            write_audit_log(
                f"[HA][SKIP] {symbol} — SL distance "
                f"{ltp - float(signal.sl_price):.2f} < MIN SL {_min_sl:.2f} "
                f"(sl={signal.sl_price:.2f} ltp={ltp:.2f})"
            )
            return
        # ── MIN_SL_GATE END ───────────────────────────────────────

        # ── HA_COND_FILTER BEGIN ── entry-condition multi-select (PAPER + LIVE).
        # Fail-open contract identical to backtest_ha_runner: absent / empty /
        # all-invalid entry_conditions => ALL enabled, so existing config files
        # without the key trade normally. Placed BEFORE SIGNAL_FIRED and BEFORE
        # _offer_to_arbitration so a disabled-condition signal never occupies
        # the arbitration window, never starts the timer, and can never shadow
        # a valid lower-premium signal. confirm_entry is deferred to election,
        # so the daily per-side cap is untouched by filtered signals.
        try:
            _raw_conds = cfg.get("entry_conditions") or []
            _enabled_conds = {str(x).strip().upper() for x in _raw_conds
                              if str(x).strip().upper() in ("COND1", "COND2", "COND3")}
        except Exception:
            _enabled_conds = set()
        if not _enabled_conds:
            _enabled_conds = {"COND1", "COND2", "COND3"}
        if signal.condition not in _enabled_conds:
            write_audit_log(
                f"[HA][SKIP] {symbol} — condition {signal.condition} not in "
                f"enabled {{{','.join(sorted(_enabled_conds))}}}"
            )
            return
        # ── HA_COND_FILTER END ──

        # ── HA_COND1_FLIP BEGIN ── COND1-only opposite-side entry (PAPER +
        # LIVE). cond1_flip_side: bool, default OFF → this whole block is a
        # no-op and the entry_* vars below equal the signal values, so COND2 /
        # COND3 and flip-off COND1 behave byte-identically to before.
        #
        # WHAT: a COND1 signal on the selected CE enters the selected PE (and
        # vice versa) — validated in backtest (C1 standalone -4.14L → flipped
        # +1.50L; C1flip+C3 net/DD 2.14 vs C2+C3 1.39). The signal contract's
        # risk transfers IN POINTS (its red-low is meaningless on the flipped
        # contract): flip_sl = flip_ltp − (ltp − red_low). trade_manager.enter
        # computes TP from the entry_ltp it receives, so the flipped TP is
        # correct with zero trade-manager changes.
        #
        # FAIL-CLOSED: every unmet precondition SKIPS the entry entirely —
        # NEVER falls back to the signal side (that side is the direction the
        # data says loses). Skip cases: opposite side not selected, opposite
        # LTP unavailable, side_mode blocks the flipped side, per-side cap
        # blocks the flipped side, or the transferred risk >= flipped LTP
        # (synthetic SL would be <= 0).
        #
        # The DB candle annotation below stays on the SIGNAL symbol/side — it
        # records that the evaluator fired, which is true regardless of which
        # contract trades. Arbitration election re-checks can_enter on the
        # entry side (the flipped side) exactly as before.
        _entry_symbol = symbol
        _entry_side = side
        _entry_ltp = float(ltp)
        _entry_sl = float(signal.sl_price)
        if bool(cfg.get("cond1_flip_side")) and signal.condition == "COND1":
            _opp_side = "PE" if side == "CE" else "CE"
            _fm = cfg.get("trade_side_mode", "BOTH")
            if _fm != "BOTH" and _fm != _opp_side:
                write_audit_log(
                    f"[HA][FLIP_SKIP] {symbol} — side_mode={_fm} blocks "
                    f"flipped side {_opp_side}; no entry (fail-closed)"
                )
                return
            _fok, _freason = self._signal_engine.can_enter(_opp_side)
            if not _fok:
                write_audit_log(
                    f"[HA][FLIP_SKIP] {symbol} — flipped side {_opp_side}: "
                    f"{_freason}; no entry (fail-closed)"
                )
                return
            with self._selection_lock:
                _opp_symbol = (self._selected_pe if side == "CE"
                               else self._selected_ce)
            if not _opp_symbol:
                write_audit_log(
                    f"[HA][FLIP_SKIP] {symbol} — no selected {_opp_side} to "
                    f"flip into; no entry (fail-closed)"
                )
                return
            _opp_ltp = LTPStore.get(_opp_symbol)
            if not _opp_ltp or _opp_ltp <= 0:
                write_audit_log(
                    f"[HA][FLIP_SKIP] {symbol} — LTP unavailable for flipped "
                    f"{_opp_symbol}; no entry (fail-closed)"
                )
                return
            _risk = float(ltp) - float(signal.sl_price)
            _flip_sl = float(_opp_ltp) - _risk
            if _flip_sl <= 0:
                write_audit_log(
                    f"[HA][FLIP_SKIP] {symbol} — transferred risk "
                    f"{_risk:.2f} >= flipped LTP {float(_opp_ltp):.2f} "
                    f"({_opp_symbol}); synthetic SL <= 0; no entry (fail-closed)"
                )
                return
            _entry_symbol = _opp_symbol
            _entry_side = _opp_side
            _entry_ltp = float(_opp_ltp)
            _entry_sl = _flip_sl
            write_audit_log(
                f"[HA][FLIP] COND1 {symbol} {side} ltp={float(ltp):.2f} "
                f"red_low={float(signal.sl_price):.2f} risk={_risk:.2f} → "
                f"ENTER {_entry_symbol} {_entry_side} ltp={_entry_ltp:.2f} "
                f"sl={_entry_sl:.2f}"
            )
        # ── HA_COND1_FLIP END ──

        write_audit_log(
            f"[HA][SIGNAL_FIRED] {_entry_symbol} side={_entry_side} "
            f"cond={signal.condition} sl={_entry_sl:.2f} ltp={_entry_ltp:.2f}"
        )

        # Annotate DB row with signal (unchanged — records that a signal fired,
        # independent of whether arbitration ultimately elects this side).
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

        # ── ARB_WINDOW BEGIN ──────────────────────────────────────
        # GLOBAL single-trade arbitration. Instead of confirm_entry + enter()
        # synchronously, defer the candidate into a per-minute window. The
        # winner (highest entry_ltp) is elected when the window timer expires;
        # only the winner gets confirm_entry()+enter(). This matches the
        # backtest's global one-trade-at-a-time, highest-premium arbitration and
        # applies in BOTH paper and live. confirm_entry is DEFERRED to election
        # (per decision) so a dropped side never burns a daily-cap slot.
        # ── HA_COND1_FLIP ── the offer uses the entry_* vars: identical to the
        # signal values when the flip is off; the flipped contract when on.
        self._offer_to_arbitration(
            bucket_ts=int(ha.ts),
            side=_entry_side,
            symbol=_entry_symbol,
            entry_ltp=_entry_ltp,
            sl_price=_entry_sl,
            condition=signal.condition,
        )
        # ── ARB_WINDOW END ────────────────────────────────────────

    # ── ARB_WINDOW BEGIN ──────────────────────────────────────────
    # Per-minute arbitration window: collect same-minute candidates across both
    # selected sides, then elect the highest entry premium on timer expiry.

    def _window_sec(self) -> float:
        """Arbitration window length from config, default 2.0s. Clamped to a
        sane [0.2, 10.0] range so a bad config value can't wedge entries."""
        try:
            v = float(load_strategy_config(self.STRATEGY_ID).get(
                "arbitration_window_sec", ARBITRATION_WINDOW_SEC_DEFAULT
            ))
        except Exception:
            v = ARBITRATION_WINDOW_SEC_DEFAULT
        if v < 0.2:
            v = 0.2
        if v > 10.0:
            v = 10.0
        return v

    def _offer_to_arbitration(self, *, bucket_ts, side, symbol,
                              entry_ltp, sl_price, condition):
        """
        Offer a fired signal to the global arbitration window.

        - If HA is globally occupied (a trade is OPEN), drop immediately
          (bare audit line) — this is the money-management gate.
        - Else, if a window is already armed for THIS bucket_ts, append this
          candidate to it.
        - Else, if a window is armed for a DIFFERENT (earlier) bucket_ts, the
          gate is occupied by a pending election → drop (bare audit line).
        - Else, arm a fresh window for this bucket_ts and start the timer.
        """
        # Money-management gate: never offer while a trade is open. (A pending
        # election is handled below by the per-bucket logic + arm_pending.)
        try:
            if self._trade_manager._has_open_trade():
                write_audit_log(
                    f"[HA][ARB_DROP] {symbol} side={side} ltp={entry_ltp:.2f} "
                    f"bucket={bucket_ts} — a trade is already open (global gate)"
                )
                return
        except Exception:
            # _has_open_trade fails SAFE (returns True) internally; this except
            # is only for an unexpected attribute error. Treat as occupied.
            write_audit_log(
                f"[HA][ARB_DROP] {symbol} side={side} — open-check error, "
                f"treating as occupied"
            )
            return

        candidate = {
            "side": side, "symbol": symbol,
            "entry_ltp": entry_ltp, "sl": sl_price, "condition": condition,
        }

        with self._arb_state_lock:
            pend = self._pending_arb

            # Same-minute window already open → join it.
            if pend is not None and pend["bucket_ts"] == bucket_ts:
                pend["candidates"].append(candidate)
                write_audit_log(
                    f"[HA][ARB_JOIN] {symbol} side={side} ltp={entry_ltp:.2f} "
                    f"bucket={bucket_ts} — joined window "
                    f"({len(pend['candidates'])} candidates)"
                )
                return

            # A window is open for a DIFFERENT bucket → gate occupied, drop.
            if pend is not None and pend["bucket_ts"] != bucket_ts:
                write_audit_log(
                    f"[HA][ARB_DROP] {symbol} side={side} ltp={entry_ltp:.2f} "
                    f"bucket={bucket_ts} — election pending for bucket "
                    f"{pend['bucket_ts']} (global gate)"
                )
                return

            # No window open → try to claim the global gate and arm one.
            if not self._trade_manager.arm_pending():
                # Lost the race — something became occupied between the open
                # check and here.
                write_audit_log(
                    f"[HA][ARB_DROP] {symbol} side={side} ltp={entry_ltp:.2f} "
                    f"bucket={bucket_ts} — gate became occupied (race)"
                )
                return

            win = self._window_sec()
            self._pending_arb = {"bucket_ts": bucket_ts, "candidates": [candidate]}
            self._arb_timer = threading.Timer(win, self._resolve_arbitration, args=(bucket_ts,))
            self._arb_timer.daemon = True
            self._arb_timer.start()
            write_audit_log(
                f"[HA][ARB_ARM] {symbol} side={side} ltp={entry_ltp:.2f} "
                f"bucket={bucket_ts} — window armed {win:.1f}s"
            )

    def _resolve_arbitration(self, bucket_ts: int):
        """
        Timer callback: elect the highest-premium candidate for this bucket and
        enter exactly one. confirm_entry is applied ONLY to the elected winner
        (deferred from signal time). Losers get a bare audit line. Always
        releases the pending gate; on a successful entry the gate stays occupied
        via the now-open trade (_has_open_trade), on failure it goes flat.
        """
        with self._arb_state_lock:
            pend = self._pending_arb
            # Guard: a newer/different window, or already cleared (e.g. EOD
            # cancel) — do nothing.
            if pend is None or pend["bucket_ts"] != bucket_ts:
                return
            # Detach this window so no late joiner mutates it during election.
            self._pending_arb = None
            self._arb_timer = None
            candidates = list(pend["candidates"])

        if not candidates:
            self._trade_manager.clear_pending()
            return

        # Re-check session: if the window crossed session end / EOD fired, the
        # eod hook normally clears us, but guard here too — cancel, no entry.
        try:
            cfg = load_strategy_config(self.STRATEGY_ID)
            s_start = cfg.get("session", {}).get("primary", {}).get("start", "09:15")
            s_end = cfg.get("session", {}).get("primary", {}).get("end", "15:20")
            now_str = datetime.now().strftime("%H:%M")
            if now_str < s_start or now_str >= s_end:
                write_audit_log(
                    f"[HA][ARB_CANCEL_EOD] bucket={bucket_ts} — window resolved "
                    f"outside session ({now_str}); no entry"
                )
                self._trade_manager.clear_pending()
                return
        except Exception:
            pass

        # Elect: highest entry premium, symbol-string tie-break (== backtest).
        winner = max(candidates, key=lambda c: (c["entry_ltp"], c["symbol"]))
        losers = [c for c in candidates if c is not winner]

        for lc in losers:
            write_audit_log(
                f"[HA][ARB_DROP] {lc['symbol']} side={lc['side']} "
                f"ltp={lc['entry_ltp']:.2f} bucket={bucket_ts} — lost election "
                f"to {winner['symbol']} ({winner['entry_ltp']:.2f})"
            )

        side = winner["side"]
        symbol = winner["symbol"]

        # Re-check the per-side daily cap at election time (state may have
        # changed during the window). If now blocked, cancel — release gate.
        allowed, reason = self._signal_engine.can_enter(side)
        if not allowed:
            write_audit_log(
                f"[HA][ARB_CANCEL] {symbol} side={side} — {reason} at election; "
                f"no entry"
            )
            self._trade_manager.clear_pending()
            return

        write_audit_log(
            f"[HA][ARB_ELECT] {symbol} side={side} ltp={winner['entry_ltp']:.2f} "
            f"bucket={bucket_ts} cond={winner['condition']} "
            f"({len(candidates)} candidate(s))"
        )

        # confirm_entry DEFERRED to here — only the elected side increments the
        # per-side daily counter + in-trade flag.
        self._signal_engine.confirm_entry(side)

        success = self._trade_manager.enter(
            symbol=symbol,
            side=side,
            entry_ltp=winner["entry_ltp"],
            sl_price=winner["sl"],
        )

        if not success:
            write_audit_log(
                f"[HA][ENTRY_FAILED] {symbol} — rolling back confirm + gate"
            )
            self._signal_engine.notify_exit(side)
            # enter() failure: release the pending gate so HA is flat again.
            # (On the live path a DEAD fill later also calls clear_pending; this
            # covers the synchronous False return.)
            self._trade_manager.clear_pending()
        else:
            # Trade is now open → the global gate stays occupied via
            # _has_open_trade(); release only the PENDING half.
            self._trade_manager.clear_pending()
            with self._active_trade_lock:
                self._active_trade_symbols.add(symbol)
            write_audit_log(
                f"[HA][ACTIVE_TRADE] {symbol} added to active_trade_symbols "
                f"(total={len(self._active_trade_symbols)})"
            )

    def _cancel_pending_arbitration(self, why: str):
        """Cancel any armed window without entering (used by EOD/session-end).
        Cancels the timer, drops candidates, releases the pending gate."""
        with self._arb_state_lock:
            pend = self._pending_arb
            timer = self._arb_timer
            self._pending_arb = None
            self._arb_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        if pend is not None:
            write_audit_log(
                f"[HA][ARB_CANCEL_EOD] bucket={pend['bucket_ts']} "
                f"candidates={len(pend['candidates'])} — {why}; no entry"
            )
        self._trade_manager.clear_pending()
    # ── ARB_WINDOW END ────────────────────────────────────────────

    # ── SL monitor — candle close only ────────────────────────────

    def _sl_monitor_loop(self):
        """
        Checks SL on the last completed HA candle close.
        Monitors BOTH selected symbols AND symbols with active open trades.
        TP is NOT checked here — it fires on every tick in on_tick().
        Runs in every mode including OFF.
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
        Rebuild _active_trade_symbols from ALL open HA_V1 positions — paper AND
        LIVE — unioned with the in-memory live set. Called at startup and every
        SUBSCRIPTION_RETRY_SEC to reconcile after exits.

        ROOT-CAUSE FIX (Issue 2): the previous version queried ONLY paper trades
        and then OVERWROTE the set. A LIVE trade lives in the `trades` table, not
        `paper_trades`, so for a live position the paper query returned [] and the
        overwrite EVICTED the live symbol from _active_trade_symbols — which is
        the ONLY gate (besides current selection) that keeps on_tick() calling
        check_tp_on_tick(). After selection rotated, the live position had NO TP
        monitoring and ran past TP naked. (2026-… first live HA session.)

        NEW: fresh = open-paper ∪ open-live(DB) ∪ in-memory _live, and we NEVER
        shrink below the trade manager's in-memory live set — memory is the
        authority for live positions, so even a transient DB read miss can never
        evict a position we know is open. A symbol only leaves the set when it is
        gone from ALL THREE sources (i.e. genuinely closed).
        """
        try:
            from app.db.paper_trades_repo import get_all_open_paper_trades
            paper_syms = {
                t["symbol"] for t in get_all_open_paper_trades(self.STRATEGY_ID)
                if t.get("symbol")
            }
        except Exception as e:
            write_audit_log(f"[HA][ACTIVE_TRADE_SYNC][PAPER_ERR] {e}")
            paper_syms = set()

        try:
            from app.db.trades_repo import get_open_trades_for_strategy
            live_syms = {
                t["symbol"] for t in get_open_trades_for_strategy(self.STRATEGY_ID)
                if t.get("symbol")
            }
        except Exception as e:
            write_audit_log(f"[HA][ACTIVE_TRADE_SYNC][LIVE_ERR] {e}")
            live_syms = set()

        # In-memory live positions from the trade manager — authoritative; a DB
        # miss must never drop these.
        try:
            mem_syms = {
                t.symbol for t in self._trade_manager._live.values()
                if getattr(t, "symbol", None)
            }
        except Exception:
            mem_syms = set()

        fresh = paper_syms | live_syms | mem_syms

        try:
            with self._active_trade_lock:
                old = set(self._active_trade_symbols)
                self._active_trade_symbols = fresh
        except Exception as e:
            write_audit_log(f"[HA][ACTIVE_TRADE_SYNC_ERROR] {e}")
            return

        added   = fresh - old
        removed = old - fresh
        if added or removed:
            write_audit_log(
                f"[HA][ACTIVE_TRADE_SYNC] added={added} removed={removed} "
                f"current={fresh} (paper={len(paper_syms)} live_db={len(live_syms)} "
                f"mem={len(mem_syms)})"
            )

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

                # ── GTT_RECON BEGIN ── close any broker TP-GTT that fired while
                # the app was blind (lost ticks / restarted), BEFORE re-adding
                # active symbols — otherwise a just-closed symbol gets re-added
                # this same cycle. Best-effort; never raises.
                try:
                    self._trade_manager.reconcile_gtt_exits()
                except Exception as e:
                    write_audit_log(f"[HA][GTT_RECON][ENGINE_ERR] {e}")
                # ── GTT_RECON END ──

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
        # ── ARB_WINDOW BEGIN ── cancel any pending election before squaring off
        # so a window resolving milliseconds after EOD can't open a trade.
        try:
            self._cancel_pending_arbitration("EOD square-off")
        except Exception as e:
            write_audit_log(f"[HA][ARB_CANCEL_ERR] {repr(e)}")
        # ── ARB_WINDOW END ──
        try:
            self._trade_manager.eod_squareoff()
        except Exception as e:
            write_audit_log(f"[HA][EOD_ERROR] {repr(e)}")

    def _maybe_mtm_squareoff(self):
            """
            Throttled (~3s) live-MTM risk check for HA_V1.

            DRIVER (revised — Decision A + B):
              The day-block is now LIVE-evaluated by risk_mtm_guard.is_day_blocked
              (it self-clears the instant the limit is raised or set to 0). So a
              mid-day limit change un-blocks immediately and this loop stops.

              Close-until-flat is preserved WITHOUT spam: we keep re-running the
              close path while a breach is live OR the day-block is set, BUT only
              when there is actually something open to close. Once flat, the loop
              does nothing — no eod_squareoff call, no log line — which kills the
              every-3s "[HA][...][EOD] 0 trade(s) closed" spam that occurred when
              the strategy was blocked-but-flat (e.g. limits set to 0 mid-day).
            """
            import time as _t
            now = _t.time()
            if now - self._last_mtm_check_ts < 3.0:
                return
            self._last_mtm_check_ts = now

            mode = self._trade_manager._mode()

            try:
                reason = mtm_breach_ha(
                    trade_mode=mode,
                    trade_manager=self._trade_manager,
                    executor=self.executor,
                )
            except Exception as e:
                write_audit_log(f"[HA][MTM_CHECK_ERROR] {e}")
                return

            from app.risk.risk_mtm_guard import (
                is_day_blocked,
                has_open_positions_ha,
            )
            already_blocked = is_day_blocked(self.STRATEGY_ID)

            # Nothing to do unless a fresh breach fired, or we're still blocked.
            if not reason and not already_blocked:
                return

            # Decision B: even while (legitimately) blocked, only act when there
            # is something actually open. A flat, blocked strategy is a no-op —
            # this is what stops the every-3s square-off spam.
            try:
                has_open = has_open_positions_ha(mode, self._trade_manager)
            except Exception as e:
                write_audit_log(f"[HA][MTM_OPEN_CHECK_ERR] {e}")
                has_open = True   # fail safe: assume open, let the close path run

            if not has_open:
                # Blocked but flat — nothing to square off. Stay silent.
                return

            write_audit_log(
                f"[HA][MTM_SQUAREOFF] reason={reason} day_blocked={already_blocked} "
                f"— closing open position(s)"
            )

            # Reuse the EOD path: closes whatever is actually open (live _live dict
            # or paper rows), per side, via the trade manager. Idempotent — a side
            # already flat is a no-op, so re-running while day-blocked is safe.
            try:
                self._trade_manager.eod_squareoff()
            except Exception as e:
                write_audit_log(f"[HA][MTM_SQUAREOFF_ERR] {e}")