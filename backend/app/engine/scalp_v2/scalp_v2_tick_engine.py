# backend/app/engine/scalp_v2/scalp_v2_tick_engine.py
#
# SCALP_V2 — Tick Engine (Option 1: dedicated engine, shared KiteTicker)
# ============================================================================
# Mirrors the SCALP_V1 ZerodhaTickEngine STRUCTURE (warmup, CandleBuilder,
# per-token StrategyEngine, IndicatorEnginePineV19, ConditionEngineV19) but:
#
#   - Watches ALL SCALP_V2-selected contracts across classes A/B/C.
#   - Generates the SAME SELL signal as SCALP_V1 (cloned entry logic via the
#     shared StrategyEngine class — no fork of the signal math).
#   - Does NOT route to SignalRouter / TradeStateManager. Instead:
#       * valid SELL signal  -> group_manager.try_elect_and_enter(...)
#       * every option tick   -> group_manager.on_tick(token, ltp)  (exit driver)
#
# ISOLATION:
#   - Registers on the shared KiteTicker via register_ws_engine, exactly like
#     BB/HA, so there is no second KiteTicker instance.
#   - Touches no SCALP_V1 / BB / HA state. The per-token StrategyEngine
#     instances here are V2-private (their own in_trade flags are unused —
#     the group manager owns trade state, Model B).
#   - SCALP_V1's engine, router, and zerodha_tick_engine.py are NOT modified.
#
# IMPORTANT — StrategyEngine.in_trade:
#   StrategyEngine tracks its own in_trade and computes exits. For V2 we only
#   want its ENTRY signal; the group manager owns exits. So after reading a
#   SELL signal we immediately reset the engine (engine._reset()) so it never
#   self-manages an exit or blocks future entry signals. The group gate
#   (all-legs-free) is what actually controls re-entry.
# ============================================================================

import json
import time
import threading
from pathlib import Path
from datetime import date
from typing import Dict, List

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
from app.utils.market_hours import is_market_open
from app.marketdata.ws_registry import register_ws_engine

from app.config.strategy_loader import load_strategy_config
from app.risk.strategy_max_loss_guard import evaluate_strategy_risk


STRATEGY_ID = "SCALP_V2"
STATE_DIR   = Path.home() / ".scalp-app" / "state"
CLASSES     = ("A", "B", "C")


def _timeframe_str(timeframe_sec: int) -> str:
    minutes = timeframe_sec // 60
    return f"{minutes}m" if minutes > 0 else f"{timeframe_sec}s"


# ============================================================================
# Selection provider — reads class-suffixed selection files (Option X)
# Naming convention (written by the V2 selection loop, Step 4b):
#   SCALP_V2_A_selected_ce.json / SCALP_V2_A_selected_pe.json, _B_, _C_
# Each file: JSON array of {symbol|tradingsymbol} rows (same as SCALP_V1).
# ============================================================================

def read_class_selection(trade_class: str, side: str) -> List[str]:
    fname = f"{STRATEGY_ID}_{trade_class}_selected_{side.lower()}.json"
    fpath = STATE_DIR / fname
    out: List[str] = []
    try:
        if fpath.exists():
            for row in json.loads(fpath.read_text()):
                sym = row.get("symbol") or row.get("tradingsymbol")
                if sym:
                    out.append(sym)
    except Exception as e:
        write_audit_log(f"[V2_ENGINE][SELECTION_WARN] {fname} ERR={e}")
    return out


