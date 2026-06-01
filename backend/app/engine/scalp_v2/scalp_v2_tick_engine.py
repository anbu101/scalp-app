# backend/app/engine/scalp_v2/scalp_v2_tick_engine.py
#
# SCALP_V2 — Tick Engine (v2.0 — clone of ZerodhaTickEngine + 3-leg handoff)
# ============================================================================
# This is SCALP_V1's ZerodhaTickEngine pipeline, cloned for SCALP_V2. The
# candle building, indicator computation, condition evaluation and signal
# generation are IDENTICAL to V1 — so V2's entry signals are the same as V1's.
#
# THE ONLY DIVERGENCE FROM V1:
#   When strategy.on_candle emits a SELL signal, V1 routes it through
#   SignalRouter -> TradeStateManager.on_sell_signal (single-slot entry).
#   V2 instead calls group_manager.try_enter(...) which performs the 3-leg
#   split (signal strike + ±1 strikes) and all-or-nothing exit.
#
#   Additionally, V2 forwards EVERY option tick to group_manager.on_tick(token,
#   ltp) so the group manager can detect TP/SL crosses for all-or-nothing exit.
#
# This engine is a SEPARATE instance from V1's ZerodhaTickEngine (its own
# WebSocket connection, its own builders), so SCALP_V1 / BB / HA are completely
# unaffected. It does NOT touch BB_ENGINE_REGISTRY / HA_ENGINE_REGISTRY — those
# remain driven by V1's engine. (V2 watches its own option universe only.)
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


STRATEGY_ID = "SCALP_V2"


def _timeframe_str(timeframe_sec: int) -> str:
    minutes = timeframe_sec // 60
    if minutes > 0:
        return f"{minutes}m"
    return f"{timeframe_sec}s"


