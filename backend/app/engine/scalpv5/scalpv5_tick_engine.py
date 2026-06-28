# backend/app/engine/scalpv5/scalpv5_tick_engine.py
#
# SCALP_V5 — Tick Engine (TEST option-BUYING strategy, 3-minute candles).
# ============================================================================
# Structural twin of ScalpV3TickEngine: OWNS its own KiteTicker, builders,
# warmup, index/expiry resolution. SEPARATE WebSocket from V1/BB/HA/V2/V3/V4 —
# it does NOT touch any shared registry or the shared ZerodhaTickEngine.
# SCALP_V1..V4 / BB / HA are completely unaffected.
#
# The candle → indicator pipeline is IDENTICAL to V1/V3 (same Pine indicator
# engine), but the SIGNAL comes from ScalpV5Engine (the 4-gate LONG filter) and
# the strategy BUYS the signalling contract itself (no hedge).
#
# DIFFERENCES FROM V3:
#   - 3-minute candles (timeframe_sec=180), written to the timeline table with
#     timeframe="3m". V5's NIFTY option symbols cannot collide with BB's
#     BANKNIFTY-fut 3m rows or V1's NIFTY 1m rows (symbol + timeframe are keyed).
#   - Single-instrument: a BUY signal enters the signalling contract directly
#     via manager.open_trade() — there is NO hedge pairing / arbitration.
#   - Exits: _watch_exit covers SL / TP (tick-driven) for the one open trade;
#     the EMA exit (candle closes below EMA20_HIGH) is candle-driven in
#     write_candle_async via strategy.should_exit_on_candle(); MTM is throttled
#     tick-driven. Live SL/TP also has a broker GTT per the manager's matrix; the
#     tick watcher is the uniform backstop. There is NO time-based exit.
#
# SELECTION-MEMBERSHIP GATE (parity with V1's SignalRouter):
#   The candle pipeline evaluates the ENTIRE subscribed universe, so BUY signals
#   fire for contracts OUTSIDE V5's selected 2-CE / 2-PE premium-band set. V5
#   gates in _handle_signal: the signalling contract MUST be one of V5's
#   currently-selected strikes on its own side, or the signal is dropped.
# ============================================================================

from typing import Dict, List, Optional
import time
from datetime import date, datetime
import threading

from kiteconnect import KiteTicker, KiteConnect

from app.candles.candle_builder import CandleBuilder
from app.marketdata.candle import Candle, CandleSource
from app.marketdata.ltp_store import LTPStore

from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.condition_engine_v1_9 import ConditionEngineV19

from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df
from app.db import timeline_repo

from app.persistence.market_timeline_writer import write_market_timeline_row
from app.utils.market_hours import is_market_open
from app.marketdata.market_indices_state import MarketIndicesState
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.utils.session_utils import is_within_session
from app.utils.selection_persistence import load_selection

from app.engine.scalpv5.scalpv5_engine import ScalpV5Engine
from app.engine.scalpv5.scalpv5_manager import ScalpV5Manager
from app.db.scalpv5_repo import get_open_v5_trade, reconcile_stale_open_v5_trades


STRATEGY_ID = "SCALP_V5"

# Same-candle signal arbitration tuning (parity with ScalpV3TickEngine).
# When >1 SELECTED contract fires a BUY on the SAME 3m candle, buffer them for a
# short window and elect the HIGHEST entry premium (entry_price = the closed-3m
# premium, identical on every machine), with the symbol string as a stable
# tie-break. Only the elected winner is entered; the manager's DB single-trade
# gate remains the authoritative entry gate.
_SIG_ARB_WINDOW_S  = 0.4    # collection window from the first same-candle candidate
_SIG_ARB_FIRED_MAX = 512    # bound the fired-candle set over a session


def _timeframe_str(timeframe_sec: int) -> str:
    minutes = timeframe_sec // 60
    return f"{minutes}m" if minutes > 0 else f"{timeframe_sec}s"