class ScalpV2TickEngine:
    """
    Dedicated SCALP_V2 engine sharing the single KiteTicker.
    """

    WARMUP_CANDLES = 500

    def __init__(
        self,
        kite_data: KiteConnect,
        instrument_tokens: List[int],
        group_manager,
        timeframe_sec: int = 60,
    ):
        register_ws_engine(self)

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

        instruments_df = load_instruments_df()

        # -------------------------------------------------
        # WEEKLY EXPIRY (current week only — same rule as SCALP_V1)
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
        # PER-TOKEN STATE (V2-private StrategyEngine instances)
        # -------------------------------------------------
        self.token_expiry: Dict[int, date] = {}
        self.token_symbol: Dict[int, str]  = {}
        self.builders    = {}
        self.indicators  = {}
        self.strategies  = {}

        self.condition_engine = ConditionEngineV19()

        for token in instrument_tokens:
            rows = instruments_df.loc[instruments_df["instrument_token"] == token]
            if rows.empty:
                continue
            row    = rows.iloc[0]
            symbol = row["tradingsymbol"]

            # Only options participate.
            if not (str(symbol).endswith("CE") or str(symbol).endswith("PE")):
                continue

            self.token_expiry[token] = row["expiry"]
            self.token_symbol[token] = symbol

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
        # token resolver for group manager (candidate_provider)
        # symbol -> token (built from the df once)
        # -------------------------------------------------
        self._symbol_token: Dict[str, int] = {
            sym: tok for tok, sym in self.token_symbol.items()
        }

        # WS callbacks
        self.kws.on_ticks   = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close   = self._on_close
        self.kws.on_error   = self._on_error

    # ==================================================
    # Providers handed to the group manager
    # ==================================================

    def selection_provider(self, trade_class: str, side: str) -> List[str]:
        return read_class_selection(trade_class, side)

    def candidate_provider(self, symbol: str) -> int:
        """symbol -> instrument_token. Falls back to df lookup if not cached."""
        tok = self._symbol_token.get(symbol)
        if tok is not None:
            return tok
        try:
            df  = load_instruments_df()
            row = df.loc[df["tradingsymbol"] == symbol]
            if not row.empty:
                tok = int(row.iloc[0]["instrument_token"])
                self._symbol_token[symbol] = tok
                return tok
        except Exception as e:
            write_audit_log(f"[V2_ENGINE][TOKEN_RESOLVE_FAIL] {symbol} ERR={e}")
        return 0

    # ==================================================
    # START / CONNECT
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
        tokens = list(self.builders.keys())
        extra  = [t for t in self._extra_tokens if t not in tokens]
        all_tokens = tokens + extra
        ws.subscribe(all_tokens)
        ws.set_mode(ws.MODE_FULL, all_tokens)
        write_audit_log(
            f"[V2_ENGINE][WS] Connected — subscribed {len(all_tokens)} tokens "
            f"({len(tokens)} option + {len(extra)} extra)"
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

            if token not in self.builders:
                continue

            symbol = self.token_symbol.get(token)
            if not symbol:
                continue

            LTPStore.update(symbol, ltp)

            # --------------------------------------------------
            # PRIMARY EXIT DRIVER: feed every option tick to the
            # group manager so it can detect first-leg TP/SL crosses
            # sub-second and run the 15s stagger FSM.
            # --------------------------------------------------
            try:
                self.group_manager.on_tick(token, ltp)
            except Exception as e:
                write_audit_log(f"[V2_ENGINE][ON_TICK_ERROR] {symbol} ERR={e}")

            # --------------------------------------------------
            # CANDLE BUILD + ENTRY SIGNAL EVALUATION
            # --------------------------------------------------
            builder = self.builders[token]
            builder.last_price = ltp
            candle = builder.on_tick(ltp, int(time.time()))
            if not candle:
                continue

            self._process_candle_async(token, symbol, candle)

    def _process_candle_async(self, token, symbol, candle):
        def _work(
            token=token, symbol=symbol, candle=candle,
            ind_engine=self.indicators[token],
            strategy=self.strategies[token],
            token_expiry=self.token_expiry.get(token),
        ):
            try:
                ind_vals = ind_engine.update(candle)
                if not ind_engine.is_ready():
                    return

                conditions = self.condition_engine.evaluate(
                    candle=candle,
                    indicators=ind_vals,
                    is_trading_time=True,
                    no_open_trade=True,   # group gate controls entry, not the engine
                )

                signal = strategy.on_candle(candle, ind_engine, conditions)

                # Only the ENTRY (SELL) signal matters here. The group manager
                # owns exits; reset the engine immediately so it never tracks
                # its own in_trade state or emit an exit.
                is_sell = getattr(signal, "is_sell", False)
                try:
                    strategy._reset()
                except Exception:
                    pass

                if not is_sell:
                    return

                # Current-week expiry gate (same as SCALP_V1).
                if self.current_week_expiry is None or token_expiry != self.current_week_expiry:
                    return

                # Session / global trade_on gate (lightweight, mirrors router).
                if not self._entry_allowed():
                    return

                write_audit_log(
                    f"[V2_ENGINE][SELL_SIGNAL] {symbol} entry={signal.entry_price} "
                    f"sl={signal.sl} tp={signal.tp} → election"
                )

                self.group_manager.try_elect_and_enter(
                    symbol=symbol,
                    token=token,
                    entry_price=signal.entry_price,
                    sl_price=signal.sl,
                    tp_price=signal.tp,
                    candle_ts=candle.end_ts,
                )

            except Exception as e:
                write_audit_log(f"[V2_ENGINE][CANDLE_ERROR] {symbol} ERR={e}")

        threading.Thread(target=_work, daemon=True).start()

    # ==================================================
    # ENTRY GATE (global trade_on + session)
    # ==================================================

    def _entry_allowed(self) -> bool:
        try:
            from app.config.global_loader import load_global_config
            if not load_global_config().get("trade_on", False):
                return False
        except Exception:
            return False

        # Per-strategy daily Max Loss / Max Profit (block-only).
        try:
            if evaluate_strategy_risk(STRATEGY_ID):
                return False
        except Exception:
            return False   # fail closed

        try:
            from app.utils.session_utils import is_within_session
            from datetime import datetime
            cfg = load_strategy_config(STRATEGY_ID)
            sess = (cfg.get("session") or {}).get("primary")
            if sess:
                if not is_within_session(datetime.now(), sess.get("start"), sess.get("end")):
                    return False
        except Exception:
            pass
        return True

    def get_ltp(self, symbol: str):
        return LTPStore.get(symbol)