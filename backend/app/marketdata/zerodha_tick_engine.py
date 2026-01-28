from typing import Dict, List
import time
from datetime import date
import math
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
from app.engine.signal_router import signal_router
from app.utils.market_hours import is_market_open
from app.marketdata.market_indices_state import MarketIndicesState

from app.db.paper_trades_repo import get_open_paper_trades_for_symbol
from app.trading.paper_trade_recorder import PaperTradeRecorder

from app.event_bus.ws_freeze import WS_MUTATION_FROZEN


class ZerodhaTickEngine:
    """
    Zerodha WebSocket Engine (AUTHORITATIVE)

    RULES (DO NOT BREAK):
    - connect() is called EXACTLY ONCE
    - KiteTicker handles reconnection internally
    - WS thread must stay non-blocking
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        kite_data: KiteConnect,
        instrument_tokens: List[int],
        timeframe_sec: int = 60,
    ):
        self.kite_data = kite_data

        self.kws = KiteTicker(
            api_key=kite_data.api_key,
            access_token=kite_data.access_token,
        )

        self._started = False
        self._connected = False
        self._lock = threading.Lock()

        # -------------------------------------------------
        # Candle queue (WS thread MUST NOT BLOCK)
        # -------------------------------------------------

        instruments_df = load_instruments_df()

        # -------------------------------------------------
        # INDEX TOKENS
        # -------------------------------------------------
        self.index_tokens: Dict[int, str] = {}

        index_rows = instruments_df[
            instruments_df["segment"].isin(["INDICES", "BSE-INDICES"])
        ]

        write_audit_log(f"[INDEX][DEBUG] index_rows count = {len(index_rows)}")

        INDEX_ALLOWLIST = {
            "NIFTY 50": "NIFTY",
            "NIFTY BANK": "BANKNIFTY",
            "SENSEX": "SENSEX",
        }

        for _, row in index_rows.iterrows():
            ts = str(row["tradingsymbol"]).upper()
            if ts in INDEX_ALLOWLIST:
                self.index_tokens[int(row["instrument_token"])] = INDEX_ALLOWLIST[ts]

        write_audit_log(f"[INDEX] WS index tokens resolved: {self.index_tokens}")

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

        write_audit_log(
            f"[ENGINE] Current weekly expiry = {self.current_week_expiry}"
        )

        # -------------------------------------------------
        # PER TOKEN STATE
        # -------------------------------------------------
        self.token_expiry: Dict[int, date] = {}
        self.builders = {}
        self.indicators = {}
        self.strategies = {}

        self.condition_engine = ConditionEngineV19()

        for token in instrument_tokens:
            row = instruments_df.loc[
                instruments_df["instrument_token"] == token
            ].iloc[0]

            symbol = row["tradingsymbol"]

            self.token_expiry[token] = row["expiry"]

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

            # -----------------------------
            # WARMUP (KEYWORD-ONLY SAFE)
            # -----------------------------
            self._warmup_symbol(
                symbol=symbol,
                timeframe="1m",
                builder=builder,
                indicator=indicator,
            )

        # -------------------------------------------------
        # WS CALLBACKS
        # -------------------------------------------------
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error


    # -------------------------------------------------
    # START (CONNECT EXACTLY ONCE)
    # -------------------------------------------------

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True

        threading.Thread(
            target=self._wait_and_connect,
            daemon=True,
        ).start()

    def _wait_and_connect(self):
        while not is_market_open():
            time.sleep(30)

        write_audit_log("[WS] Market open → starting WS (DATA session)")
        self.kws.connect(threaded=True)

    # -------------------------------------------------
    # WARMUP
    # -------------------------------------------------

    def _warmup_symbol(
        self,
        *,
        symbol: str,
        timeframe: str,
        builder: CandleBuilder,
        indicator: IndicatorEnginePineV19,
    ):
        rows = timeline_repo.fetch_recent_candles_for_warmup(
            symbol=symbol,
            timeframe=timeframe,
            limit=self.WARMUP_CANDLES,
        )

        if not rows:
            return

        candles: List[Candle] = []
        for r in rows:
            ts = int(r["ts"])
            candles.append(
                Candle(
                    start_ts=ts,
                    end_ts=ts + builder.tf,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    source=CandleSource.WARMUP,
                )
            )

        indicator.warmup(candles, use_history=True)
        builder.last_emitted_end_ts = None
        builder.last_candle_end_ts = None



    # -------------------------------------------------
    # WS CALLBACKS
    # -------------------------------------------------

    def _on_connect(self, ws, response):
        write_audit_log("[WS] Connected")

        tokens = list(self.builders.keys()) + list(self.index_tokens.keys())
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

        with self._lock:
            self._connected = True

        write_audit_log(
            f"[WS] Subscribed: {len(self.builders)} options, "
            f"{len(self.index_tokens)} indices"
        )

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[WS] Closed code={code} reason={reason}")
        with self._lock:
            self._connected = False

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[WS] Error {code} {reason}")

    # -------------------------------------------------
    # LIVE TICKS (WS THREAD — LIGHT ONLY)
    # -------------------------------------------------

    def _on_ticks(self, ws, ticks):
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")

            if token is None or ltp is None:
                continue

            ts = tick.get("exchange_timestamp")
            sys_ts = int(time.time())

            if ts:
                exch_ts = int(ts.timestamp())
                now_ts = exch_ts if exch_ts >= sys_ts - 120 else sys_ts
            else:
                now_ts = sys_ts

            # -----------------------------
            # Indices
            # -----------------------------
            if token in self.index_tokens:
                MarketIndicesState.update_ltp(
                    self.index_tokens[token],
                    ltp,
                )
                continue

            if token not in self.builders:
                continue

            builder = self.builders[token]
            symbol = self.strategies[token].symbol

            LTPStore.update(symbol, ltp)

            # -----------------------------
            # PAPER TRADE EXIT
            # -----------------------------
            open_paper_trades = get_open_paper_trades_for_symbol(
                strategy_name="1M_SCALP",
                symbol=symbol,
            )

            for t in open_paper_trades:
                PaperTradeRecorder.try_exit(
                    paper_trade_id=t["paper_trade_id"],
                    symbol=symbol,
                    sl_price=t["sl_price"],
                    tp_price=t["tp_price"],
                )

            builder.last_price = ltp

            # ✅ ONLY place where candle timeline is initialized
            if builder.last_emitted_end_ts is None:
                builder.last_emitted_end_ts = now_ts - (now_ts % builder.tf)
                builder.last_candle_end_ts = builder.last_emitted_end_ts
                
            candle = builder.on_tick(ltp, now_ts)
            write_audit_log(
                f"[DEBUG][TICK] symbol={symbol} "
                f"now={now_ts} "
                f"last_end={builder.last_candle_end_ts} "
                f"emitted={builder.last_emitted_end_ts}"
            )

            if not candle:
                continue

            # 1️⃣ INSERT RAW CANDLE
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

            # 2️⃣ INDICATORS
            ind_engine = self.indicators[token]
            ind_vals = ind_engine.update(candle)

            if not ind_engine.is_ready():
                continue

            # 3️⃣ CONDITIONS
            conditions = self.condition_engine.evaluate(
                candle=candle,
                indicators=ind_vals,
                is_trading_time=True,
                no_open_trade=not self.strategies[token].in_trade,
            )

            # 4️⃣ STRATEGY
            signal = self.strategies[token].on_candle(
                candle,
                ind_engine,
                conditions,
            )

            # 5️⃣ ROUTE BUY SIGNAL
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

            # 6️⃣ UPDATE TIMELINE
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


    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)
