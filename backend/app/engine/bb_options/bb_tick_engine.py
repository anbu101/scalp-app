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

from app.engine.bb_options.indicator_bundle import IndicatorBundle
from app.engine.bb_options.confluence_signal_engine import ConfluenceSignalEngine
from app.engine.bb_options.bb_trade_manager import BBTradeManager
from app.engine.bb_options.bb_trade_state_manager import BBTradeStateManager
from app.engine.bb_options.gtt_monitor import GTTMonitor

from app.marketdata.ws_registry import get_ws_engines
from app.db.paper_trades_repo import get_all_open_paper_trades  # FIX Bug 2: candle-level SL/TP check
from app.core.engine_registry import BB_ENGINE_REGISTRY

from app.indicators.token_registry import save_contract
from app.engine.bb_options.monthly_expiry_resolver import resolve_current_monthly_expiry

class BBOptionsTickEngine:

    STRATEGY_ID = "BB_V1"

    # How many calendar days back to fetch 3m warmup data.
    # 7 days guarantees ≥ 3 trading days even across a long weekend,
    # giving SuperTrend/ATR enough candles to converge close to Zerodha.
    _WARMUP_DAYS = 10

    def __init__(
        self,
        kite_data: KiteConnect,
        executor,
        config: dict,
        trade_mode: str,
        broker_manager=None,
    ):

        try:

            write_audit_log(
                f"[BB_RUNTIME_FILE] {inspect.getfile(BBOptionsTickEngine)}"
            )
            write_audit_log(f"[BB_MODULE_NAME] {__name__}")
            write_audit_log(
                f"[BB_REGISTRY_ID_BEFORE] id={id(BB_ENGINE_REGISTRY)} "
                f"size={len(BB_ENGINE_REGISTRY)}"
            )

            self.kite_data = kite_data
            self.executor = executor
            self.config = config
            self.trade_mode = trade_mode
            self.broker_manager = broker_manager
            self._futures_subscribed = False

            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}]"
                f"[MODE={trade_mode}]"
                f"[COMPONENT=TickEngine][INIT]"
            )

            # -------------------------------------------------
            # RESOLVE FUTURES
            # -------------------------------------------------
            resolved = resolve_current_month_banknifty_fut()
            if not resolved:
                raise RuntimeError("Could not resolve BANKNIFTY FUT contract")

            self.fut_token, self.fut_symbol = resolved

            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}] "
                f"[INIT] FUTURES symbol={self.fut_symbol} token={self.fut_token}"
            )

            # Persist token so backfill can resolve this contract later.
            # This fires once per startup and never overwrites existing entries.

            _this_expiry = resolve_current_monthly_expiry()
            if _this_expiry:
                save_contract(self.fut_token, self.fut_symbol, _this_expiry)

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
            # Must run BEFORE IndicatorBundle so the DB has enough
            # clean 3m candles for ATR to seed correctly.
            # -------------------------------------------------
            self._warmup_intraday_history()

            # -------------------------------------------------
            # INSTRUMENTS
            # -------------------------------------------------
            self.monthly_expiry = resolve_current_monthly_expiry()
            self.instruments_df = load_instruments_df()

            self.option_tokens = {}
            self.last_strike_refresh_price = None

            # -------------------------------------------------
            # BUILDER
            # Seed last_candle_end_ts from the most recent warmup
            # candle so CandleBuilder aligns correctly even after
            # a long gap (e.g. Monday morning after Friday close).
            # Without this, on Monday the builder sees a 64-hour
            # gap and may mis-align the first live candle boundary.
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
                    write_audit_log(
                        f"[BB][BUILDER_SEED] last_warmup_ts={last_warmup_ts} "
                        f"({datetime.fromtimestamp(last_warmup_ts).strftime('%Y-%m-%d %H:%M')})"
                    )
                else:
                    write_audit_log("[BB][BUILDER_SEED] No warmup candles found — builder starts cold")
            except Exception as e:
                write_audit_log(f"[BB][BUILDER_SEED_ERROR] {e}")

            self.builder = CandleBuilder(
                instrument_token=self.fut_token,
                timeframe_sec=3 * 60,
                last_candle_end_ts=last_warmup_ts,
            )

            # IMPORTANT: IndicatorBundle created AFTER _warmup_intraday_history
            # so fetch_recent_candles sees the full 3m history.
            self.indicator_bundle = IndicatorBundle(self.fut_symbol)

            self.signal_engine = ConfluenceSignalEngine(
                max_trades_per_side=config.get("max_trades_per_side", 2),
                strategy_id=self.STRATEGY_ID,
            )

            # -------------------------------------------------
            # LIVE STATE MANAGERS
            # -------------------------------------------------
            if trade_mode == "LIVE":

                self.ce_state = BBTradeStateManager(
                    side="CE",
                    strategy_id=self.STRATEGY_ID,
                    executor=self.executor,
                    state_file=Path("state/bb_ce.json"),
                )

                self.pe_state = BBTradeStateManager(
                    side="PE",
                    strategy_id=self.STRATEGY_ID,
                    executor=self.executor,
                    state_file=Path("state/bb_pe.json"),
                )

                try:
                    if self.ce_state.in_trade:
                        self.signal_engine.ce_in_trade = True
                    if self.pe_state.in_trade:
                        self.signal_engine.pe_in_trade = True
                    write_audit_log(
                        f"[BB][STATE_SYNC] LIVE "
                        f"CE={self.signal_engine.ce_in_trade} "
                        f"PE={self.signal_engine.pe_in_trade}"
                    )
                except Exception as e:
                    write_audit_log(f"[BB][STATE_SYNC_ERROR] {e}")

            else:
                # PAPER mode — no file state managers, but restore
                # in_trade flags from the DB so restart doesn't lose
                # track of an open paper trade.
                self.ce_state = None
                self.pe_state = None

                try:
                    from app.db.paper_trades_repo import has_open_paper_trade_by_side

                    ce_open = has_open_paper_trade_by_side(
                        strategy_name=self.STRATEGY_ID,
                        side="CE",
                    )
                    pe_open = has_open_paper_trade_by_side(
                        strategy_name=self.STRATEGY_ID,
                        side="PE",
                    )

                    if ce_open:
                        self.signal_engine.ce_in_trade = True
                    if pe_open:
                        self.signal_engine.pe_in_trade = True

                    write_audit_log(
                        f"[BB][PAPER_STATE_SYNC] "
                        f"CE={self.signal_engine.ce_in_trade} "
                        f"PE={self.signal_engine.pe_in_trade}"
                    )

                except Exception as e:
                    write_audit_log(f"[BB][PAPER_STATE_SYNC_ERROR] {e}")

            # -------------------------------------------------
            # TRADE MANAGER
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

            # Back-reference so the trade manager can arm the live path on a
            # mid-session PAPER->LIVE flip (mirrors BB_V2). Without this,
            # self._engine stays None and _enter() refuses every live entry
            # after a flip with a misleading "trade session not ready".
            self.trade_manager._engine = self

            # -------------------------------------------------
            # GTT MONITOR  (LIVE only)
            # Polls Zerodha every 30s for GTT status changes.
            # Handles SL_HIT / TP_HIT mid-session without any
            # webhook dependency.  Started here so it is alive
            # from the moment the engine is constructed.
            # -------------------------------------------------
            self._gtt_monitor = None

            if trade_mode == "LIVE":
                self._gtt_monitor = GTTMonitor(
                    executor=self.executor,
                    signal_engine=self.signal_engine,
                    ce_state=self.ce_state,
                    pe_state=self.pe_state,
                    strategy_id=self.STRATEGY_ID,
                    trade_manager=self.trade_manager,
                    trade_mode=trade_mode,
                )
                self._gtt_monitor.start()

            write_audit_log(f"[STRATEGY={self.STRATEGY_ID}][ENGINE_READY]")

            if not any(isinstance(e, BBOptionsTickEngine) for e in BB_ENGINE_REGISTRY):
                BB_ENGINE_REGISTRY.append(self)

            write_audit_log(
                f"[BB_REGISTER_AFTER_APPEND] "
                f"id={id(BB_ENGINE_REGISTRY)} "
                f"size={len(BB_ENGINE_REGISTRY)}"
            )

        except Exception as e:
            write_audit_log(f"[BB_CONSTRUCTOR_FATAL] {repr(e)}")
            raise

    def start(self):
        # GTTMonitor is started inside __init__ for LIVE mode.
        # Start the FUT tick watchdog to detect silent tick starvation.
        self._last_fut_tick_ts = time.time()
        threading.Thread(
            target=self._fut_tick_watchdog,
            daemon=True,
            name="bb-fut-watchdog",
        ).start()

    # ==================================================
    # FUT TICK WATCHDOG
    # Zerodha occasionally stops delivering ticks for
    # individual tokens without dropping the WS.  This
    # thread detects that silence and forces a re-subscribe.
    # ==================================================

    def _fut_tick_watchdog(self):
        import time as _time

        # Grace period: don't fire during warmup / first subscription window
        _time.sleep(120)

        STALE_THRESHOLD = 180   # 3 minutes without a FUT tick = stale

        while True:
            _time.sleep(60)

            elapsed = _time.time() - self._last_fut_tick_ts

            if elapsed > STALE_THRESHOLD:
                write_audit_log(
                    f"[BB][WATCHDOG] No FUT tick for {int(elapsed)}s — "
                    f"forcing re-subscription of token={self.fut_token}"
                )
                # Reset the subscription flag so _ensure_futures_subscription
                # will re-subscribe on the next tick.  Also reset the option
                # map so _ensure_option_subscription rebuilds on next FUT tick.
                self._futures_subscribed = False
                self.last_strike_refresh_price = None

                # Directly call subscribe to avoid waiting for next tick
                engines = get_ws_engines()
                if engines:
                    try:
                        engines[0].subscribe_additional_tokens([self.fut_token])
                        self._futures_subscribed = True
                        write_audit_log(
                            f"[BB][WATCHDOG] Re-subscribed FUT token={self.fut_token}"
                        )
                    except Exception as e:
                        write_audit_log(f"[BB][WATCHDOG][ERROR] {e}")


    # ==================================================
    # 3M HISTORICAL WARMUP
    # ==================================================

    def _warmup_intraday_history(self):
        """
        Fetch recent 3m candles from Zerodha historical API and persist
        them to futures_candles (timeframe='3m') so that IndicatorBundle
        warmup has enough data for ATR/SuperTrend to converge.

        FIX 1: Use date() boundaries instead of datetime.now() so the
                Zerodha API window is always aligned to trading sessions
                regardless of what time the app restarts.

        FIX 2: Fetch _WARMUP_DAYS=7 calendar days back instead of 2,
                guaranteeing ≥ 3 trading days even across a long weekend.

        FIX 3: Use INSERT OR IGNORE so re-starts never overwrite live
                candle rows that already have indicator + signal columns
                populated.
        """

        write_audit_log(
            f"[STRATEGY={self.STRATEGY_ID}] "
            f"Fetching {self._WARMUP_DAYS}d of 3m historical candles for warmup"
        )

        try:
            # FIX 1: date boundaries — always full trading days, never
            # a partial window caused by the current time-of-day.
            end_date   = date.today()
            start_date = end_date - timedelta(days=self._WARMUP_DAYS)

            candles = self.kite_data.historical_data(
                instrument_token=self.fut_token,
                from_date=start_date,
                to_date=end_date,
                interval="3minute",
            )

            if not candles:
                write_audit_log("[BB][WARMUP] No 3m historical candles returned")
                return

            inserted = 0

            for c in candles:
                ts = int(c["date"].timestamp())

                # FIX 3: INSERT OR IGNORE — never clobber a live candle row
                # that already has indicators/signals populated.
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
                f"[BB][WARMUP] Loaded {len(candles)} candles from API, inserted={inserted}"
            )

        except Exception as e:
            write_audit_log(f"[BB][WARMUP_ERROR] {repr(e)}")

    # ==================================================
    # SAFE FUTURES SUBSCRIPTION
    # ==================================================

    def _ensure_futures_subscription(self):

        if self._futures_subscribed:
            return

        engines = get_ws_engines()
        if not engines:
            return

        ws_engine = engines[0]

        try:
            ws_engine.subscribe_additional_tokens([self.fut_token])
            self._futures_subscribed = True
            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}] "
                f"[FUTURES_SUBSCRIBED] token={self.fut_token}"
            )
        except Exception as e:
            write_audit_log(f"[BB][FUT_SUB_ERROR] {e}")

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
                (self.instruments_df["segment"] == "NFO-OPT")
                & (self.instruments_df["name"] == "BANKNIFTY")
                & (self.instruments_df["strike"] == strike)
                & (self.instruments_df["expiry"] == self.monthly_expiry)
            ]
            for _, row in df.iterrows():
                tok = int(row["instrument_token"])
                sym = str(row["tradingsymbol"])
                new_token_map[tok] = sym

        if not new_token_map:
            return

        # Merge into the instance map so on_tick can resolve symbol → LTPStore.
        # We do NOT subscribe all these tokens via WS — doing so (122 tokens,
        # MODE_FULL) floods the receive buffer and kills the connection for
        # all strategies.  Only the specific traded option token is subscribed
        # individually via _ensure_active_option_subscriptions().
        self.option_tokens.update(new_token_map)

        write_audit_log(
            f"[BB] Option token map built count={len(new_token_map)} ATM={atm} "
            f"(WS subscription deferred to active-trade tokens only)"
        )

    # ==================================================
    # WS RECONNECT HANDLER
    # Called by ZerodhaTickEngine._on_connect on every
    # WS connect/reconnect. Resets subscription flags so
    # futures + options are re-subscribed on next tick.
    # ==================================================

    def on_ws_reconnect(self):
        # _on_connect in ZerodhaTickEngine already re-subscribes self.builders.keys()
        # which includes the BB FUT token (injected by selection_engine at startup).
        # We must NOT reset _futures_subscribed here — doing so triggers
        # _ensure_futures_subscription to call subscribe_additional_tokens([fut_token])
        # via a background thread, which is a redundant subscription that causes
        # Zerodha to drop the connection (another 1006), creating a reconnect loop.
        #
        # last_strike_refresh_price is also kept — option tokens are not subscribed
        # via WS anyway (deferred mode), so there is nothing to re-establish.
        write_audit_log(
            f"[BB][WS_RECONNECT] WS reconnected — "
            f"FUT already re-subscribed by _on_connect, no action needed"
        )

    # ==================================================
    # DISPATCH ENTRY POINT
    # ==================================================

    def on_tick(self, token: int, ltp: float, ts: int):

        self._ensure_futures_subscription()

        if token == self.fut_token:
            LTPStore.update(self.fut_symbol, ltp)
            self._ensure_option_subscription(ltp)

            # Keep watchdog timestamp fresh
            self._last_fut_tick_ts = time.time()

            # Throttled diagnostic: confirm futures ticks are arriving
            if not hasattr(self, '_last_fut_tick_log') or ts - self._last_fut_tick_log >= 60:
                write_audit_log(
                    f"[BB][FUT_TICK] ltp={ltp} ts={ts}"
                )
                self._last_fut_tick_log = ts

            candle = self.builder.on_tick(ltp, ts)
            if candle:
                self._process_candle(candle)

        elif token in self.option_tokens:
            LTPStore.update(self.option_tokens[token], ltp)

    # ==================================================
    # PROCESS CANDLE
    # ==================================================

    def _process_candle(self, candle):

        indicators = self.indicator_bundle.update(candle)

        signal = self.signal_engine.update(
            close=candle.close,
            indicators=indicators,
            candle_open=candle.open,
        )

        insert_candle(
            symbol=self.fut_symbol,
            timeframe="3m",
            ts=candle.start_ts,
            open_=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            indicators=indicators,
            signal_action=signal.action,
            signal_reason=signal.reason,
            rejection_reason=signal.rejection_reason,
            ce_in_trade=self.signal_engine.ce_in_trade,
            pe_in_trade=self.signal_engine.pe_in_trade,
            ce_trades_today=self.signal_engine.ce_trades_today,
            pe_trades_today=self.signal_engine.pe_trades_today,
        )

        write_audit_log(
            f"[BB_SIGNAL] close={candle.close} "
            f"action={signal.action} "
            f"reason={signal.reason} "
            f"reject={signal.rejection_reason}"
        )

        if signal.action:
            self.trade_manager.handle_signal(signal)

        # Ensure the active trade's option token is subscribed on every candle,
        # not just on signal candles.  Far-OTM strikes (outside the ±30 ATM
        # window) never get ticks otherwise, breaking SL/TP checks.
        self._ensure_active_option_subscriptions()

        # FIX Bug 2: SL / TP monitoring for PAPER trades.
        # PaperTradeRecorder.try_exit() exists and is correct but was
        # never called anywhere — SL/TP only fired for EOD squareoff.
        # Check after every closed candle using candle.close as the LTP
        # proxy (WS option ticks may not be available at exact close time).
        if self.trade_mode == "PAPER":
            self._check_paper_sl_tp()

    # ==================================================
    # PAPER SL / TP MONITOR
    # Called after every closed candle in PAPER mode.
    # Primary source: LTPStore (WS tick).
    # Fallback: REST kite.ltp() for illiquid / far-OTM
    # strikes where WS ticks may never arrive.
    # ==================================================

    def _check_paper_sl_tp(self):

        # ── MTM RISK SQUARE-OFF (PAPER) ──
        # NOTE: get_all_open_paper_trades is imported at MODULE level. Do NOT
        # re-import it locally here — a local `from ... import
        # get_all_open_paper_trades` would make Python treat the name as a
        # function-local for the WHOLE method, so the later module-level use
        # below would raise "referenced before assignment" whenever this MTM
        # block is skipped. Only close_paper_trade (not module-level) is
        # imported locally.
        try:
            from app.risk.risk_mtm_guard import mtm_breach_bb
            reason = mtm_breach_bb(
                strategy_id=self.STRATEGY_ID,
                trade_mode="PAPER",
                ce_state=None,
                pe_state=None,
                executor=self.executor,
            )
            if reason:
                from app.db.paper_trades_repo import close_paper_trade
                write_audit_log(
                    f"[{self.STRATEGY_ID}][MTM_SQUAREOFF][PAPER] {reason} — "
                    f"closing open paper rows"
                )
                for t in get_all_open_paper_trades(self.STRATEGY_ID):
                    sym = t.get("symbol")
                    entry = t.get("entry_price")
                    ltp = LTPStore.get(sym)
                    exit_price = float(ltp) if ltp and ltp > 0 else float(entry or 0)
                    close_paper_trade(
                        paper_trade_id=t["paper_trade_id"],
                        exit_price=exit_price,
                        exit_reason="MAX_LOSS",
                    )
                return  # nothing left to SL/TP-check this candle
        except Exception as e:
            write_audit_log(f"[{self.STRATEGY_ID}][MTM_PAPER_CHECK_ERROR] {e}")

        try:
            open_trades = get_all_open_paper_trades(self.STRATEGY_ID)
        except Exception as e:
            write_audit_log(f"[BB][PAPER_SL_CHECK_ERROR] {e}")
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

            # ── LTP resolution: WS first, REST fallback ──────────
            if LTPStore.get(symbol) is None:
                try:
                    quote = self.kite_data.ltp(f"NFO:{symbol}")
                    rest_ltp = quote[f"NFO:{symbol}"]["last_price"]
                    if rest_ltp and rest_ltp > 0:
                        LTPStore.update(symbol, rest_ltp)
                        write_audit_log(
                            f"[BB][PAPER_LTP_REST] {symbol} ltp={rest_ltp} "
                            f"(WS unavailable, seeded from REST)"
                        )
                except Exception as e:
                    write_audit_log(
                        f"[BB][PAPER_LTP_REST_FAIL] {symbol} ERR={e}"
                    )
                    # Nothing we can do this candle — skip SL/TP check
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
                    f"[BB][PAPER_SL_FAILED] "
                    f"trade_id={paper_trade_id} symbol={symbol} ERR={e}"
                )

    # ==================================================
    # SUBSCRIBE SPECIFIC OPTION TOKENS FOR ACTIVE TRADES
    # Called after every handle_signal so far-OTM strikes
    # that were entered outside the ±30 ATM subscription
    # window still receive WS ticks for SL/TP monitoring.
    # ==================================================

    def _ensure_active_option_subscriptions(self):
        """
        Called every candle. Gathers all symbols currently in open trades
        and ensures their WS tokens are subscribed AND that LTPStore has a
        live price for them.

        Two-pass logic:
          1. If the token is not yet in self.option_tokens → subscribe it now.
          2. If the token IS in self.option_tokens but LTPStore still has no
             price (e.g. after a WS reconnect) → re-subscribe it.

        This fires every 3 minutes so the overhead is trivial.
        """
        symbols_needed = set()

        # LIVE — read from state managers
        for state in [self.ce_state, self.pe_state]:
            if state and state.active_trade and state.active_trade.symbol:
                symbols_needed.add(state.active_trade.symbol)

        # PAPER — read from DB
        if self.trade_mode == "PAPER":
            try:
                open_trades = get_all_open_paper_trades(self.STRATEGY_ID)
                for t in open_trades:
                    sym = t.get("symbol")
                    if sym:
                        symbols_needed.add(sym)
            except Exception:
                pass

        if not symbols_needed:
            return

        tokens_to_subscribe = []

        for sym in symbols_needed:
            # Resolve token from instruments_df
            df = self.instruments_df[
                self.instruments_df["tradingsymbol"] == sym
            ]
            if df.empty:
                write_audit_log(f"[BB][OPTION_SUB] Instrument not found: {sym}")
                continue

            tok = int(df.iloc[0]["instrument_token"])

            # Case 1: token not in our map at all → add and subscribe
            if tok not in self.option_tokens:
                self.option_tokens[tok] = sym
                tokens_to_subscribe.append(tok)
                write_audit_log(
                    f"[BB][OPTION_SUB] New subscription: {sym} token={tok}"
                )

            # Case 2: token is mapped but LTPStore has no price → re-subscribe
            elif LTPStore.get(sym) is None:
                tokens_to_subscribe.append(tok)
                write_audit_log(
                    f"[BB][OPTION_SUB] Re-subscribing (no LTP): {sym} token={tok}"
                )

        if not tokens_to_subscribe:
            return

        engines = get_ws_engines()
        if not engines:
            return

        try:
            engines[0].subscribe_additional_tokens(tokens_to_subscribe)
        except Exception as e:
            write_audit_log(f"[BB][OPTION_SUB_ERROR] {e}")

    # ==================================================
    # LIVE ARMING (mid-session PAPER->LIVE flip support)
    #
    # Idempotent. Builds the live machinery that __init__ only builds on a LIVE
    # start: the two state managers and the GTT monitor, plus (if the runtime
    # couldn't pre-build it) the executor. Returns True only when the engine is
    # fully armed for live trading; False means the caller (the trade manager's
    # live entry path) MUST refuse the entry — no order is placed.
    #
    # Safety: this never brings up a trade session. If the trade session isn't
    # already authenticated, it returns False and the entry is refused + alerted.
    # ==================================================
 
    def ensure_live_armed(self) -> bool:
        # Already armed? (state managers exist) -> nothing to do.
        if self.ce_state is not None and self.pe_state is not None:
            return True
 
        # Require an authenticated trade session. Never bring one up mid-trade.
        bm = getattr(self, "broker_manager", None)
        if bm is None:
            write_audit_log(
                f"[{self.STRATEGY_ID}][ARM_LIVE][REFUSE] no broker_manager handle "
                f"— cannot arm live; entry must be refused"
            )
            return False
        try:
            if not bm.is_trade_ready():
                write_audit_log(
                    f"[{self.STRATEGY_ID}][ARM_LIVE][REFUSE] trade session not "
                    f"ready — cannot arm live; entry must be refused"
                )
                return False
        except Exception as e:
            write_audit_log(
                f"[{self.STRATEGY_ID}][ARM_LIVE][REFUSE] is_trade_ready() error "
                f"{repr(e)} — entry must be refused"
            )
            return False
 
        # Build the executor if the runtime didn't pre-build one.
        if self.executor is None:
            try:
                from app.execution.zerodha_executor import ZerodhaOrderExecutor
                self.executor = ZerodhaOrderExecutor(bm)
                write_audit_log(
                    f"[{self.STRATEGY_ID}][ARM_LIVE] executor built on demand"
                )
            except Exception as e:
                write_audit_log(
                    f"[{self.STRATEGY_ID}][ARM_LIVE][REFUSE] executor build "
                    f"failed {repr(e)} — entry must be refused"
                )
                self.executor = None
                return False
 
        # Propagate the executor to the trade manager (it caches its own ref).
        try:
            self.trade_manager.executor = self.executor
        except Exception as e:
            write_audit_log(f"[{self.STRATEGY_ID}][ARM_LIVE][WARN] tm executor set: {e}")
 
        # Build the two state managers (this is what __init__ does on LIVE start).
        try:
            self.ce_state = BBTradeStateManager(
                side="CE",
                strategy_id=self.STRATEGY_ID,
                executor=self.executor,
                state_file=Path("state/bb_ce.json"),
            )
            self.pe_state = BBTradeStateManager(
                side="PE",
                strategy_id=self.STRATEGY_ID,
                executor=self.executor,
                state_file=Path("state/bb_pe.json"),
            )
        except Exception as e:
            write_audit_log(
                f"[{self.STRATEGY_ID}][ARM_LIVE][REFUSE] state manager build "
                f"failed {repr(e)} — entry must be refused"
            )
            self.ce_state = None
            self.pe_state = None
            return False
 
        # Re-attach state managers so the trade manager points at the live ones.
        try:
            self.trade_manager.attach_state_managers(
                ce_state=self.ce_state,
                pe_state=self.pe_state,
                signal_engine=self.signal_engine,
            )
        except Exception as e:
            write_audit_log(f"[{self.STRATEGY_ID}][ARM_LIVE][WARN] attach: {e}")
 
        # Sync signal-engine in_trade flags from any restored open trades.
        try:
            if self.ce_state.in_trade:
                self.signal_engine.ce_in_trade = True
            if self.pe_state.in_trade:
                self.signal_engine.pe_in_trade = True
        except Exception as e:
            write_audit_log(f"[{self.STRATEGY_ID}][ARM_LIVE][WARN] flag sync: {e}")
 
        # Start the GTT monitor if not already running.
        try:
            if getattr(self, "_gtt_monitor", None) is None:
                self._gtt_monitor = GTTMonitor(
                    executor=self.executor,
                    signal_engine=self.signal_engine,
                    ce_state=self.ce_state,
                    pe_state=self.pe_state,
                    strategy_id=self.STRATEGY_ID,
                    trade_manager=self.trade_manager,
                    trade_mode="LIVE",
                )
                self._gtt_monitor.start()
                write_audit_log(f"[{self.STRATEGY_ID}][ARM_LIVE] GTT monitor started")
        except Exception as e:
            # Non-fatal: GTTs are still placed per-trade; monitor is the poller.
            write_audit_log(
                f"[{self.STRATEGY_ID}][ARM_LIVE][WARN] GTT monitor start failed: {e}"
            )
 
        write_audit_log(f"[{self.STRATEGY_ID}][ARM_LIVE] live arming COMPLETE")
        return True

    # ==================================================
    # EOD SQUARE-OFF
    # Delegates to trade_manager which owns all broker
    # and state-clearing logic.  Called by bb_live_eod_job
    # via BB_ENGINE_REGISTRY at 15:25 IST.
    # ==================================================

    def eod_squareoff(self):
        try:
            self.trade_manager.eod_squareoff()
        except Exception as e:
            write_audit_log(f"[BB][EOD][ERROR] {repr(e)}")