# backend/app.engine.scalp_v4.scalp_v4_tick_engine.py
#
# SCALP_V4 — Tick Engine (TEST option-BUYING hedge clone of SCALP_V1).
# ============================================================================
# Structural twin of ScalpV2TickEngine: OWNS its own KiteTicker, builders,
# warmup, index/expiry resolution. SEPARATE WebSocket from V1/BB/HA/V2 — it
# does NOT touch BB_ENGINE_REGISTRY / HA_ENGINE_REGISTRY / the shared
# ZerodhaTickEngine. SCALP_V1 / BB / HA / V2 are completely unaffected.
#
# The candle → indicator → condition → signal pipeline is IDENTICAL to V1/V2,
# so V4's ENTRY signals are the same as V1's.
#
# DIVERGENCE FROM V1/V2 (the whole point of V4):
#   The contract that FIRES the signal (e.g. 24500CE) is the "signal" contract:
#   TRACKED for its own SL/TP but NEVER traded. On a SELL signal, V4 BUYS the
#   highest-premium OPPOSITE-side selected option (e.g. 24450PE) — the "hedge".
#   The hedge is protected by an SL-only GTT at (hedge_fill - max_sl_points) and
#   exits when EITHER the signal contract hits its SL/TP (watched here, tick-
#   wise) OR the hedge's own SL fires (broker GTT live / this watcher in paper).
#
# This engine forwards EVERY option tick to _watch_exit (mirrors V2 forwarding
# to group_manager.on_tick), then builds candles and, on a SELL signal, pairs a
# hedge and calls manager.open_hedge_trade.
#
# SAME-CANDLE SIGNAL ARBITRATION (uniformity across machines):
#   When >1 selected signal contract fires on the SAME candle, the tracked
#   signal (and therefore SL/TP and exit timing) must be IDENTICAL on every
#   friend's machine, even when the hedge is the same. Previously whichever
#   write_candle_async thread reached open_hedge_trade first won the DB single-
#   trade gate — nondeterministic across machines. Fix: buffer gate-passing
#   same-candle signals, wait a short window, then elect the HIGHEST signal
#   premium (entry_price, the closed-candle premium — identical everywhere) with
#   the symbol string as a stable tie-break. Only the elected winner is paired
#   with a hedge and entered. Hedge pairing also moves here, so a losing signal
#   never triggers a hedge price lookup.
#
# no_open_trade is ALWAYS True for the condition engine: V4's single-trade gate
# is DB-backed in the manager (scalp_v4_trades), NOT StrategyEngine.in_trade —
# StrategyEngine queries SCALP_V4 in trades/paper_trades, which V4 never writes,
# so its self-managed in_trade would be permanently wrong for V4. (This is a
# justified divergence from V2, whose group manager DOES write those tables.)
#
# SELECTION-MEMBERSHIP GATE (parity with V1's SignalRouter):
#   The candle pipeline evaluates the ENTIRE subscribed universe (~132 symbols),
#   so SELL signals fire for many contracts OUTSIDE V4's selected 2-CE / 2-PE
#   premium-band set. V1 rejects non-selected signals in SignalRouter
#   (CE_NOT_SELECTED / PE_NOT_SELECTED → EXIT). V4 has no router, so it gates in
#   _handle_signal: the SIGNAL contract MUST be one of V4's currently-selected
#   strikes on its own side, or the signal is dropped.
# ============================================================================

from typing import Dict, List, Optional
import time
from datetime import date
import threading

from kiteconnect import KiteTicker, KiteConnect

from app.candles.candle_builder import CandleBuilder
from app.marketdata.candle import Candle, CandleSource
from app.marketdata.ltp_store import LTPStore

from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.strategy_engine import StrategyEngine
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
from datetime import datetime

from app.engine.scalp_v4.scalp_v4_manager import ScalpV4Manager
from app.db.scalp_v4_repo import get_open_v4_trade


STRATEGY_ID = "SCALP_V4"

# Same-candle signal arbitration tuning (see header). 0.4s collection window
# from the first same-candle candidate; fired-set bounded over a session.
_SIG_ARB_WINDOW_S  = 0.4
_SIG_ARB_FIRED_MAX = 512


