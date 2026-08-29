from typing import Dict, List
import time
from datetime import date
import threading

from kiteconnect import KiteTicker, KiteConnect
from app.marketdata.rotating_ticker import RotatingKiteTicker  # ── TOKEN_ROTATE ──

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
from app.risk.risk_mtm_guard import mtm_breach_scalp_v1
from app.risk.strategy_max_loss_guard import _strategy_mode

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
        self._last_mtm_check_ts = 0.0
        self.kws = RotatingKiteTicker(          # ── TOKEN_ROTATE ──
            api_key=kite_data.api_key,
            access_token=kite_data.access_token,
            kind="data",
        )

        self._started   = False
        self._connected = False
        self._lock      = threading.Lock()
        self._extra_tokens: set = set()

        # ── WS tick-watchdog state (zombie-socket guard) ──
        self._last_tick_ts      = 0.0   # set once per _on_ticks batch
        self._last_connect_ts   = 0.0   # set in _on_connect
        self._last_wd_action_ts = 0.0   # last forced-reconnect attempt
        self._wd_strikes        = 0     # consecutive no-tick reconnect attempts

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


        # ── NEAR-ATM WARMUP BACKFILL (fail-open; never blocks warmup/signals) ──
        # V1 previously relied on V3/V4's backfill having run FIRST (all loops
        # are independent asyncio tasks — an ordering accident). V1 now heals
        # its own history before warming; idempotent: zero API calls when the
        # local candles are already complete.
        try:
            from app.engine.scalp_common.warmup_backfill import run_near_atm_backfill
            from app.fetcher.zerodha_instruments import load_instruments_df as _bf_df
            # ATM from spot LTP directly: this block runs BEFORE the token
            # loop populates token_expiry, so the universe-median fallback
            # had nothing (observed 2026-07-15 05:31:27 "could not resolve
            # ATM"). One quote call; on failure spot stays None and the
            # median fallback applies (by then still empty → skip, fail-open).
            _bf_spot = None
            try:
                _bf_spot = float(self.kite_data.ltp(["NSE:NIFTY 50"])
                                 ["NSE:NIFTY 50"]["last_price"])
            except Exception as _e:
                write_audit_log(f"[ENGINE][WARMUP_BF] spot LTP fetch failed: {_e!r}")
            run_near_atm_backfill(
                kite_data=self.kite_data,
                instruments_df=_bf_df(),
                option_tokens=list(self.token_expiry.keys()),
                current_week_expiry=self.current_week_expiry,
                spot_ltp=_bf_spot,
                include_today=True,
            )
        except Exception as e:
            write_audit_log(f"[ENGINE][WARMUP_BF_SKIP] {e!r} — proceeding with normal warmup")

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

            # ── SCALP_V1_EMA_GATE_20260824 ── gate params from SCALP_V1
            # config; enabled=false (the shipped default) constructs NO gate
            # object — live behavior byte-identical until enabled in Settings.
            _eg = {}
            try:
                from app.config.strategy_loader import load_strategy_config as _lsc
                _eg = (_lsc("SCALP_V1") or {}).get("ema_gate") or {}
            except Exception:
                _eg = {}
            indicator = IndicatorEnginePineV19(
                gate_ema_period=(int(_eg.get("period", 144) or 144)
                                 if _eg.get("enabled") else None),
                gate_slope_lookback=int(_eg.get("slope_lookback", 30) or 30))
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
            write_audit_log(f"[WS][FATAL] kws.connect exception: {e}")

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

                # Respect the app's WS-mutation freeze if it is set.
                try:
                    if (WS_MUTATION_FROZEN.is_set()
                            if hasattr(WS_MUTATION_FROZEN, "is_set")
                            else bool(WS_MUTATION_FROZEN)):
                        continue
                except Exception:
                    pass

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
            self._last_connect_ts = time.time()

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
        self._last_tick_ts = time.time() 
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp   = tick.get("last_price")

            if token is None or ltp is None:
                continue

            ts = int(time.time())

            # ── MTM risk square-off (throttled inside) ──
            self._maybe_mtm_squareoff()

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

                    # ── SCALP_V1_LIVE_CONFIGB_20260827 ── ATM skew gate, the
                    # live analogue of the backtest runner's gate. Runs only
                    # on a sell signal, before ANY routing. Fail-closed.
                    if signal.is_sell and is_option:
                        if not self._atm_skew_ok(symbol):
                            return


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

    # ── SCALP_V1_LIVE_CONFIGB_20260827 ─────────────────────────────────────
    def _atm_skew_ok(self, symbol: str) -> bool:
        """Config B's ATM skew gate for the LIVE/PAPER path.

        Sell the side the ATM pair prices as DEARER (invert=True): a CE sell
        needs the ATM CE dearer than the ATM PE by >= min_diff_pts, and a PE
        sell the mirror. Disabled -> always True.

        FAIL-CLOSED: any missing input (config, spot, ATM pair, quote) blocks
        the entry and audits. A blocked entry costs one trade; an unfiltered
        one costs whatever the filter existed to prevent.
        """
        try:
            from app.config.strategy_loader import load_strategy_config
            _sk = (load_strategy_config("SCALP_V1") or {}).get("atm_skew_filter") or {}
        except Exception as e:
            write_audit_log(f"[SCALP_V1][SKEW] config read failed ({e!r}) — BLOCKED")
            return False
        if not bool(_sk.get("enabled", False)):
            return True
        try:
            min_diff = float(_sk.get("min_diff_pts", 0) or 0)
        except (TypeError, ValueError):
            min_diff = 0.0
        invert = bool(_sk.get("invert", False))

        spot = None
        try:
            from app.marketdata.market_indices_state import MarketIndicesState
            spot = (MarketIndicesState.snapshot().get("NIFTY") or {}).get("ltp")
        except Exception:
            spot = None
        if spot is None:
            write_audit_log(f"[SCALP_V1][SKEW] no NIFTY spot — BLOCKED {symbol}")
            return False

        # ATM strike on the SAME expiry as the contract being sold, taken from
        # the instrument master so the strike step is never hardcoded.
        try:
            import pandas as pd  # noqa: F401  (df ops only)
            df = self.instruments_df
            row = df[df["tradingsymbol"] == symbol]
            if row.empty:
                write_audit_log(f"[SCALP_V1][SKEW] {symbol} not in master — BLOCKED")
                return False
            exp = row.iloc[0]["expiry"]
            same = df[(df["expiry"] == exp) & (df["name"] == "NIFTY")]
            ce = same[same["instrument_type"] == "CE"]
            pe = same[same["instrument_type"] == "PE"]
            common = set(ce["strike"]).intersection(set(pe["strike"]))
            if not common:
                write_audit_log(f"[SCALP_V1][SKEW] no complete ATM pair — BLOCKED {symbol}")
                return False
            k = min(common, key=lambda s: (abs(float(s) - float(spot)), float(s)))
            ce_sym = ce[ce["strike"] == k].iloc[0]["tradingsymbol"]
            pe_sym = pe[pe["strike"] == k].iloc[0]["tradingsymbol"]
        except Exception as e:
            write_audit_log(f"[SCALP_V1][SKEW] ATM resolve failed ({e!r}) — BLOCKED {symbol}")
            return False

        # One quote for both legs. LTPStore is not usable here: it only holds
        # SUBSCRIBED symbols, and the ATM pair is often outside the
        # premium-band universe (near expiry especially).
        try:
            q = self.kite_data.ltp([f"NFO:{ce_sym}", f"NFO:{pe_sym}"])
            ce_px = float(q[f"NFO:{ce_sym}"]["last_price"])
            pe_px = float(q[f"NFO:{pe_sym}"]["last_price"])
        except Exception as e:
            write_audit_log(f"[SCALP_V1][SKEW] ATM quote failed ({e!r}) — BLOCKED {symbol}")
            return False

        sk = pe_px - ce_px                      # same sign convention as the backtest
        diff = sk if symbol.endswith("CE") else -sk
        if invert:
            diff = -diff
        ok = diff > min_diff
        write_audit_log(
            f"[SCALP_V1][SKEW] {symbol} atm={k} ce={ce_px} pe={pe_px} sk={sk:.2f} "
            f"diff={diff:.2f} min={min_diff} invert={invert} -> "
            f"{'ALLOW' if ok else 'BLOCK'}")
        return ok
    
    def _maybe_mtm_squareoff(self):
            """
            Throttled (~3s) live-MTM risk check for SCALP_V1. On breach, square off
            all open SCALP_V1 slots (live) / open paper rows, reusing existing
            exit paths. Only meaningful for SCALP_V1 (this engine's strategy).

            DRIVER (revised — Decision A + B):
              The day-block is now LIVE-evaluated by risk_mtm_guard.is_day_blocked
              (it self-clears the instant the limit is raised or set to 0), so a
              mid-day limit change un-blocks immediately and this loop stops.

              Close-until-flat is preserved WITHOUT spam: keep re-running the close
              path while a breach is live OR the day-block is set, BUT only when
              there is actually something open to close. A flat, blocked strategy
              is a no-op (no log, no work) — which kills the every-3s
              "[SCALP_V1][MTM_SQUAREOFF]" spam when blocked-but-flat.

              The per-slot pending-GTT skip below still lets us retry a slot whose
              protective GTT hasn't landed yet: as long as that slot is OPEN,
              has_open_positions_scalp_v1() is True, so the loop keeps running.
            """
            if self.strategy_id != "SCALP_V1":
                return
            now = time.time()
            if now - self._last_mtm_check_ts < 3.0:
                return
            self._last_mtm_check_ts = now

            try:
                # ── ACC2_W3 ── MTM kill fires on the bound account (SCALP_V1)
                from app.execution.executor_factory import get_executor_for_strategy
                executor = get_executor_for_strategy("SCALP_V1")
            except Exception:
                executor = None

            # A fresh breach sets the latch; an already-latched day-block means a
            # prior breach may have left a slot unclosed (e.g. GTT was pending).
            try:
                reason = mtm_breach_scalp_v1(executor=executor)
            except Exception as e:
                write_audit_log(f"[SCALP_V1][MTM_CHECK_ERROR] {e}")
                return

            from app.risk.risk_mtm_guard import (
                is_day_blocked,
                has_open_positions_scalp_v1,
            )
            already_blocked = is_day_blocked("SCALP_V1")

            # Run the close loop only if EITHER a fresh breach fired this cycle OR
            # the day-block is already set.
            if not reason and not already_blocked:
                return

            # Decision B: even while (legitimately) blocked, only act when there is
            # actually something open. A flat, blocked strategy is a no-op — this
            # is what stops the every-3s square-off log spam.
            try:
                has_open = has_open_positions_scalp_v1()
            except Exception as e:
                write_audit_log(f"[SCALP_V1][MTM_OPEN_CHECK_ERR] {e}")
                has_open = True   # fail safe: assume open, let the close path run

            if not has_open:
                # Blocked but flat — nothing to square off. Stay silent.
                return

            write_audit_log(
                f"[SCALP_V1][MTM_SQUAREOFF] reason={reason} "
                f"day_blocked={already_blocked} — closing open position(s)"
            )

            mode = _strategy_mode("SCALP_V1")

            if mode == "PAPER":
                # Close every open paper row at current LTP via the same path EOD uses.
                try:
                    from app.db.paper_trades_repo import (
                        get_all_open_paper_trades, close_paper_trade,
                    )
                    for t in get_all_open_paper_trades("SCALP_V1"):
                        sym = t.get("symbol")
                        entry = t.get("entry_price")
                        ltp = LTPStore.get(sym)
                        exit_price = float(ltp) if ltp and ltp > 0 else float(entry or 0)
                        close_paper_trade(
                            paper_trade_id=t["paper_trade_id"],
                            exit_price=exit_price,
                            exit_reason="MAX_LOSS",
                        )
                except Exception as e:
                    write_audit_log(f"[SCALP_V1][MTM_PAPER_CLOSE_ERR] {e}")
                return

            # LIVE — force-exit each registered slot via existing _force_exit.
            try:
                from app.trading.trade_state_manager import TradeStateManager
                slots = TradeStateManager._REGISTRY.get("SCALP_V1", {})
                for slot_name, mgr in list(slots.items()):
                    at = getattr(mgr, "active_trade", None)
                    if at is None:
                        continue
                    # Skip a slot whose protective GTT hasn't landed yet — its
                    # fill-confirm thread is mid-flight, and force-closing now would
                    # race it (double buy-back + stray GTT). Because we re-run this
                    # loop every cycle while day-blocked, the slot WILL be closed on
                    # a later cycle once its GTT is in place.
                    if getattr(at, "gtt_id", None) is None:
                        write_audit_log(
                            f"[SCALP_V1][MTM_SKIP_PENDING] slot={slot_name} "
                            f"GTT not yet placed — deferring square-off one cycle"
                        )
                        continue
                    try:
                        mgr._force_exit("MAX_LOSS")
                    except Exception as e:
                        write_audit_log(
                            f"[SCALP_V1][MTM_FORCE_EXIT_ERR] slot={slot_name} ERR={e}"
                        )
            except Exception as e:
                write_audit_log(f"[SCALP_V1][MTM_LIVE_CLOSE_ERR] {e}")