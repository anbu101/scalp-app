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
from app.db.timeline_repo import fetch_recent_candles_for_warmup
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

    IMPORTANT:
    - Uses DATA KiteConnect session ONLY
    - WebSocket handles OPTIONS ONLY
    - Indices are handled via REST elsewhere
    """

    WARMUP_CANDLES = 500

    STALE_TICK_SEC = 20
    RECONNECT_DELAY_SEC = 5
    CONNECT_GRACE_SEC = 60

    def __init__(
        self,
        kite_data: KiteConnect,              # DATA SESSION
        instrument_tokens: List[int],
        timeframe_sec: int = 60,
    ):
        self.kite_data = kite_data

        self.kws = KiteTicker(
            api_key=kite_data.api_key,
            access_token=kite_data.access_token,
        )

        self._last_tick_ts = 0.0
        self._connected = False
        self._reconnecting = False
        self._connected_at = 0.0
        self._started = False
        self._lock = threading.Lock()

        instruments_df = load_instruments_df()

        # -------------------------------------------------
        # INDEX TOKENS (WS FEED)
        # -------------------------------------------------
        self.index_tokens: Dict[int, str] = {}

        index_rows = instruments_df[
            instruments_df["segment"].isin(["INDICES", "BSE-INDICES"])
        ]

        write_audit_log(
            f"[INDEX][DEBUG] index_rows count = {len(index_rows)}"
        )
        write_audit_log(
            f"[INDEX][DEBUG] index_rows symbols = "
            f"{index_rows['tradingsymbol'].unique().tolist()}"
        )

        # -------------------------------------------------
        # STRICT INDEX TOKEN RESOLUTION (UI SAFE)
        # -------------------------------------------------

        # Only subscribe to indices that the UI understands.
        # DO NOT widen this list without updating MarketIndicesState + UI.
        INDEX_ALLOWLIST = {
            "NIFTY 50": "NIFTY",
            "NIFTY BANK": "BANKNIFTY",
            "SENSEX": "SENSEX",  # enable only if UI supports it
        }

        self.index_tokens = {}

        for _, row in index_rows.iterrows():
            ts = str(row["tradingsymbol"]).upper()
            token = int(row["instrument_token"])

            if ts in INDEX_ALLOWLIST:
                self.index_tokens[token] = INDEX_ALLOWLIST[ts]

        # -------------------------------------------------
        #   Validation
        # -------------------------------------------------

        if not self.index_tokens:
            write_audit_log(
                "[INDEX][FATAL] No index tokens resolved — WS index feed DISABLED"
            )
        else:
            write_audit_log(
                f"[INDEX] WS index tokens resolved: {self.index_tokens}"
            )


        # -------------------------------------------------
        # WEEKLY EXPIRY (BUY ROUTING SAFETY)
        # -------------------------------------------------
        weekly_opts = instruments_df[
            (instruments_df["segment"] == "NFO-OPT")
            & (instruments_df["name"] == "NIFTY")
        ]

        from datetime import date

        today = date.today()

        valid_expiries = weekly_opts[
            weekly_opts["expiry"] >= today
        ]["expiry"]

        self.current_week_expiry = (
            valid_expiries.min() if not valid_expiries.empty else None
        )


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

        # -------------------------------------------------
        # PER-TOKEN STATE
        # -------------------------------------------------
        self.token_expiry: Dict[int, date] = {}
        self.builders: Dict[int, CandleBuilder] = {}
        self.indicators: Dict[int, IndicatorEnginePineV19] = {}
        self.strategies: Dict[int, StrategyEngine] = {}

        self.condition_engine = ConditionEngineV19()

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

        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

        threading.Thread(
            target=self._health_loop,
            daemon=True,
        ).start()

    # -------------------------------------------------
    # START
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
        rows = fetch_recent_candles_for_warmup(
            symbol=symbol,
            timeframe=timeframe,
            limit=self.WARMUP_CANDLES,
        )

        if not rows:
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

    # -------------------------------------------------
    # WS CALLBACKS
    # -------------------------------------------------

    def _on_connect(self, ws, response):
        if WS_MUTATION_FROZEN:
            return

        option_tokens = list(self.builders.keys())
        index_tokens = list(self.index_tokens.keys())

        tokens = option_tokens + index_tokens

        if not tokens:
            write_audit_log("[WS][WARN] No tokens to subscribe")
            return

        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

        now = time.time()
        with self._lock:
            self._connected = True
            self._connected_at = now
            self._last_tick_ts = now

        write_audit_log(
            f"[WS] Subscribed: {len(option_tokens)} options (FULL), "
            f"{len(index_tokens)} indices (FULL)"
        )


    def _on_close(self, ws, code, reason):
        with self._lock:
            self._connected = False
        write_audit_log(f"[WS] Closed {code} {reason}")

    def _on_error(self, ws, code, reason):
        with self._lock:
            self._connected = False
        write_audit_log(f"[WS] Error {code} {reason}")

    # -------------------------------------------------
    # HEALTH LOOP
    # -------------------------------------------------

    def _health_loop(self):
        while True:
            time.sleep(5)

            if not is_market_open():
                continue

            with self._lock:
                connected = self._connected
                last_tick = self._last_tick_ts
                connected_at = self._connected_at

            now = time.time()

            if connected and (now - connected_at) < self.CONNECT_GRACE_SEC:
                continue

            if connected and (now - last_tick) > self.STALE_TICK_SEC:
                if WS_MUTATION_FROZEN:
                    continue
                self._schedule_reconnect()

    def _schedule_reconnect(self):
        with self._lock:
            if self._reconnecting:
                return
            self._reconnecting = True

        def delayed():
            try:
                time.sleep(self.RECONNECT_DELAY_SEC)
                if WS_MUTATION_FROZEN:
                    return
                self.kws.connect(threaded=True)
            finally:
                with self._lock:
                    self._reconnecting = False

        threading.Thread(target=delayed, daemon=True).start()

    # -------------------------------------------------
    # LIVE TICKS (OPTIONS ONLY)
    # -------------------------------------------------

    def _on_ticks(self, ws, ticks):
        with self._lock:
            self._last_tick_ts = time.time()

        now_ts = int(time.time())

        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")

            if token is None or ltp is None:
                continue

            # ---------------- INDEX TICKS ----------------
            if token in self.index_tokens:
                index_name = self.index_tokens[token]
                MarketIndicesState.update_ltp(index_name, ltp)
                continue

            # ---------------- OPTION TICKS ----------------
            if token not in self.builders:
                continue

            symbol = self.strategies[token].symbol
            LTPStore.update(symbol, ltp)

            # -------------------------------------------------
            # PAPER TRADE EXIT CHECK (AUTHORITATIVE)
            # -------------------------------------------------
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


    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)
