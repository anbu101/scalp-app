from typing import Dict, List
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

from app.db.paper_trades_repo import get_open_paper_trades_for_symbol
from app.trading.paper_trade_recorder import PaperTradeRecorder

from app.event_bus.ws_freeze import WS_MUTATION_FROZEN
from app.engine.signal_router import SignalRouter
from app.core.engine_registry import BB_ENGINE_REGISTRY
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY      # ← NEW
from app.marketdata.ws_registry import register_ws_engine


def _timeframe_str(timeframe_sec: int) -> str:
    minutes = timeframe_sec // 60
    if minutes > 0:
        return f"{minutes}m"
    return f"{timeframe_sec}s"


class ZerodhaTickEngine:
    """
    Zerodha WebSocket Engine (AUTHORITATIVE)

    RULES (DO NOT BREAK):
    - connect() is called EXACTLY ONCE
    - KiteTicker handles reconnection internally
    - WS thread must stay non-blocking

    SCALP_V1 now generates SELL signals (short options).
    BB_V1 / BB_V2 / HA_V1 are dispatched via BB_ENGINE_REGISTRY
    and are completely unaffected by this change.
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        strategy_id: str,
        kite_data: KiteConnect,
        instrument_tokens: List[int],
        timeframe_sec: int = 60,
    ):

        register_ws_engine(self)

        self.strategy_id   = strategy_id
        self.signal_router = SignalRouter(strategy_id)
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

        instruments_df = load_instruments_df()

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
        # STARTUP RECONCILE — clear stale OPEN paper trades
        # Runs once at engine construction, before any tick can
        # arrive (kws.connect() not called yet). Guarantees the
        # strategy-wide single-trade gate isn't held by a row left
        # OPEN from a prior session (e.g. a skipped EOD close).
        #
        # dry_run=True → diagnostic only: logs stale rows, closes
        # nothing. Inspect [RECONCILE][STALE] lines, then flip to
        # dry_run=False once verified.
        # -------------------------------------------------
        try:
            from app.db.paper_trades_repo import reconcile_stale_open_trades
            reconcile_stale_open_trades(self.strategy_id, dry_run=True)
        except Exception as e:
            write_audit_log(
                f"[RECONCILE][STALE][ERROR] {self.strategy_id} "
                f"startup reconcile failed: {e!r}"
            )
        # -------------------------------------------------
        # WS CALLBACKS
        # -------------------------------------------------

        self.kws.on_ticks   = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close   = self._on_close
        self.kws.on_error   = self._on_error

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
                write_audit_log(
                    f"[WS] Additional tokens subscribed: {len(tokens)}"
                )
            except Exception as e:
                write_audit_log(f"[WS][ERROR] subscribe_additional_tokens: {e}")

        threading.Thread(target=_do_subscribe, daemon=True).start()

    # ==================================================
    # START
    # ==================================================

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

        try:
            self.kws.connect(threaded=True)
        except Exception as e:
            write_audit_log(f"[WS][FATAL] kws.connect exception: {e}")

    # ==================================================
    # WARMUP
    # ==================================================

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
            f"[WS] Connected — subscribed {len(all_tokens)} tokens "
            f"({len(tokens)} initial + {len(extra)} extra)"
        )

        with self._lock:
            self._connected = True

        try:
            for bb_engine in BB_ENGINE_REGISTRY:
                bb_engine.on_ws_reconnect()
        except Exception as e:
            write_audit_log(f"[WS][BB_RECONNECT_NOTIFY_ERROR] {e}")

                # Notify HA engines                                          ← NEW
        try:
            for ha_engine in HA_ENGINE_REGISTRY:
                ha_engine.on_ws_reconnect()
        except Exception as e:
            write_audit_log(f"[WS][HA_RECONNECT_NOTIFY_ERROR] {e}")

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[WS] Closed {code} {reason}")
        with self._lock:
            self._connected = False

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[WS] Error {code} {reason}")

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

            # Throttled FUT-tick diagnostic for BB
            if BB_ENGINE_REGISTRY:
                bb = BB_ENGINE_REGISTRY[0]
                if token == getattr(bb, 'fut_token', None):
                    if not hasattr(self, '_ws_fut_tick_log') or ts - self._ws_fut_tick_log >= 60:
                        write_audit_log(
                            f"[WS][FUT_TICK] token={token} ltp={ltp} "
                            f"registry_size={len(BB_ENGINE_REGISTRY)}"
                        )
                        self._ws_fut_tick_log = ts

            # Forward to BB / HA engines (unaffected by SCALP changes)
            try:
                for bb_engine in BB_ENGINE_REGISTRY:
                    bb_engine.on_tick(token, ltp, ts)
            except Exception as e:
                write_audit_log(f"[BB_DISPATCH_ERROR] {e}")


            try:
                for ha_engine in HA_ENGINE_REGISTRY:
                    ha_engine.on_tick(token, ltp, ts)
            except Exception as e:
                write_audit_log(f"[HA_DISPATCH_ERROR] {e}")

            # -------------------------------------------------
            # INDEX UPDATE
            # -------------------------------------------------

            if token in self.index_tokens:
                MarketIndicesState.update_ltp(
                    self.index_tokens[token],
                    ltp,
                )
                continue

            if token not in self.builders:
                continue

            builder  = self.builders[token]
            strategy = self.strategies[token]
            symbol   = strategy.symbol

            # Hard block: only CE/PE options reach strategy execution
            if not (symbol.endswith("CE") or symbol.endswith("PE")):
                continue

            LTPStore.update(symbol, ltp)

            # -------------------------------------------------
            # PAPER TRADE EXIT (DB-DRIVEN)
            # SHORT trade: profit when ltp FALLS to tp, loss when ltp RISES to sl
            # -------------------------------------------------

            try:
                open_trades = get_open_paper_trades_for_symbol(
                    strategy_name=self.strategy_id,
                    symbol=symbol,
                )

                if open_trades:
                    trade = open_trades[0]

                    paper_trade_id = trade["paper_trade_id"]
                    sl_price       = trade["sl_price"]
                    tp_price       = trade["tp_price"]

                    # SHORT: SL = ltp rises ABOVE sl_price
                    if sl_price and ltp >= sl_price:
                        PaperTradeRecorder.force_exit(
                            paper_trade_id=paper_trade_id,
                            strategy_id=self.strategy_id,
                            symbol=symbol,
                            reason="SL",
                        )
                    # SHORT: TP = ltp falls BELOW tp_price
                    elif tp_price and ltp <= tp_price:
                        PaperTradeRecorder.force_exit(
                            paper_trade_id=paper_trade_id,
                            strategy_id=self.strategy_id,
                            symbol=symbol,
                            reason="TP",
                        )

            except Exception as e:
                write_audit_log(f"[EXIT_CHECK_ERROR] {e}")

            builder.last_price = ltp
            candle = builder.on_tick(ltp, ts)

            if not candle:
                continue

            _timeframe_str_val = self.timeframe_str

            def write_candle_async(
                candle=candle,
                symbol=symbol,
                token=token,
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
                        candle=candle,
                        indicators={},
                        conditions={},
                        signal=None,
                        symbol=symbol,
                        timeframe=timeframe_str,
                        strategy_version="V1.9",
                        mode="insert",
                    )

                    conn.commit()

                    ind_vals = ind_engine.update(candle)

                    if not ind_engine.is_ready():
                        return

                    conditions = self.condition_engine.evaluate(
                        candle=candle,
                        indicators=ind_vals,
                        is_trading_time=True,
                        no_open_trade=not strategy.in_trade,
                    )

                    signal = strategy.on_candle(
                        candle,
                        ind_engine,
                        conditions,
                    )

                    is_option = symbol.endswith("CE") or symbol.endswith("PE")

                    # --------------------------------------------------
                    # SCALP_V1 SELL SIGNAL ROUTING
                    # Previously routed route_buy_signal on is_buy.
                    # Now routes route_sell_signal on is_sell.
                    # BB_V1 / BB_V2 / HA_V1 are dispatched via
                    # BB_ENGINE_REGISTRY above — completely separate path.
                    # --------------------------------------------------
                    if signal.is_sell and is_option:
                        if current_week_expiry is None:
                            write_audit_log(
                                f"[DISPATCH][{self.strategy_id}] DROP_NO_EXPIRY "
                                f"{symbol} token={token} ts={candle.end_ts}"
                            )
                        elif token_expiry != current_week_expiry:
                            write_audit_log(
                                f"[DISPATCH][{self.strategy_id}] DROP_EXPIRY "
                                f"{symbol} token={token} ts={candle.end_ts} "
                                f"token_expiry={token_expiry} "
                                f"current_week={current_week_expiry}"
                            )
                        else:
                            write_audit_log(
                                f"[DISPATCH][{self.strategy_id}] ROUTE "
                                f"{symbol} token={token} ts={candle.end_ts} "
                                f"entry={signal.entry_price} "
                                f"sl={signal.sl} tp={signal.tp}"
                            )
                            self.signal_router.route_sell_signal(
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
                            "ema8":       ind_vals["ema8"],
                            "ema20_low":  ind_vals["ema20_low"],
                            "ema20_high": ind_vals["ema20_high"],
                            "rsi_raw":    ind_vals["rsi_raw"],
                        },
                        conditions=conditions,
                        signal="SELL" if signal.is_sell else None,
                        symbol=symbol,
                        timeframe=timeframe_str,
                        strategy_version="V1.9",
                        mode="update",
                    )

                except Exception as e:
                    import traceback
                    write_audit_log(
                        f"[ERROR][{self.strategy_id}] Candle processing failed "
                        f"for {symbol} token={token} ts={candle.end_ts}: {e!r}"
                    )
                    write_audit_log(traceback.format_exc())

            threading.Thread(
                target=write_candle_async,
                daemon=True,
            ).start()

    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)