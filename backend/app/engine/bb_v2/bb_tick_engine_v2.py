# backend/app/engine/bb_v2/bb_tick_engine_v2.py

import time
import threading
import inspect
from pathlib import Path
from datetime import datetime, timedelta, date

from kiteconnect import KiteConnect

from app.engine.bb_options.futures_resolver import (
    resolve_current_month_banknifty_fut,
)
from app.candles.candle_builder import CandleBuilder
from app.event_bus.audit_logger import write_audit_log
from app.engine.bb_options.daily_futures_loader import load_recent_daily_futures
from app.db.futures_candles_repo import init_table, insert_candle, fetch_recent_candles
from app.engine.bb_options.monthly_expiry_resolver import resolve_current_monthly_expiry
from app.fetcher.zerodha_instruments import load_instruments_df
from app.marketdata.ltp_store import LTPStore

from app.engine.bb_v2.indicator_bundle_v2 import IndicatorBundleV2
from app.engine.bb_v2.confluence_signal_engine_v2 import ConfluenceSignalEngineV2

from app.engine.bb_options.bb_trade_manager import BBTradeManager
from app.engine.bb_options.bb_trade_state_manager import BBTradeStateManager
from app.engine.bb_options.gtt_monitor import GTTMonitor

from app.marketdata.ws_registry import get_ws_engines
from app.db.paper_trades_repo import get_all_open_paper_trades
from app.core.engine_registry import BB_ENGINE_REGISTRY