class ScalpV2TickEngine:
    """
    SCALP_V2 WebSocket tick engine. Mirrors ZerodhaTickEngine for the candle/
    signal pipeline, but hands a finished SELL signal to the group manager's
    3-leg try_enter() and forwards ticks to on_tick() for all-or-nothing exit.

    The group manager is injected (built by the selection loop) so this engine
    stays a thin clone of V1's pipeline.
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        kite_data: KiteConnect,
        instrument_tokens: List[int],
        group_manager,
        timeframe_sec: int = 60,
    ):
        self.strategy_id   = STRATEGY_ID
        self.group_manager = group_manager
        self.kite_data     = kite_data
        self.timeframe_sec = timeframe_sec
        self.timeframe_str = _timeframe_str(timeframe_sec)

        self.kws = KiteTicker(
            api_key=kite_data.api_key,
            access_token=kite_data.access_token,
        )

        self._started   = False
        self._connected = False
        self._lock      = threading.Lock()
        self._extra_tokens: set = set()

        # Last candle close per symbol — feeds the group manager's E1 fallback
        # (sibling pricing when no fresh LTPStore tick is available).
        self._last_close: Dict[str, float] = {}
        # symbol -> token, so siblings (resolved by symbol) can be priced.
        self._symbol_token: Dict[str, int] = {}

        instruments_df = load_instruments_df()
        self._instruments_df = instruments_df

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
        write_audit_log(f"[V2_ENGINE][INDEX] index tokens resolved: {self.index_tokens}")

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
        write_audit_log(f"[V2_ENGINE] Current weekly expiry = {self.current_week_expiry}")

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
            self.token_expiry[token] = row["expiry"]
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
    # PROVIDERS (consumed by the group manager)
    # ==================================================

    def candle_provider(self, symbol: str) -> Optional[float]:
        """Last candle close for a symbol — group manager E1 sibling-pricing fallback."""
        return self._last_close.get(symbol)

    def candidate_provider(self, symbol: str) -> Optional[int]:
        """Resolve token for a symbol (siblings + signal). Engine map first, then df."""
        if symbol in self._symbol_token:
            return self._symbol_token[symbol]
        try:
            row = self._instruments_df.loc[
                self._instruments_df["tradingsymbol"] == symbol
            ]
            if not row.empty:
                return int(row.iloc[0]["instrument_token"])
        except Exception:
            pass
        return None

    def instrument_provider(self, symbol: str = None, strike: int = None,
                            opt_type: str = None, expiry=None) -> Optional[dict]:
        """
        Resolve a full instrument record either by symbol, or by
        (strike, opt_type, expiry). Returns a dict with tradingsymbol /
        instrument_token / strike / type / expiry, or None.
        """
        df = self._instruments_df
        try:
            if symbol is not None:
                row = df.loc[df["tradingsymbol"] == symbol]
                if row.empty:
                    return None
                r = row.iloc[0]
                return {
                    "tradingsymbol":    r["tradingsymbol"],
                    "instrument_token": int(r["instrument_token"]),
                    "strike":           int(r["strike"]),
                    "type":             r.get("instrument_type", opt_type)
                                        or (r["tradingsymbol"][-2:]),
                    "expiry":           r["expiry"],
                }
            # by strike + type + expiry
            sub = df[
                (df["segment"] == "NFO-OPT")
                & (df["name"] == "NIFTY")
                & (df["strike"] == strike)
                & (df["instrument_type"] == opt_type)
            ]
            if expiry is not None:
                sub = sub[sub["expiry"] == expiry]
            if sub.empty:
                return None
            r = sub.iloc[0]
            return {
                "tradingsymbol":    r["tradingsymbol"],
                "instrument_token": int(r["instrument_token"]),
                "strike":           int(r["strike"]),
                "type":             r["instrument_type"],
                "expiry":           r["expiry"],
            }
        except Exception as e:
            write_audit_log(f"[V2_ENGINE][INSTRUMENT_RESOLVE_FAIL] {symbol or (strike, opt_type)} ERR={e}")
            return None

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
                write_audit_log(f"[V2_ENGINE][WS] Additional tokens subscribed: {len(tokens)}")
            except Exception as e:
                write_audit_log(f"[V2_ENGINE][WS][ERROR] subscribe_additional_tokens: {e}")

        threading.Thread(target=_do_subscribe, daemon=True).start()

    # ==================================================
    # START
    # ==================================================

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._wait_and_connect, daemon=True).start()

    def _wait_and_connect(self):
        while not is_market_open():
            time.sleep(30)
        try:
            self.kws.connect(threaded=True)
        except Exception as e:
            write_audit_log(f"[V2_ENGINE][WS][FATAL] kws.connect exception: {e}")

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
            f"[V2_ENGINE][WS] Connected — subscribed {len(all_tokens)} tokens "
            f"({len(tokens)} initial + {len(extra)} extra)"
        )
        with self._lock:
            self._connected = True

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[V2_ENGINE][WS] Closed {code} {reason}")
        with self._lock:
            self._connected = False

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[V2_ENGINE][WS] Error {code} {reason}")

    # ==================================================
    # LIVE TICKS
    # ==================================================

    def _on_ticks(self, ws, ticks):
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

            # ---- ALL-OR-NOTHING EXIT: forward every option tick to the group ----
            try:
                self.group_manager.on_tick(token, ltp)
            except Exception as e:
                write_audit_log(f"[V2_ENGINE][ON_TICK_ERROR] {symbol} {e}")

            builder.last_price = ltp
            candle = builder.on_tick(ltp, ts)
            if not candle:
                continue

            # Track last candle close for the group manager's E1 fallback.
            self._last_close[symbol] = float(candle.close)

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

                    conditions = self.condition_engine.evaluate(
                        candle=candle, indicators=ind_vals,
                        is_trading_time=True,
                        no_open_trade=not strategy.in_trade,
                    )

                    signal = strategy.on_candle(candle, ind_engine, conditions)

                    is_option = symbol.endswith("CE") or symbol.endswith("PE")

                    # ---- DIVERGENCE FROM V1: hand SELL signal to the 3-leg group manager ----
                    if (
                        signal.is_sell
                        and is_option
                        and current_week_expiry is not None
                        and token_expiry == current_week_expiry
                    ):
                        write_audit_log(
                            f"[V2_ENGINE][SELL_SIGNAL] {symbol} entry={signal.entry_price} "
                            f"sl={signal.sl} tp={signal.tp} → try_enter"
                        )
                        try:
                            self.group_manager.try_enter(
                                symbol=symbol,
                                token=token,
                                entry_price=signal.entry_price,
                                sl_price=signal.sl,
                                tp_price=signal.tp,
                                candle_ts=candle.end_ts,
                            )
                        except Exception as e:
                            write_audit_log(f"[V2_ENGINE][TRY_ENTER_ERROR] {symbol} {e}")

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
                    write_audit_log(f"[V2_ENGINE][ERROR] Candle processing failed for {symbol}: {e}")

            threading.Thread(target=write_candle_async, daemon=True).start()

    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)