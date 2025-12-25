from typing import Dict, List
import time

from kiteconnect import KiteTicker

from app.candles.candle_builder import CandleBuilder
from app.marketdata.candle import Candle, CandleSource

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

    FINAL RESPONSIBILITIES:
    ✅ Build 1M candles (bucket-based, exchange-aligned)
    ✅ Update EMA / RSI
    ✅ Run strategy
    ✅ Write market_timeline
    ✅ Route BUY signals

    ❌ No REST candles
    ❌ No CSV writes
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        api_key: str,
        access_token: str,
        instrument_tokens: List[int],
        exchange: str = "NFO",
        timeframe_sec: int = 60,
    ):
        self.kws = KiteTicker(api_key, access_token)

        instruments_df = load_instruments_df()

        self.builders: Dict[int, CandleBuilder] = {}
        self.indicators: Dict[int, IndicatorEnginePineV19] = {}
        self.strategies: Dict[int, StrategyEngine] = {}

        self.condition_engine = ConditionEngineV19()

        for token in instrument_tokens:
            row = instruments_df.loc[
                instruments_df["instrument_token"] == token
            ].iloc[0]

            symbol = row["tradingsymbol"]

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

            # -------------------------
            # WARMUP (INDICATORS ONLY)
            # -------------------------
            self._warmup_symbol(
                symbol=symbol,
                timeframe="1m",
                builder=builder,
                indicator=indicator,
            )

        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

    # -------------------------------------------------
    # WARMUP (DB → INDICATORS)
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

        
        #Warmup candle picks only today's candles
        #indicator.warmup(candles)

        #warmup candle picks previous day candles as well
        indicator.warmup(candles, use_history=True)


        # IMPORTANT: do NOT block live candles
        builder.last_emitted_end_ts = None

        write_audit_log(
            f"[WARMUP] {symbol} aligned with {len(candles)} DB candles"
        )

    # -------------------------------------------------
    # WS lifecycle
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
        write_audit_log(
            f"[WS] Subscribed in FULL mode for {len(tokens)} tokens"
        )

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[WS] Closed {code} {reason}")

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[WS] Error {code} {reason}")

    # -------------------------------------------------
    # LIVE TICKS → CANDLES → STRATEGY
    # -------------------------------------------------

    def _on_ticks(self, ws, ticks):
        now_ts = int(time.time())  # 🔑 WALL-CLOCK TIME (TradingView parity)

        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")

            if token not in self.builders or ltp is None:
                continue

            builder = self.builders[token]
            builder.last_price = ltp  # TP / SL / UI

            candle = builder.on_tick(ltp, now_ts)
            if not candle:
                continue  # candle not closed yet

            symbol = self.strategies[token].symbol

            # -------------------------
            # DB INSERT (OHLC)
            # -------------------------
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

            # -------------------------
            # INDICATORS
            # -------------------------
            ind_engine = self.indicators[token]
            ind_vals = ind_engine.update(candle)

            if not ind_engine.is_ready():
                continue

            # -------------------------
            # CONDITIONS + STRATEGY
            # -------------------------
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

            if signal.is_buy:
                signal_router.route_buy_signal(
                    symbol=symbol,
                    token=token,
                    candle_ts=candle.end_ts,
                    entry_price=signal.entry_price,
                    sl_price=signal.sl,
                    tp_price=signal.tp,
                )

            # -------------------------
            # DB UPDATE (INDICATORS)
            # -------------------------
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

    def get_ltp(self, symbol: str):
        for token, strat in self.strategies.items():
            if strat.symbol == symbol:
                return getattr(self.builders[token], "last_price", None)
        return None