class BBOptionsTickEngineV2:

    STRATEGY_ID  = "BB_V2"
    _WARMUP_DAYS = 7

    def __init__(
        self,
        kite_data:  KiteConnect,
        executor,
        config:     dict,
        trade_mode: str,
    ):
        try:
            write_audit_log(
                f"[BB_V2_RUNTIME_FILE] {inspect.getfile(BBOptionsTickEngineV2)}"
            )
            write_audit_log(
                f"[BB_V2_REGISTRY_BEFORE] id={id(BB_ENGINE_REGISTRY)} "
                f"size={len(BB_ENGINE_REGISTRY)}"
            )

            self.kite_data           = kite_data
            self.executor            = executor
            self.config              = config
            self.trade_mode          = trade_mode
            self._futures_subscribed = False

            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}][MODE={trade_mode}]"
                f"[COMPONENT=TickEngineV2][INIT]"
            )

            # -------------------------------------------------
            # RESOLVE FUTURES
            # -------------------------------------------------
            resolved = resolve_current_month_banknifty_fut()
            if not resolved:
                raise RuntimeError(
                    "Could not resolve BANKNIFTY FUT contract (BB_V2)"
                )

            self.fut_token, self.fut_symbol = resolved

            write_audit_log(
                f"[{self.STRATEGY_ID}] FUT symbol={self.fut_symbol} "
                f"token={self.fut_token}"
            )

            # -------------------------------------------------
            # DB INIT + DAILY LOAD
            # -------------------------------------------------
            init_table()

            load_recent_daily_futures(
                kite=self.kite_data,
                instrument_token=self.fut_token,
                symbol=self.fut_symbol,
            )

            # -------------------------------------------------
            # 3M HISTORICAL WARMUP
            # -------------------------------------------------
            self._warmup_intraday_history()

            # -------------------------------------------------
            # INSTRUMENTS
            # -------------------------------------------------
            self.monthly_expiry            = resolve_current_monthly_expiry()
            self.instruments_df            = load_instruments_df()
            self.option_tokens:  dict      = {}
            self.last_strike_refresh_price = None

            # -------------------------------------------------
            # CANDLE BUILDER (independent of V1's builder)
            # -------------------------------------------------
            last_warmup_ts = None
            try:
                recent = fetch_recent_candles(
                    symbol=self.fut_symbol,
                    timeframe="3m",
                    limit=1,
                )
                if recent:
                    last_warmup_ts = recent[-1]["ts"]
            except Exception as e:
                write_audit_log(f"[BB_V2][BUILDER_SEED_ERROR] {e}")

            self.builder = CandleBuilder(
                instrument_token=self.fut_token,
                timeframe_sec=3 * 60,
                last_candle_end_ts=last_warmup_ts,
            )

            # -------------------------------------------------
            # V2-SPECIFIC INDICATOR + SIGNAL ENGINES
            # -------------------------------------------------
            self.indicator_bundle = IndicatorBundleV2(self.fut_symbol)

            self.signal_engine = ConfluenceSignalEngineV2(
                max_trades_per_side=config.get("max_trades_per_side", 10),
            )

            # -------------------------------------------------
            # STATE MANAGERS
            # -------------------------------------------------
            if trade_mode == "LIVE":
                self.ce_state = BBTradeStateManager(
                    side="CE",
                    strategy_id=self.STRATEGY_ID,
                    executor=self.executor,
                    state_file=Path("state/bb_v2_ce.json"),
                )
                self.pe_state = BBTradeStateManager(
                    side="PE",
                    strategy_id=self.STRATEGY_ID,
                    executor=self.executor,
                    state_file=Path("state/bb_v2_pe.json"),
                )

                try:
                    if self.ce_state.in_trade:
                        self.signal_engine.ce_in_trade = True
                    if self.pe_state.in_trade:
                        self.signal_engine.pe_in_trade = True
                    write_audit_log(
                        f"[BB_V2][STATE_SYNC] "
                        f"CE={self.signal_engine.ce_in_trade} "
                        f"PE={self.signal_engine.pe_in_trade}"
                    )
                except Exception as e:
                    write_audit_log(f"[BB_V2][STATE_SYNC_ERROR] {e}")

            else:
                self.ce_state = None
                self.pe_state = None

                try:
                    from app.db.paper_trades_repo import has_open_paper_trade_by_side

                    if has_open_paper_trade_by_side(
                        strategy_name=self.STRATEGY_ID, side="CE"
                    ):
                        self.signal_engine.ce_in_trade = True

                    if has_open_paper_trade_by_side(
                        strategy_name=self.STRATEGY_ID, side="PE"
                    ):
                        self.signal_engine.pe_in_trade = True

                    write_audit_log(
                        f"[BB_V2][PAPER_STATE_SYNC] "
                        f"CE={self.signal_engine.ce_in_trade} "
                        f"PE={self.signal_engine.pe_in_trade}"
                    )
                except Exception as e:
                    write_audit_log(f"[BB_V2][PAPER_STATE_SYNC_ERROR] {e}")

            # -------------------------------------------------
            # TRADE MANAGER (reuses V1 BBTradeManager — duck typing
            # means TradeSignalV2 works since fields are identical)
            # -------------------------------------------------
            self.trade_manager = BBTradeManager(
                strategy_id=self.STRATEGY_ID,
                trade_mode=trade_mode,
                executor=self.executor,
                symbol_fut=self.fut_symbol,
                lot_size=30,
                lot_count=1,
                sl_percent=config.get("sl_pct", 0),
                tp_percent=config.get("tp_pct", 0),
                max_premium=config.get("max_premium", 300),
                scan_strikes=60,
                config=config,
            )

            self.trade_manager.attach_state_managers(
                ce_state=self.ce_state,
                pe_state=self.pe_state,
                signal_engine=self.signal_engine,
            )

            # -------------------------------------------------
            # GTT MONITOR (LIVE only)
            # -------------------------------------------------
            self._gtt_monitor = None

            if trade_mode == "LIVE":
                self._gtt_monitor = GTTMonitor(
                    executor=self.executor,
                    signal_engine=self.signal_engine,
                    ce_state=self.ce_state,
                    pe_state=self.pe_state,
                    strategy_id=self.STRATEGY_ID,
                )
                self._gtt_monitor.start()

            write_audit_log(f"[STRATEGY={self.STRATEGY_ID}][ENGINE_READY]")

            if not any(
                isinstance(e, BBOptionsTickEngineV2)
                for e in BB_ENGINE_REGISTRY
            ):
                BB_ENGINE_REGISTRY.append(self)

            write_audit_log(
                f"[BB_V2_REGISTERED] id={id(BB_ENGINE_REGISTRY)} "
                f"size={len(BB_ENGINE_REGISTRY)}"
            )

        except Exception as e:
            write_audit_log(f"[BB_V2_CONSTRUCTOR_FATAL] {repr(e)}")
            raise

    # ==================================================
    # START
    # ==================================================

    def start(self):
        self._last_fut_tick_ts = time.time()
        threading.Thread(
            target=self._fut_tick_watchdog,
            daemon=True,
            name="bb-v2-fut-watchdog",
        ).start()

    # ==================================================
    # WATCHDOG
    # ==================================================

    def _fut_tick_watchdog(self):
        import time as _t
        _t.sleep(120)

        STALE_THRESHOLD = 180

        while True:
            _t.sleep(60)
            elapsed = _t.time() - self._last_fut_tick_ts

            if elapsed > STALE_THRESHOLD:
                write_audit_log(
                    f"[BB_V2][WATCHDOG] No FUT tick for {int(elapsed)}s — "
                    f"re-subscribing token={self.fut_token}"
                )
                self._futures_subscribed       = False
                self.last_strike_refresh_price = None

                engines = get_ws_engines()
                if engines:
                    try:
                        engines[0].subscribe_additional_tokens([self.fut_token])
                        self._futures_subscribed = True
                    except Exception as e:
                        write_audit_log(f"[BB_V2][WATCHDOG][ERROR] {e}")

    # ==================================================
    # INTRADAY WARMUP
    # ==================================================

    def _warmup_intraday_history(self):
        write_audit_log(
            f"[{self.STRATEGY_ID}] Fetching {self._WARMUP_DAYS}d of 3m candles"
        )
        try:
            end_date   = date.today()
            start_date = end_date - timedelta(days=self._WARMUP_DAYS)

            candles = self.kite_data.historical_data(
                instrument_token=self.fut_token,
                from_date=start_date,
                to_date=end_date,
                interval="3minute",
            )

            if not candles:
                write_audit_log(f"[{self.STRATEGY_ID}][WARMUP] No 3m candles returned")
                return

            inserted = 0
            for c in candles:
                ts = int(c["date"].timestamp())
                insert_candle(
                    symbol=self.fut_symbol,
                    timeframe="3m",
                    ts=ts,
                    open_=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    indicators=None,
                )
                inserted += 1

            write_audit_log(
                f"[{self.STRATEGY_ID}][WARMUP] "
                f"Loaded {len(candles)} candles, inserted={inserted}"
            )
        except Exception as e:
            write_audit_log(f"[{self.STRATEGY_ID}][WARMUP_ERROR] {repr(e)}")

    # ==================================================
    # WS RECONNECT HANDLER
    # ==================================================

    def on_ws_reconnect(self):
        write_audit_log(
            f"[{self.STRATEGY_ID}][WS_RECONNECT] "
            f"FUT already re-subscribed by _on_connect"
        )

    # ==================================================
    # TICK DISPATCH (called by ZerodhaTickEngine)
    # ==================================================

    def on_tick(self, token: int, ltp: float, ts: int):
        self._ensure_futures_subscription()

        if token == self.fut_token:
            LTPStore.update(self.fut_symbol, ltp)
            self._ensure_option_subscription(ltp)
            self._last_fut_tick_ts = time.time()

            candle = self.builder.on_tick(ltp, ts)
            if candle:
                self._process_candle(candle)

        elif token in self.option_tokens:
            LTPStore.update(self.option_tokens[token], ltp)

    # ==================================================
    # PROCESS CANDLE
    # ==================================================

    def _process_candle(self, candle):
        # IndicatorBundleV2.update() returns a dict whose keys are
        # already aligned with DB column names:
        #   "supertrend_v2"   → futures_candles.supertrend_v2
        #   "st_direction_v2" → futures_candles.st_direction_v2
        #   "r2", "pp", "s2", "s3" → shared extended pivot columns
        #   "bb_*", "rsi_*", "r1", "s1" → shared columns
        #   "prev_close" → internal only, ignored by insert_candle
        indicators = self.indicator_bundle.update(candle)

        signal = self.signal_engine.update(
            close=candle.close,
            indicators=indicators,
        )

        # --------------------------------------------------
        # PERSIST TO futures_candles
        #
        # Isolation contract:
        #   • indicators dict has NO "supertrend" or "st_direction" keys
        #     → insert_candle's ind.get("supertrend") returns None
        #     → COALESCE(NULL, existing_V1_value) preserves V1's ST
        #   • V1 signal fields passed as None → COALESCE preserves V1's signals
        #   • V1 state fields passed as None  → COALESCE preserves V1's state
        #   • V2-specific fields written to their own columns exclusively
        # --------------------------------------------------
        insert_candle(
            symbol=self.fut_symbol,
            timeframe="3m",
            ts=candle.start_ts,
            open_=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            indicators=indicators,    # already has correct V2 key names

            # V1 signal/state fields — pass None to leave V1's data intact
            signal_action=None,
            signal_reason=None,
            rejection_reason=None,
            ce_in_trade=None,         # None → COALESCE keeps V1's value
            pe_in_trade=None,
            ce_trades_today=None,
            pe_trades_today=None,

            # V2-specific signal/state fields
            signal_action_v2=signal.action,
            signal_reason_v2=signal.reason,
            rejection_reason_v2=signal.rejection_reason,
            ce_in_trade_v2=self.signal_engine.ce_in_trade,
            pe_in_trade_v2=self.signal_engine.pe_in_trade,
            ce_trades_today_v2=self.signal_engine.ce_trades_today,
            pe_trades_today_v2=self.signal_engine.pe_trades_today,
        )

        write_audit_log(
            f"[BB_V2_SIGNAL] close={candle.close} "
            f"st_v2={indicators.get('supertrend_v2')} "
            f"action={signal.action} "
            f"reason={signal.reason} "
            f"reject={signal.rejection_reason}"
        )

        if signal.action:
            self.trade_manager.handle_signal(signal)

        self._ensure_active_option_subscriptions()

        if self.trade_mode == "PAPER":
            self._check_paper_sl_tp()

    # ==================================================
    # PAPER SL/TP MONITOR
    # ==================================================

    def _check_paper_sl_tp(self):
        try:
            open_trades = get_all_open_paper_trades(self.STRATEGY_ID)
        except Exception as e:
            write_audit_log(f"[BB_V2][PAPER_SL_CHECK_ERROR] {e}")
            return

        if not open_trades:
            return

        for trade in open_trades:
            paper_trade_id = trade.get("paper_trade_id")
            symbol         = trade.get("symbol")
            sl_price       = trade.get("sl_price") or 0
            tp_price       = trade.get("tp_price") or 0

            if not paper_trade_id or not symbol:
                continue
            if sl_price <= 0 and tp_price <= 0:
                continue

            if LTPStore.get(symbol) is None:
                try:
                    quote    = self.kite_data.ltp(f"NFO:{symbol}")
                    rest_ltp = quote[f"NFO:{symbol}"]["last_price"]
                    if rest_ltp and rest_ltp > 0:
                        LTPStore.update(symbol, rest_ltp)
                except Exception as e:
                    write_audit_log(
                        f"[BB_V2][PAPER_LTP_REST_FAIL] {symbol} ERR={e}"
                    )
                    continue

            try:
                from app.trading.paper_trade_recorder import PaperTradeRecorder
                PaperTradeRecorder.try_exit(
                    paper_trade_id=paper_trade_id,
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    sl_price=sl_price,
                    tp_price=tp_price,
                )
            except Exception as e:
                write_audit_log(
                    f"[BB_V2][PAPER_SL_FAILED] "
                    f"trade_id={paper_trade_id} symbol={symbol} ERR={e}"
                )

    # ==================================================
    # SUBSCRIPTION HELPERS
    # ==================================================

    def _ensure_futures_subscription(self):
        if self._futures_subscribed:
            return
        engines = get_ws_engines()
        if not engines:
            return
        try:
            engines[0].subscribe_additional_tokens([self.fut_token])
            self._futures_subscribed = True
            write_audit_log(
                f"[{self.STRATEGY_ID}][FUTURES_SUBSCRIBED] token={self.fut_token}"
            )
        except Exception as e:
            write_audit_log(f"[BB_V2][FUT_SUB_ERROR] {e}")

    def _ensure_option_subscription(self, futures_price: float):
        if self.last_strike_refresh_price is not None:
            if abs(futures_price - self.last_strike_refresh_price) < 200:
                return

        self.last_strike_refresh_price = futures_price
        atm = int((futures_price + 50) // 100) * 100

        new_token_map = {}
        for i in range(-30, 31):
            strike = atm + i * 100
            df = self.instruments_df[
                (self.instruments_df["segment"]  == "NFO-OPT")
                & (self.instruments_df["name"]   == "BANKNIFTY")
                & (self.instruments_df["strike"] == strike)
                & (self.instruments_df["expiry"] == self.monthly_expiry)
            ]
            for _, row in df.iterrows():
                new_token_map[int(row["instrument_token"])] = str(row["tradingsymbol"])

        self.option_tokens.update(new_token_map)

    def _ensure_active_option_subscriptions(self):
        symbols_needed = set()

        for state in [self.ce_state, self.pe_state]:
            if state and state.active_trade and state.active_trade.symbol:
                symbols_needed.add(state.active_trade.symbol)

        if self.trade_mode == "PAPER":
            try:
                for t in get_all_open_paper_trades(self.STRATEGY_ID):
                    sym = t.get("symbol")
                    if sym:
                        symbols_needed.add(sym)
            except Exception:
                pass

        if not symbols_needed:
            return

        tokens_to_subscribe = []
        for sym in symbols_needed:
            df = self.instruments_df[self.instruments_df["tradingsymbol"] == sym]
            if df.empty:
                continue
            tok = int(df.iloc[0]["instrument_token"])
            if tok not in self.option_tokens:
                self.option_tokens[tok] = sym
                tokens_to_subscribe.append(tok)
            elif LTPStore.get(sym) is None:
                tokens_to_subscribe.append(tok)

        if not tokens_to_subscribe:
            return

        engines = get_ws_engines()
        if not engines:
            return
        try:
            engines[0].subscribe_additional_tokens(tokens_to_subscribe)
        except Exception as e:
            write_audit_log(f"[BB_V2][OPTION_SUB_ERROR] {e}")

    # ==================================================
    # EOD SQUARE-OFF
    # ==================================================

    def eod_squareoff(self):
        try:
            self.trade_manager.eod_squareoff()
        except Exception as e:
            write_audit_log(f"[BB_V2][EOD][ERROR] {repr(e)}")