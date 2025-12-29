from typing import Dict, List
import time
from datetime import date
import math
import threading   # ✅ ADDED

from kiteconnect import KiteTicker

from app.candles.candle_builder import CandleBuilder
from app.marketdata.candle import Candle, CandleSource
from app.marketdata.ltp_store import LTPStore

from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
from app.engine.strategy_engine import StrategyEngine
from app.engine.condition_engine_v1_9 import ConditionEngineV19

from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df
from app.db.timeline_repo import fetch_recent_candles_for_warmup
from app.persistence.market_timeline_writer import write_market_timeline_row
from app.engine.signal_router import signal_router


class ZerodhaTickEngine:
    """
    Zerodha WebSocket Engine (AUTHORITATIVE)

    RESPONSIBILITIES:
    ✅ Build 1M candles
    ✅ Update indicators (ALL expiries)
    ✅ Persist market_timeline (ALL expiries)
    ✅ Run strategy logic
    ✅ Route BUY signals (CURRENT WEEK ONLY)

    ❌ No REST candles
    ❌ No execution logic
    """

    WARMUP_CANDLES = 500

    # ✅ NEW (SAFE CONSTANTS)
    STALE_TICK_SEC = 20
    RECONNECT_DELAY_SEC = 3

    def __init__(
        self,
        api_key: str,
        access_token: str,
        instrument_tokens: List[int],
        exchange: str = "NFO",
        timeframe_sec: int = 60,
    ):
        self.api_key = api_key
        self.access_token = access_token

        # 🔒 ORIGINAL BEHAVIOR PRESERVED
        self.kws = KiteTicker(api_key, access_token)

        # ✅ NEW STATE (NO LOGIC CHANGE)
        self._last_tick_ts = 0
        self._connected = False
        self._reconnecting = False

        instruments_df = load_instruments_df()

        # -------------------------------------------------
        # 🔒 CURRENT WEEK EXPIRY (AUTHORITATIVE)
        # -------------------------------------------------
        weekly_opts = instruments_df[
            (instruments_df["segment"] == "NFO-OPT") &
            (instruments_df["name"] == "NIFTY")
        ]

        self.current_week_expiry = weekly_opts["expiry"].min()

        if self.current_week_expiry is None or (
            isinstance(self.current_week_expiry, float)
            and math.isnan(self.current_week_expiry)
        ):
            write_audit_log(
                "[ENGINE][WARN] Weekly expiry unresolved — BUY routing disabled"
            )
            self.current_week_expiry = None
        else:
            write_audit_log(
                f"[ENGINE] Current weekly expiry = {self.current_week_expiry}"
            )

        self.token_expiry: Dict[int, date] = {}
        self.builders: Dict[int, CandleBuilder] = {}
        self.indicators: Dict[int, IndicatorEnginePineV19] = {}
        self.strategies: Dict[int, StrategyEngine] = {}

        self.condition_engine = ConditionEngineV19()

        # -------------------------------------------------
        # INIT PER TOKEN (UNCHANGED)
        # -------------------------------------------------
        for token in instrument_tokens:
            row = instruments_df.loc[
                instruments_df["instrument_token"] == token
            ].iloc[0]

            symbol = row["tradingsymbol"]
            expiry = row["expiry"]

            self.token_expiry[token] = expiry

            builder = CandleBuilder(
                instrument_token=token,
                timeframe_sec=timeframe_sec,
                last_candle_end_ts=None,
            )

            indicator = IndicatorEnginePineV19()
            strategy = StrategyEngine(
                slot_name=str(token),
                symbol=symbol,
            )

            self.builders[token] = builder
            self.indicators[token] = indicator
            self.strategies[token] = strategy

            self._warmup_symbol(
                symbol=symbol,
                timeframe="1m",
                builder=builder,
                indicator=indicator,
            )

        # -------------------------------------------------
        # WS CALLBACKS (UNCHANGED NAMES)
        # -------------------------------------------------
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

        # ✅ NEW: background health monitor (NON-BLOCKING)
        threading.Thread(
            target=self._health_loop,
            daemon=True,
        ).start()

    # -------------------------------------------------
    # WARMUP (UNCHANGED)
    # -------------------------------------------------

    def _warmup_symbol(
        self,
        *,
        symbol: str,
        timeframe: str,
        builder: CandleBuilder,
        indicator: IndicatorEnginePineV19,
    ):
        rows = fetch_recent_candles_for_warmup(
            symbol=symbol,
            timeframe=timeframe,
            limit=self.WARMUP_CANDLES,
        )

        if not rows:
            write_audit_log(f"[WARMUP] {symbol} no historical candles")
            return

        candles: List[Candle] = []

        for row in rows:
            ts = int(row["ts"])
            candles.append(
                Candle(
                    start_ts=ts,
                    end_ts=ts + builder.tf,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    source=CandleSource.WARMUP,
                )
            )

        indicator.warmup(candles, use_history=True)
        builder.last_emitted_end_ts = None

        write_audit_log(
            f"[WARMUP] {symbol} aligned with {len(candles)} DB candles"
        )

    # -------------------------------------------------
    # WS lifecycle (MINIMAL ADDITIONS)
    # -------------------------------------------------

    def start(self):
        write_audit_log(
            f"[WS] Starting Zerodha WS for {len(self.builders)} tokens"
        )
        self.kws.connect(threaded=True)

    def _on_connect(self, ws, response):
        tokens = list(self.builders.keys())
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        self._connected = True
        self._last_tick_ts = time.time()
        write_audit_log(
            f"[WS] Subscribed in FULL mode for {len(tokens)} tokens"
        )

    def _on_close(self, ws, code, reason):
        self._connected = False
        write_audit_log(f"[WS] Closed {code} {reason}")
        self._schedule_reconnect()

    def _on_error(self, ws, code, reason):
        self._connected = False
        write_audit_log(f"[WS] Error {code} {reason}")
        self._schedule_reconnect()

    # -------------------------------------------------
    # HEALTH MONITOR (NEW, SAFE)
    # -------------------------------------------------

    def _health_loop(self):
        while True:
            if self._connected:
                if time.time() - self._last_tick_ts > self.STALE_TICK_SEC:
                    write_audit_log("[WS][STALE] No ticks → reconnecting")
                    self._connected = False
                    self._schedule_reconnect()
            time.sleep(5)

    def _schedule_reconnect(self):
        if self._reconnecting:
            return

        self._reconnecting = True

        def delayed():
            time.sleep(self.RECONNECT_DELAY_SEC)
            try:
                self.kws.connect(threaded=True)
            finally:
                self._reconnecting = False

        threading.Thread(target=delayed, daemon=True).start()

    # -------------------------------------------------
    # LIVE TICKS (UNCHANGED LOGIC)
    # -------------------------------------------------

    def _on_ticks(self, ws, ticks):
        self._last_tick_ts = time.time()
        now_ts = int(time.time())

        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")

            if token not in self.builders or ltp is None:
                continue

            symbol = self.strategies[token].symbol
            LTPStore.update(symbol, ltp)

            builder = self.builders[token]
            builder.last_price = ltp

            candle = builder.on_tick(ltp, now_ts)
            if not candle:
                continue

            write_market_timeline_row(
                candle=candle,
                indicators={},
                conditions={},
                signal=None,
                symbol=symbol,
                timeframe="1m",
                strategy_version="V1.9",
                mode="insert",
            )

            ind_engine = self.indicators[token]
            ind_vals = ind_engine.update(candle)

            if not ind_engine.is_ready():
                continue

            conditions = self.condition_engine.evaluate(
                candle=candle,
                indicators=ind_vals,
                is_trading_time=True,
                no_open_trade=not self.strategies[token].in_trade,
            )

            signal = self.strategies[token].on_candle(
                candle,
                ind_engine,
                conditions,
            )

            if (
                signal.is_buy
                and self.current_week_expiry is not None
                and self.token_expiry.get(token) == self.current_week_expiry
            ):
                signal_router.route_buy_signal(
                    symbol=symbol,
                    token=token,
                    candle_ts=candle.end_ts,
                    entry_price=signal.entry_price,
                    sl_price=signal.sl,
                    tp_price=signal.tp,
                )

            write_market_timeline_row(
                candle=candle,
                indicators={
                    "ema8": ind_vals["ema8"],
                    "ema20_low": ind_vals["ema20_low"],
                    "ema20_high": ind_vals["ema20_high"],
                    "rsi_raw": ind_vals["rsi_raw"],
                },
                conditions=conditions,
                signal="BUY" if signal.is_buy else None,
                symbol=symbol,
                timeframe="1m",
                strategy_version="V1.9",
                mode="update",
            )

    # -------------------------------------------------
    # DEBUG
    # -------------------------------------------------

    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)