def _timeframe_str(timeframe_sec: int) -> str:
    minutes = timeframe_sec // 60
    return f"{minutes}m" if minutes > 0 else f"{timeframe_sec}s"


class ScalpV4TickEngine:
    """
    SCALP_V4 WebSocket tick engine. Owns its own KiteTicker. Built by the
    selection loop with the shared data-kite, the option-universe tokens, and
    the execution router. Constructs its own ScalpV4Manager.
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        kite_data: KiteConnect,
        instrument_tokens: List[int],
        executor,
        timeframe_sec: int = 60,
    ):
        self.strategy_id   = STRATEGY_ID
        self.kite_data     = kite_data
        self.timeframe_sec = timeframe_sec
        self.timeframe_str = _timeframe_str(timeframe_sec)
        self.manager       = ScalpV4Manager(executor)
        self.executor      = executor

        self.kws = KiteTicker(
            api_key=kite_data.api_key,
            access_token=kite_data.access_token,
        )

        self._started   = False
        self._connected = False
        self._lock      = threading.Lock()
        self._extra_tokens: set = set()

        # ── same-candle signal arbitration state ──
        self._sig_arb_lock      = threading.Lock()
        self._sig_arb_candle_ts = None
        self._sig_arb_buffer: List[dict] = []
        self._sig_arb_fired: set = set()

        # ── WS tick-watchdog state (zombie-socket guard) ──
        self._last_tick_ts      = 0.0   # set once per _on_ticks batch
        self._last_connect_ts   = 0.0   # set in _on_connect
        self._last_wd_action_ts = 0.0   # last forced-reconnect attempt
        self._wd_strikes        = 0     # consecutive no-tick reconnect attempts

        instruments_df = load_instruments_df()
        self._instruments_df = instruments_df

        # symbol -> token (for hedge pairing + selection resolution)
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
        write_audit_log(f"[V4_ENGINE][INDEX] index tokens resolved: {self.index_tokens}")

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
        write_audit_log(f"[V4_ENGINE] Current weekly expiry = {self.current_week_expiry}")

        # -------------------------------------------------
        # PER TOKEN STATE
        # -------------------------------------------------
        self.token_expiry: Dict[int, date] = {}
        self.builders    = {}
        self.indicators  = {}
        self.strategies  = {}

        self.condition_engine = ConditionEngineV19()

        # ── NEAR-ATM WARMUP BACKFILL (fail-open; never blocks warmup/signals) ──
        # Ensure the near-ATM band has identical historical candles across
        # machines so EMA seeds match (fixes the 09:35-vs-09:37 divergence from
        # a machine that wasn't running yesterday). ANY failure here is logged
        # and ignored — the per-token warmup loop below runs regardless, giving
        # exactly today's behavior if the backfill does nothing.
        try:
            from app.engine.scalp_common.warmup_backfill import run_near_atm_backfill
            run_near_atm_backfill(
                include_today=True,   # heal mid-session-restart holes (2026-07-15)
                kite_data=self.kite_data,
                instruments_df=self._instruments_df,
                option_tokens=list(self.builders.keys()) if self.builders else list(instrument_tokens),
                current_week_expiry=self.current_week_expiry,
                spot_ltp=None,   # ATM derived from universe median (no spot tick needed at startup)
            )
        except Exception as e:
            write_audit_log(f"[V4_ENGINE][WARMUP_BF_SKIP] {e!r} — proceeding with normal warmup")

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
            strategy  = StrategyEngine(
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
        # WS CALLBACKS
        # -------------------------------------------------
        self.kws.on_ticks   = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close   = self._on_close
        self.kws.on_error   = self._on_error

    # ==================================================
    # SUBSCRIBE (selection refresh — same shape as V2)
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
                write_audit_log(f"[V4_ENGINE][WS] Additional tokens subscribed: {len(tokens)}")
            except Exception as e:
                write_audit_log(f"[V4_ENGINE][WS][ERROR] subscribe_additional_tokens: {e}")

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
            write_audit_log(f"[V4_ENGINE][WS][FATAL] kws.connect exception: {e}")


    # ==================================================
    # WS TICK-WATCHDOG (zombie-socket guard)
    # ==================================================

    def _tick_watchdog(self):
        """
        Guards against the zombie-socket failure observed live 2026-06-11:
        V2's WS reported Connected at 09:15:22 but delivered ZERO ticks until
        the dead TCP flow was finally dropped at 12:02:45 (code 1006), after
        which KiteTicker's auto-reconnect healed it in 2 seconds. KiteTicker
        raises NO event while the socket is dead, so the only reliable
        detector is "no _on_ticks callback for a long time during market
        hours". Action: drop the protocol (NOT kws.close(), which calls
        stop_retry() and would disable the auto-reconnect we rely on).
        Fully wrapped — nothing here can affect the tick path; on any error
        the watchdog degrades to a no-op.
        """
        SILENT_S   = 120    # market open + connected + 0 ticks this long → act
        POLL_S     = 30
        COOLDOWN_S = 90     # min gap between actions (covers on_close lag)
        BACKOFF_S  = 1800   # after 3 fruitless drops, slow to one per 30 min

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

                # Back-off: ticks arrived since our last action → reset strikes;
                # otherwise repeated drops aren't helping (e.g. holiday) → slow down.
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
            f"[V4_ENGINE][WS] Connected — subscribed {len(all_tokens)} tokens "
            f"({len(tokens)} initial + {len(extra)} extra)"
        )
        with self._lock:
            self._connected = True
            self._last_connect_ts = time.time()

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[V4_ENGINE][WS] Closed {code} {reason}")
        with self._lock:
            self._connected = False

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[V4_ENGINE][WS] Error {code} {reason}")

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

            # ---- EXIT WATCHER: forward every option tick (mirrors V2 on_tick) ----
            try:
                self._watch_exit(token, ltp)
            except Exception as e:
                write_audit_log(f"[V4_ENGINE][WATCH_ERROR] {symbol} {e}")

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

                    # no_open_trade ALWAYS True — V4 gate is DB-backed in manager.
                    conditions = self.condition_engine.evaluate(
                        candle=candle, indicators=ind_vals,
                        is_trading_time=True,
                        no_open_trade=True,
                    )

                    signal = strategy.on_candle(candle, ind_engine, conditions)

                    # ── SCALP_V4_EXTRA_GATE BEGIN ─────────────────────────────
                    # V4 = V3 + ONE extra entry rule: EMA8 must NOT be above
                    # EMA20_High. The shared ConditionEngineV19 / StrategyEngine
                    # are NOT modified (they are shared with V1/V2/V3/BB), so the
                    # extra rule is applied here as a post-signal VETO on the SELL
                    # entry only. This is the ONLY logic difference between the V3
                    # and V4 tick engines. Strict ">": ema8 == ema20_high passes
                    # (mirrors the old inclusive "close <= EMA20_High" semantics).
                    # Exits are untouched — Signal.is_exit / is_sell each gate a
                    # different path and we only neutralise the entry side.
                    if signal.is_sell:
                        _ema8       = ind_vals.get("ema8")
                        _ema20_high = ind_vals.get("ema20_high")
                        if (
                            _ema8 is not None
                            and _ema20_high is not None
                            and _ema8 > _ema20_high
                        ):
                            write_audit_log(
                                f"[V4_ENGINE][EXTRA_GATE_BLOCK] {symbol} "
                                f"ema8={_ema8} > ema20_high={_ema20_high} "
                                f"— V4 vetoes V3 SELL signal (no entry)"
                            )
                            signal.is_sell     = False
                            signal.entry_price = None
                            signal.sl          = None
                            signal.tp          = None
                    # ── SCALP_V4_EXTRA_GATE END ───────────────────────────────

                    is_option = symbol.endswith("CE") or symbol.endswith("PE")

                    # ---- DIVERGENCE: SELL signal → buy OPPOSITE-side hedge ----
                    if (
                        signal.is_sell
                        and is_option
                        and current_week_expiry is not None
                        and token_expiry == current_week_expiry
                    ):
                        self._handle_signal(
                            symbol=symbol, token=token,
                            entry_price=signal.entry_price,
                            sl_price=signal.sl, tp_price=signal.tp,
                            candle_ts=candle.end_ts,
                        )

                    write_market_timeline_row(
                        candle=candle,
                        indicators={
                            "ema8":       ind_vals["ema8"],
                            "ema20_low":  ind_vals["ema20_low"],
                            "ema20_high": ind_vals["ema20_high"],
                            "rsi_raw":    ind_vals["rsi_raw"],
                        },
                        conditions=conditions,
                        signal="SELL" if signal.is_sell else None,
                        symbol=symbol, timeframe=timeframe_str,
                        strategy_version="V1.9", mode="update",
                    )
                except Exception as e:
                    write_audit_log(f"[V4_ENGINE][ERROR] Candle processing failed for {symbol}: {e}")

            threading.Thread(target=write_candle_async, daemon=True).start()

    # ==================================================
    # SIGNAL → (gate) → BUFFER → elect → HEDGE PAIRING → entry
    # ==================================================

    def _handle_signal(self, *, symbol, token, entry_price, sl_price, tp_price, candle_ts):
        """
        Per-candidate gates run synchronously (UNCHANGED). A surviving signal is
        BUFFERED for its candle_ts; after a short window the highest-premium
        signal is elected (see header) and ONLY that one is paired with a hedge
        and entered. Hedge pairing happens for the winner only — losing signals
        never trigger a hedge price lookup.
        """
        signal_side = "CE" if symbol.endswith("CE") else "PE"

        cfg  = load_strategy_config(STRATEGY_ID)

        # ── CHEAP PRE-GATE (before any pairing / price work) ──
        # The manager re-checks these authoritatively, but checking here first
        # means a pre-session or trade-off signal does NOT enter the buffer.
        try:
            if not load_global_config().get("trade_on", False):
                return
        except Exception:
            pass

        primary = (cfg.get("session") or {}).get("primary") or {}
        if not is_within_session(datetime.now(), primary.get("start"), primary.get("end")):
            # Silent: this fires every candle before the window opens; logging
            # it per-candle would itself be spam. The manager logs the SKIP if a
            # signal ever slips through to it.
            return

        # trade_side_mode gates the SIGNAL side.
        mode = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()
        if mode in ("CE", "PE") and mode != signal_side:
            write_audit_log(
                f"[V4_ENGINE][SIDE_BLOCKED] {symbol} side={signal_side} mode={mode} — drop"
            )
            return

        # ── SELECTION-MEMBERSHIP GATE (parity with V1's router CE/PE_NOT_SELECTED) ──
        # The candle pipeline evaluates the ENTIRE subscribed universe (~132
        # symbols), so SELL signals fire for many contracts OUTSIDE V4's selected
        # 2-CE / 2-PE premium-band set. V1 rejects these in SignalRouter
        # (CE_NOT_SELECTED → EXIT); V4 has no router, so it MUST gate here, or it
        # trades out-of-band contracts (e.g. an 819-premium CE when the band is
        # 150–200) and diverges from V1. The SIGNAL contract must be one of V4's
        # currently-selected strikes on its own side.
        if not self._is_selected_signal(symbol, signal_side):
            write_audit_log(
                f"[V4_ENGINE][{signal_side}_NOT_SELECTED] {symbol} fired but is not in "
                f"V4's selected {signal_side} set — drop (out-of-band / non-selected)"
            )
            return

        # ── BUFFER for same-candle arbitration ──
        self._register_signal_candidate(
            symbol=symbol, token=token, signal_side=signal_side,
            entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
            candle_ts=candle_ts,
        )

    def _register_signal_candidate(self, *, symbol, token, signal_side,
                                   entry_price, sl_price, tp_price, candle_ts):
        """
        Collect gate-passing same-candle signals; the first registrant for a
        candle_ts arms a single arbitration timer. Determinism: ranking key is
        (entry_price, symbol), both identical on every machine for the same
        closed candle.
        """
        late = False
        arm = False
        with self._sig_arb_lock:
            # Already elected for this candle → this signal missed the window.
            # DO NOT drop it: route it through to hedge-pair + entry (outside the
            # lock). The manager's DB single-trade gate decides whether it
            # actually enters — never miss a trade for the sake of uniformity.
            if candle_ts in self._sig_arb_fired:
                late = True

            if not late:
                if self._sig_arb_candle_ts != candle_ts:
                    self._sig_arb_candle_ts = candle_ts
                    self._sig_arb_buffer = []
                self._sig_arb_buffer.append({
                    "symbol": symbol, "token": token, "side": signal_side,
                    "entry_price": float(entry_price),
                    "sl_price": sl_price, "tp_price": tp_price,
                })
                if len(self._sig_arb_buffer) == 1:
                    arm = True

        if late:
            write_audit_log(
                f"[V4_ENGINE][SIG_ARB_LATE] {symbol} ({signal_side}) ts={candle_ts} "
                f"missed window — routing through (entering on its own gate)"
            )
            self._pair_and_enter(
                symbol=symbol, token=token, signal_side=signal_side,
                entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
                candle_ts=candle_ts,
            )
            return

        if arm:
            threading.Thread(
                target=self._arbitrate_after_window,
                args=(candle_ts,),
                daemon=True,
                name=f"scalp-v4-sigarb-{candle_ts}",
            ).start()

    def _arbitrate_after_window(self, candle_ts: int):
        """Wait the collection window, elect the highest-premium signal, enter it."""
        time.sleep(_SIG_ARB_WINDOW_S)

        with self._sig_arb_lock:
            if self._sig_arb_candle_ts != candle_ts:
                return
            if candle_ts in self._sig_arb_fired:
                return
            candidates = list(self._sig_arb_buffer)
            if not candidates:
                return
            self._sig_arb_fired.add(candle_ts)
            if len(self._sig_arb_fired) > _SIG_ARB_FIRED_MAX:
                for old in sorted(self._sig_arb_fired)[:-(_SIG_ARB_FIRED_MAX // 2)]:
                    self._sig_arb_fired.discard(old)
            self._sig_arb_buffer = []

        winner = max(candidates, key=lambda c: (c["entry_price"], c["symbol"]))

        if len(candidates) > 1:
            losers = ", ".join(
                f"{c['symbol']}@{c['entry_price']}" for c in candidates if c is not winner
            )
            write_audit_log(
                f"[V4_ENGINE][SIG_ARB] candle_ts={candle_ts} {len(candidates)} signals "
                f"→ elected {winner['symbol']}@{winner['entry_price']} (dropped: {losers})"
            )

        # Pair hedge + enter for the elected signal only.
        self._pair_and_enter(
            symbol=winner["symbol"], token=winner["token"], signal_side=winner["side"],
            entry_price=winner["entry_price"], sl_price=winner["sl_price"],
            tp_price=winner["tp_price"], candle_ts=candle_ts,
        )

    def _pair_and_enter(self, *, symbol, token, signal_side,
                        entry_price, sl_price, tp_price, candle_ts):
        """
        Shared post-decision entry: pair the highest-premium opposite-side hedge
        and call the manager. Used by both the elected winner and a late
        straggler that missed the arbitration window. The manager's DB single-
        trade gate is the authoritative entry gate.
        """
        hedge = self._pick_hedge(opposite_of=signal_side)
        if hedge is None:
            write_audit_log(
                f"[V4_ENGINE][NO_HEDGE] {symbol} but no "
                f"{'PE' if signal_side == 'CE' else 'CE'} selection — skipping (per spec)"
            )
            return

        write_audit_log(
            f"[V4_ENGINE][SELL_SIGNAL] {symbol} ({signal_side}) "
            f"entry={entry_price} sl={sl_price} tp={tp_price} "
            f"→ BUY hedge {hedge['symbol']} ({hedge['side']}, premium≈{hedge['premium']})"
        )

        self.manager.open_hedge_trade(
            signal_symbol=symbol,
            signal_token=token,
            signal_side=signal_side,
            signal_entry_price=entry_price,
            signal_sl=sl_price,
            signal_tp=tp_price,
            signal_candle_ts=candle_ts,
            hedge_symbol=hedge["symbol"],
            hedge_token=hedge["token"],
            hedge_side=hedge["side"],
        )

    def _is_selected_signal(self, symbol: str, signal_side: str) -> bool:
        """
        True iff `symbol` is one of V4's currently-selected strikes on its OWN
        side. This is the V4 analogue of V1's SignalRouter selection gate
        (CE_NOT_SELECTED / PE_NOT_SELECTED). Reads the selection with the same
        structure _pick_hedge uses, so signal-side membership is checked exactly
        as hedge candidates are sourced.

        Conservative on read failure: if the selection can't be read, return
        False (drop the signal) rather than risk trading a non-selected contract.
        """
        try:
            sel = load_selection(STRATEGY_ID)
        except Exception as e:
            write_audit_log(f"[V4_ENGINE][SEL_READ_ERR] {e} — dropping signal")
            return False

        rows = sel.get(signal_side, []) or []
        for r in rows:
            sym = r.get("symbol") or r.get("tradingsymbol")
            if sym and sym == symbol:
                return True
        return False

    def _pick_hedge(self, *, opposite_of: str) -> Optional[Dict]:
        """
        Highest-premium opposite-side selected strike.

        Price source order (avoids REST rate-limit storms):
          1. LTPStore  — the WS tick for this symbol. V4's own ticker already
             subscribes every selected strike, so this is fresh and free.
          2. selection-file `ltp` — the premium captured at selection time.
          3. REST resolve_ltp — last resort only, and only if BOTH above are
             missing for a candidate (rare). REST here previously fired once per
             candidate per candle and tripped Kite's "Too many requests".
        """
        hedge_side = "PE" if opposite_of == "CE" else "CE"

        try:
            sel = load_selection(STRATEGY_ID)
        except Exception as e:
            write_audit_log(f"[V4_ENGINE][PICK_HEDGE_READ_ERR] {e}")
            return None

        rows = sel.get(hedge_side, [])
        if not rows:
            return None

        best = None
        for r in rows:
            sym = r.get("symbol") or r.get("tradingsymbol")
            if not sym:
                continue
            tok = self.resolve_token(sym)
            if tok is None:
                continue

            # 1) WS tick (free, fresh) → 2) selection-time ltp → 3) REST (rare)
            premium = LTPStore.get(sym)
            if not premium or premium <= 0:
                premium = float(r.get("ltp") or 0.0)
            if not premium or premium <= 0:
                try:
                    premium = self.executor.resolve_ltp(sym)
                except Exception:
                    premium = None

            cand = {"symbol": sym, "token": tok, "side": hedge_side,
                    "premium": float(premium or 0.0)}
            if best is None or cand["premium"] > best["premium"]:
                best = cand

        if best is None or best["premium"] <= 0:
            return None
        return best

    # ==================================================
    # EXIT WATCHER — signal SL/TP (both modes) + hedge SL (paper)
    # ==================================================

    def _watch_exit(self, token: int, ltp: float):
        try:
            row = get_open_v4_trade()
        except Exception:
            return
        if not row:
            return

        v4_id = row["v4_trade_id"]
        paper = bool(row.get("paper"))

        # Signal contract tick → its own SL/TP (SCALP_V1 SHORT semantics on the
        # SIGNAL premium): SL ABOVE entry (ltp >= sl), TP BELOW entry (ltp <= tp).
        if token == int(row["signal_token"]):
            sig_sl = row.get("signal_sl")
            sig_tp = row.get("signal_tp")
            if sig_sl is not None and ltp >= float(sig_sl):
                write_audit_log(
                    f"[V4_ENGINE][SIG_SL] id={v4_id} signal={row['signal_symbol']} "
                    f"ltp={ltp} >= sl={sig_sl} — exit hedge"
                )
                self.manager.close_hedge_trade(v4_trade_id=v4_id, exit_reason="SIG_SL")
                return
            if sig_tp is not None and ltp <= float(sig_tp):
                write_audit_log(
                    f"[V4_ENGINE][SIG_TP] id={v4_id} signal={row['signal_symbol']} "
                    f"ltp={ltp} <= tp={sig_tp} — exit hedge"
                )
                self.manager.close_hedge_trade(v4_trade_id=v4_id, exit_reason="SIG_TP")
                return

        # Hedge contract tick → PAPER own-SL (live uses the broker GTT).
        if paper and token == int(row["hedge_token"]):
            hedge_sl = row.get("hedge_sl")
            if hedge_sl is not None and ltp <= float(hedge_sl):
                write_audit_log(
                    f"[V4_ENGINE][HEDGE_SL][PAPER] id={v4_id} hedge={row['hedge_symbol']} "
                    f"ltp={ltp} <= hedge_sl={hedge_sl} — exit hedge"
                )
                self.manager.close_hedge_trade(v4_trade_id=v4_id, exit_reason="HEDGE_SL")
                return

    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)