class ScalpV5TickEngine:
    """
    SCALP_V5 WebSocket tick engine. Owns its own KiteTicker. Built by the
    selection loop with the shared data-kite, the option-universe tokens, and
    the execution router. Constructs its own ScalpV5Manager.
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        kite_data: KiteConnect,
        instrument_tokens: List[int],
        executor,
        timeframe_sec: int = 180,
    ):
        self.strategy_id   = STRATEGY_ID
        self.kite_data     = kite_data
        self.timeframe_sec = timeframe_sec
        self.timeframe_str = _timeframe_str(timeframe_sec)
        self.manager       = ScalpV5Manager(executor)
        self.executor      = executor

        self.kws = KiteTicker(
            api_key=kite_data.api_key,
            access_token=kite_data.access_token,
        )

        self._started   = False
        self._connected = False
        self._lock      = threading.Lock()
        self._extra_tokens: set = set()

        # ── same-candle signal arbitration state (parity with V3) ──
        self._sig_arb_lock      = threading.Lock()
        self._sig_arb_candle_ts = None
        self._sig_arb_buffer: List[dict] = []
        self._sig_arb_fired: set = set()

        # ── MTM throttle (≈3s, like SCALP_V1) ──
        self._last_mtm_check_ts = 0.0

        # ── WS tick-watchdog state (zombie-socket guard) ──
        self._last_tick_ts      = 0.0
        self._last_connect_ts   = 0.0
        self._last_wd_action_ts = 0.0
        self._wd_strikes        = 0

        instruments_df = load_instruments_df()
        self._instruments_df = instruments_df

        # symbol -> token (selection resolution)
        self._symbol_token: Dict[str, int] = {}

        # -------------------------------------------------
        # INDEX TOKENS
        # -------------------------------------------------
        self.index_tokens: Dict[int, str] = {}
        index_rows = instruments_df[
            instruments_df["segment"].isin(["INDICES", "BSE-INDICES"])
        ]
        INDEX_ALLOWLIST = {
            "NIFTY 50":   "NIFTY",
            "NIFTY BANK": "BANKNIFTY",
            "SENSEX":     "SENSEX",
        }
        for _, row in index_rows.iterrows():
            ts = str(row["tradingsymbol"]).upper()
            if ts in INDEX_ALLOWLIST:
                self.index_tokens[int(row["instrument_token"])] = INDEX_ALLOWLIST[ts]
        write_audit_log(f"[V5_ENGINE][INDEX] index tokens resolved: {self.index_tokens}")

        # -------------------------------------------------
        # WEEKLY EXPIRY
        # -------------------------------------------------
        weekly_opts = instruments_df[
            (instruments_df["segment"] == "NFO-OPT")
            & (instruments_df["name"] == "NIFTY")
        ]
        today = date.today()
        valid_expiries = weekly_opts[weekly_opts["expiry"] >= today]["expiry"]
        self.current_week_expiry = (
            valid_expiries.min() if not valid_expiries.empty else None
        )
        write_audit_log(f"[V5_ENGINE] Current weekly expiry = {self.current_week_expiry}")

        # -------------------------------------------------
        # PER TOKEN STATE
        # -------------------------------------------------
        self.token_expiry: Dict[int, date] = {}
        self.builders    = {}
        self.indicators  = {}
        self.strategies  = {}

        self.condition_engine = ConditionEngineV19()

        for token in instrument_tokens:
            row = instruments_df.loc[
                instruments_df["instrument_token"] == token
            ].iloc[0]

            symbol = row["tradingsymbol"]
            self.token_expiry[token]   = row["expiry"]
            self._symbol_token[symbol] = token

            builder = CandleBuilder(
                instrument_token=token,
                timeframe_sec=timeframe_sec,
                last_candle_end_ts=None,
            )
            indicator = IndicatorEnginePineV19()
            strategy  = ScalpV5Engine(
                strategy_id=self.strategy_id,
                slot_name=str(token),
                symbol=symbol,
            )

            self.builders[token]   = builder
            self.indicators[token] = indicator
            self.strategies[token] = strategy

            self._warmup_symbol(
                symbol=symbol,
                timeframe=self.timeframe_str,
                builder=builder,
                indicator=indicator,
            )

        # -------------------------------------------------
        # STARTUP RECONCILE — clear stale OPEN V5 trades (dry-run only)
        # Runs before any tick can arrive (kws.connect not called yet).
        # dry_run=True: log STALE rows; close nothing. Flip to False after
        # inspecting [RECONCILE][V5][STALE] lines once verified.
        # -------------------------------------------------
        try:
            reconcile_stale_open_v5_trades(dry_run=True)
        except Exception as e:
            write_audit_log(
                f"[RECONCILE][V5][STALE][ERROR] startup reconcile failed: {e!r}"
            )

        # -------------------------------------------------
        # WS CALLBACKS
        # -------------------------------------------------
        self.kws.on_ticks   = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close   = self._on_close
        self.kws.on_error   = self._on_error

    # ==================================================
    # SUBSCRIBE (selection refresh)
    # ==================================================

    def subscribe_additional_tokens(self, tokens: List[int]):
        if not tokens:
            return
        for t in tokens:
            self._extra_tokens.add(t)

        def _do_subscribe():
            try:
                time.sleep(0.3)
                if not self._connected:
                    return
                self.kws.subscribe(tokens)
                self.kws.set_mode(self.kws.MODE_FULL, tokens)
                write_audit_log(f"[V5_ENGINE][WS] Additional tokens subscribed: {len(tokens)}")
            except Exception as e:
                write_audit_log(f"[V5_ENGINE][WS][ERROR] subscribe_additional_tokens: {e}")

        threading.Thread(target=_do_subscribe, daemon=True).start()

    def resolve_token(self, symbol: str) -> Optional[int]:
        if symbol in self._symbol_token:
            return self._symbol_token[symbol]
        try:
            row = self._instruments_df.loc[
                self._instruments_df["tradingsymbol"] == symbol
            ]
            if not row.empty:
                tok = int(row.iloc[0]["instrument_token"])
                self._symbol_token[symbol] = tok
                return tok
        except Exception:
            pass
        return None

    # ==================================================
    # START
    # ==================================================

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._wait_and_connect, daemon=True).start()
        threading.Thread(
            target=self._tick_watchdog, daemon=True,
            name=f"{self.strategy_id.lower()}-ws-watchdog",
        ).start()

    def _wait_and_connect(self):
        while not is_market_open():
            time.sleep(30)
        try:
            self.kws.connect(threaded=True)
        except Exception as e:
            write_audit_log(f"[V5_ENGINE][WS][FATAL] kws.connect exception: {e}")

    # ==================================================
    # WS TICK-WATCHDOG (zombie-socket guard)
    # ==================================================

    def _tick_watchdog(self):
        """
        Same zombie-socket guard as V1/V3: if connected during market hours but
        ZERO ticks for SILENT_S, drop the protocol so KiteTicker auto-reconnects.
        Fully wrapped; on any error the watchdog degrades to a no-op.
        """
        SILENT_S   = 120
        POLL_S     = 30
        COOLDOWN_S = 90
        BACKOFF_S  = 1800

        while True:
            try:
                time.sleep(POLL_S)

                if not self._connected:
                    continue
                if not is_market_open():
                    continue

                baseline = max(self._last_tick_ts, self._last_connect_ts)
                if baseline <= 0:
                    continue

                silent = time.time() - baseline
                if silent < SILENT_S:
                    continue
                if time.time() - self._last_wd_action_ts < COOLDOWN_S:
                    continue

                if self._last_tick_ts > self._last_wd_action_ts:
                    self._wd_strikes = 0
                if self._wd_strikes >= 3 and silent < BACKOFF_S:
                    continue

                write_audit_log(
                    f"[WS][WATCHDOG][{self.strategy_id}] connected but ZERO ticks "
                    f"for {int(silent)}s during market hours — forcing reconnect"
                )
                self._wd_strikes        += 1
                self._last_wd_action_ts  = time.time()
                try:
                    ws = getattr(self.kws, "ws", None)
                    if ws is not None:
                        ws.sendClose(1000, "tick-watchdog")
                    else:
                        write_audit_log(
                            f"[WS][WATCHDOG][{self.strategy_id}] no protocol object — skipped"
                        )
                except Exception as e:
                    write_audit_log(
                        f"[WS][WATCHDOG][{self.strategy_id}] drop failed: {e!r} — no action taken"
                    )
            except Exception as e:
                try:
                    write_audit_log(f"[WS][WATCHDOG][{self.strategy_id}] loop error: {e!r}")
                except Exception:
                    pass

    # ==================================================
    # WARMUP
    # ==================================================

    def _warmup_symbol(self, *, symbol, timeframe, builder, indicator):
        rows = timeline_repo.fetch_recent_candles_for_warmup(
            symbol=symbol, timeframe=timeframe, limit=self.WARMUP_CANDLES,
        )
        if not rows:
            return
        candles: List[Candle] = []
        for r in rows:
            ts = int(r["ts"])
            candles.append(
                Candle(
                    start_ts=ts, end_ts=ts + builder.tf,
                    open=float(r["open"]), high=float(r["high"]),
                    low=float(r["low"]), close=float(r["close"]),
                    source=CandleSource.WARMUP,
                )
            )
        indicator.warmup(candles, use_history=True)
        builder.last_emitted_end_ts = None

    # ==================================================
    # WS CALLBACKS
    # ==================================================

    def _on_connect(self, ws, response):
        tokens = list(self.builders.keys()) + list(self.index_tokens.keys())
        extra      = list(self._extra_tokens)
        all_tokens = tokens + [t for t in extra if t not in tokens]
        ws.subscribe(all_tokens)
        ws.set_mode(ws.MODE_FULL, all_tokens)
        write_audit_log(
            f"[V5_ENGINE][WS] Connected — subscribed {len(all_tokens)} tokens "
            f"({len(tokens)} initial + {len(extra)} extra)"
        )
        with self._lock:
            self._connected = True
            self._last_connect_ts = time.time()

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[V5_ENGINE][WS] Closed {code} {reason}")
        with self._lock:
            self._connected = False

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[V5_ENGINE][WS] Error {code} {reason}")

    # ==================================================
    # LIVE TICKS
    # ==================================================

    def _on_ticks(self, ws, ticks):
        self._last_tick_ts = time.time()
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp   = tick.get("last_price")
            if token is None or ltp is None:
                continue
            ts = int(time.time())

            # INDEX update
            if token in self.index_tokens:
                MarketIndicesState.update_ltp(self.index_tokens[token], ltp)
                continue

            if token not in self.builders:
                continue

            builder  = self.builders[token]
            strategy = self.strategies[token]
            symbol   = strategy.symbol

            if not (symbol.endswith("CE") or symbol.endswith("PE")):
                continue

            LTPStore.update(symbol, ltp)

            # ---- EXIT WATCHER: SL / TP (tick-driven) for the one open trade ----
            try:
                self._watch_exit(token, ltp, ts)
            except Exception as e:
                write_audit_log(f"[V5_ENGINE][WATCH_ERROR] {symbol} {e}")

            # ---- MTM square-off (throttled ≈3s) ----
            self._maybe_mtm_squareoff()

            builder.last_price = ltp
            candle = builder.on_tick(ltp, ts)
            if not candle:
                continue

            _timeframe_str_val = self.timeframe_str

            def write_candle_async(
                candle=candle, symbol=symbol, token=token,
                ind_engine=self.indicators[token],
                strategy=self.strategies[token],
                current_week_expiry=self.current_week_expiry,
                token_expiry=self.token_expiry.get(token),
                timeframe_str=_timeframe_str_val,
            ):
                try:
                    from app.db.sqlite import get_conn
                    conn = get_conn()

                    write_market_timeline_row(
                        candle=candle, indicators={}, conditions={}, signal=None,
                        symbol=symbol, timeframe=timeframe_str,
                        strategy_version="V1.9", mode="insert",
                    )
                    conn.commit()

                    ind_vals = ind_engine.update(candle)
                    if not ind_engine.is_ready():
                        return

                    # ── SL/TP read live from config so a mid-session UI change
                    #    takes effect on the next signal (matches V1's inline read). ──
                    cfg       = load_strategy_config(STRATEGY_ID)
                    sl_points = float(cfg.get("sl_points", 0) or 0)
                    tp_points = float(cfg.get("tp_points", 0) or 0)

                    # ── EVALUATE ENTRY FILTER ON EVERY COMPLETED CANDLE ──
                    # on_candle ALSO advances the engine's EMA8/EMA20_HIGH crossover
                    # state, so it MUST run on every completed candle (even while a
                    # trade is open) or the crossover relationship goes stale and
                    # could misfire after the next exit. Duplicate entries are
                    # prevented downstream by the manager's DB single-trade gate.
                    signal = strategy.on_candle(candle, ind_engine, sl_points, tp_points)

                    # ── CANDLE-DRIVEN EMA EXIT ──
                    # If THIS completed candle belongs to the held symbol and closes
                    # below EMA20_HIGH, exit now (EMA_EXIT). Complements the tick-
                    # driven SL/TP/MTM in _watch_exit. The held-symbol check keeps us
                    # from acting on a non-position contract's candle.
                    try:
                        held = get_open_v5_trade()
                        if (
                            held is not None
                            and int(held.get("token", -1)) == int(token)
                            and strategy.should_exit_on_candle(candle, ind_engine)
                        ):
                            write_audit_log(
                                f"[V5_ENGINE][EMA_EXIT] id={held['v5_trade_id']} "
                                f"{symbol} close={candle.close} < ema20_high="
                                f"{ind_vals.get('ema20_high')} — candle exit"
                            )
                            self.manager.close_trade(
                                v5_trade_id=held["v5_trade_id"], exit_reason="EMA_EXIT"
                            )
                    except Exception as e:
                        write_audit_log(f"[V5_ENGINE][EMA_EXIT_CANDLE_ERR] {symbol} {e}")

                    is_option = symbol.endswith("CE") or symbol.endswith("PE")

                    if (
                        signal.is_buy
                        and is_option
                        and current_week_expiry is not None
                        and token_expiry == current_week_expiry
                    ):
                        self._handle_signal(
                            symbol=symbol, token=token,
                            entry_price=signal.entry_price,
                            sl_price=signal.sl, tp_price=signal.tp,
                            entry_candle_ts=signal.entry_candle_ts,
                        )

                    write_market_timeline_row(
                        candle=candle,
                        indicators={
                            "ema8":       ind_vals["ema8"],
                            "ema20_low":  ind_vals["ema20_low"],
                            "ema20_high": ind_vals["ema20_high"],
                            "rsi_raw":    ind_vals["rsi_raw"],
                        },
                        conditions={},
                        signal="BUY" if signal.is_buy else None,
                        symbol=symbol, timeframe=timeframe_str,
                        strategy_version="V1.9", mode="update",
                    )
                except Exception as e:
                    write_audit_log(f"[V5_ENGINE][ERROR] Candle processing failed for {symbol}: {e}")

            threading.Thread(target=write_candle_async, daemon=True).start()

    # ==================================================
    # SIGNAL → (gates) → manager.open_trade
    # ==================================================

    def _handle_signal(self, *, symbol, token, entry_price, sl_price, tp_price, entry_candle_ts):
        signal_side = "CE" if symbol.endswith("CE") else "PE"

        cfg = load_strategy_config(STRATEGY_ID)

        # Cheap pre-gates (manager re-checks authoritatively).
        try:
            if not load_global_config().get("trade_on", False):
                return
        except Exception:
            pass

        primary = (cfg.get("session") or {}).get("primary") or {}
        if not is_within_session(datetime.now(), primary.get("start"), primary.get("end")):
            return

        # trade_side_mode gates the traded side (V5 trades the signalling side).
        mode = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()
        if mode in ("CE", "PE") and mode != signal_side:
            write_audit_log(
                f"[V5_ENGINE][SIDE_BLOCKED] {symbol} side={signal_side} mode={mode} — drop"
            )
            return

        # SELECTION-MEMBERSHIP GATE (parity with V1 router CE/PE_NOT_SELECTED).
        if not self._is_selected_signal(symbol, signal_side):
            write_audit_log(
                f"[V5_ENGINE][{signal_side}_NOT_SELECTED] {symbol} fired but is not in "
                f"V5's selected {signal_side} set — drop (out-of-band / non-selected)"
            )
            return

        # ── BUFFER for same-candle arbitration (parity with V3) ──
        # The gates above already dropped non-selected / wrong-side / out-of-
        # session signals. Surviving signals on the SAME candle compete; the
        # highest entry premium is elected after a short window. entry_candle_ts
        # is the 3m candle key (identical on every machine).
        self._register_signal_candidate(
            symbol=symbol, token=token, signal_side=signal_side,
            entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
            entry_candle_ts=entry_candle_ts,
        )

    def _register_signal_candidate(self, *, symbol, token, signal_side,
                                   entry_price, sl_price, tp_price, entry_candle_ts):
        """
        Collect gate-passing same-candle BUY signals; the first registrant for an
        entry_candle_ts arms a single arbitration timer. Ranking key is
        (entry_price, symbol) — both identical on every machine for the same
        closed 3m candle, so the elected winner is deterministic.

        A signal that arrives AFTER its candle was already elected is NOT dropped:
        it is routed straight to entry (the manager's DB single-trade gate decides
        whether it actually opens) — never miss a trade for the sake of uniformity.
        """
        late = False
        arm = False
        with self._sig_arb_lock:
            if entry_candle_ts in self._sig_arb_fired:
                late = True

            if not late:
                if self._sig_arb_candle_ts != entry_candle_ts:
                    self._sig_arb_candle_ts = entry_candle_ts
                    self._sig_arb_buffer = []
                self._sig_arb_buffer.append({
                    "symbol": symbol, "token": token, "side": signal_side,
                    "entry_price": float(entry_price),
                    "sl_price": sl_price, "tp_price": tp_price,
                    "entry_candle_ts": entry_candle_ts,
                })
                if len(self._sig_arb_buffer) == 1:
                    arm = True

        if late:
            write_audit_log(
                f"[V5_ENGINE][SIG_ARB_LATE] {symbol} ({signal_side}) ts={entry_candle_ts} "
                f"missed window — routing through (entering on its own gate)"
            )
            self.manager.open_trade(
                symbol=symbol, token=token, side=signal_side,
                entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
                entry_candle_ts=entry_candle_ts,
            )
            return

        if arm:
            threading.Thread(
                target=self._arbitrate_after_window,
                args=(entry_candle_ts,),
                daemon=True,
                name=f"scalpv5-sigarb-{entry_candle_ts}",
            ).start()

    def _arbitrate_after_window(self, entry_candle_ts: int):
        """Wait the collection window, elect the HIGHEST entry premium, enter it."""
        time.sleep(_SIG_ARB_WINDOW_S)

        with self._sig_arb_lock:
            if self._sig_arb_candle_ts != entry_candle_ts:
                return
            if entry_candle_ts in self._sig_arb_fired:
                return
            candidates = list(self._sig_arb_buffer)
            if not candidates:
                return
            self._sig_arb_fired.add(entry_candle_ts)
            if len(self._sig_arb_fired) > _SIG_ARB_FIRED_MAX:
                for old in sorted(self._sig_arb_fired)[:-(_SIG_ARB_FIRED_MAX // 2)]:
                    self._sig_arb_fired.discard(old)
            self._sig_arb_buffer = []

        # Elect highest entry premium; symbol string as deterministic tie-break.
        winner = max(candidates, key=lambda c: (c["entry_price"], c["symbol"]))

        if len(candidates) > 1:
            losers = ", ".join(
                f"{c['symbol']}@{c['entry_price']}" for c in candidates if c is not winner
            )
            write_audit_log(
                f"[V5_ENGINE][SIG_ARB] entry_candle_ts={entry_candle_ts} "
                f"{len(candidates)} signals → elected {winner['symbol']}@{winner['entry_price']} "
                f"(dropped: {losers})"
            )

        self.manager.open_trade(
            symbol=winner["symbol"], token=winner["token"], side=winner["side"],
            entry_price=winner["entry_price"], sl_price=winner["sl_price"],
            tp_price=winner["tp_price"], entry_candle_ts=winner["entry_candle_ts"],
        )

    def _is_selected_signal(self, symbol: str, signal_side: str) -> bool:
        """
        True iff `symbol` is one of V5's currently-selected strikes on its OWN
        side. V5 analogue of V1's SignalRouter selection gate. Conservative on
        read failure: drop the signal rather than trade a non-selected contract.
        """
        try:
            sel = load_selection(STRATEGY_ID)
        except Exception as e:
            write_audit_log(f"[V5_ENGINE][SEL_READ_ERR] {e} — dropping signal")
            return False

        rows = sel.get(signal_side, []) or []
        for r in rows:
            sym = r.get("symbol") or r.get("tradingsymbol")
            if sym and sym == symbol:
                return True
        return False

    # ==================================================
    # EXIT WATCHER — SL / TP for the single open trade
    # ==================================================

    def _watch_exit(self, token: int, ltp: float, now_ts: int):
        """
        Tick-driven exit for the one open V5 trade (LONG):
          SL : ltp <= sl_price
          TP : ltp >= tp_price
        Runs in BOTH paper and live. In live, SL/TP also have a broker GTT (per
        the manager's matrix); these checks are a uniform backstop. The EMA exit
        (candle closes below EMA20_HIGH) is candle-driven, not handled here.
        """
        try:
            row = get_open_v5_trade()
        except Exception:
            return
        if not row:
            return

        # Only act on the held symbol's own ticks for SL/TP.
        if token == int(row["token"]):
            sl = row.get("sl_price")
            tp = row.get("tp_price")
            if sl is not None and ltp <= float(sl):
                write_audit_log(
                    f"[V5_ENGINE][SL] id={row['v5_trade_id']} {row['symbol']} "
                    f"ltp={ltp} <= sl={sl} — exit"
                )
                self.manager.close_trade(v5_trade_id=row["v5_trade_id"], exit_reason="SL")
                return
            if tp is not None and ltp >= float(tp):
                write_audit_log(
                    f"[V5_ENGINE][TP] id={row['v5_trade_id']} {row['symbol']} "
                    f"ltp={ltp} >= tp={tp} — exit"
                )
                self.manager.close_trade(v5_trade_id=row["v5_trade_id"], exit_reason="TP")
                return


    # ==================================================
    # MTM SQUARE-OFF (throttled ≈3s; self-contained in the manager)
    # ==================================================

    def _maybe_mtm_squareoff(self):
        now = time.time()
        if now - self._last_mtm_check_ts < 3.0:
            return
        self._last_mtm_check_ts = now
        try:
            row = get_open_v5_trade()
            if not row:
                return
            self.manager.mtm_check(row)
        except Exception as e:
            write_audit_log(f"[V5_ENGINE][MTM_CHECK_ERR] {e}")

    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)