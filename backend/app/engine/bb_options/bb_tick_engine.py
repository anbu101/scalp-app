import time
import inspect
from pathlib import Path

from kiteconnect import KiteConnect

from app.engine.bb_options.futures_resolver import (
    resolve_current_month_nifty_fut,
)
from app.candles.candle_builder import CandleBuilder
from app.event_bus.audit_logger import write_audit_log
from app.engine.bb_options.daily_futures_loader import load_recent_daily_futures
from app.db.futures_candles_repo import init_table, insert_candle
from app.engine.bb_options.monthly_expiry_resolver import resolve_current_monthly_expiry
from app.fetcher.zerodha_instruments import load_instruments_df
from app.marketdata.ltp_store import LTPStore

from app.db.paper_trades_repo import (
    get_all_open_paper_trades,
    get_open_paper_trades_for_symbol,
)
from app.trading.paper_trade_recorder import PaperTradeRecorder

from app.engine.bb_options.indicator_bundle import IndicatorBundle
from app.engine.bb_options.confluence_signal_engine import ConfluenceSignalEngine
from app.engine.bb_options.bb_trade_manager import BBTradeManager
from app.engine.bb_options.bb_trade_state_manager import BBTradeStateManager

from app.marketdata.ws_registry import get_ws_engines
from app.core.engine_registry import BB_ENGINE_REGISTRY


class BBOptionsTickEngine:

    STRATEGY_ID = "BB_V1"

    def __init__(
        self,
        kite_data: KiteConnect,
        executor,
        config: dict,
        trade_mode: str,
    ):

        try:

            # =================================================
            # HARD PROOF — WHICH FILE IS EXECUTING
            # =================================================
            write_audit_log(
                f"[BB_RUNTIME_FILE] {inspect.getfile(BBOptionsTickEngine)}"
            )
            write_audit_log(
                f"[BB_MODULE_NAME] {__name__}"
            )
            write_audit_log(
                f"[BB_REGISTRY_ID_BEFORE] "
                f"id={id(BB_ENGINE_REGISTRY)} "
                f"size={len(BB_ENGINE_REGISTRY)}"
            )

            # =================================================
            # BASIC STATE
            # =================================================
            self.kite_data = kite_data
            self.executor = executor
            self.config = config
            self.trade_mode = trade_mode
            self._futures_subscribed = False

            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}]"
                f"[MODE={trade_mode}]"
                f"[COMPONENT=TickEngine][INIT]"
            )

            # =================================================
            # RESOLVE FUTURES
            # =================================================
            resolved = resolve_current_month_nifty_fut()
            if not resolved:
                raise RuntimeError("Could not resolve NIFTY FUT contract")

            self.fut_token, self.fut_symbol = resolved

            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}]"
                f"[INIT] FUTURES symbol={self.fut_symbol} token={self.fut_token}"
            )

            # =================================================
            # DB INIT
            # =================================================
            init_table()

            load_recent_daily_futures(
                kite=self.kite_data,
                instrument_token=self.fut_token,
                symbol=self.fut_symbol,
            )

            # =================================================
            # INSTRUMENTS
            # =================================================
            self.monthly_expiry = resolve_current_monthly_expiry()
            self.instruments_df = load_instruments_df()

            self.option_tokens = {}
            self.last_strike_refresh_price = None

            # =================================================
            # 3M BUILDER
            # =================================================
            self.builder = CandleBuilder(
                instrument_token=self.fut_token,
                timeframe_sec=3 * 60,
                last_candle_end_ts=None,
            )

            self.indicator_bundle = IndicatorBundle(self.fut_symbol)

            self.signal_engine = ConfluenceSignalEngine(
                max_trades_per_side=config.get("max_trades_per_side", 2)
            )

            # =================================================
            # LIVE STATE MANAGERS
            # =================================================
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
            else:
                self.ce_state = None
                self.pe_state = None

            # =================================================
            # TRADE MANAGER
            # =================================================
            self.trade_manager = BBTradeManager(
                strategy_id=self.STRATEGY_ID,
                trade_mode=trade_mode,
                executor=self.executor,
                symbol_fut=self.fut_symbol,
                lot_size=65,
                lot_count=1,
                sl_percent=config.get("sl_pct", 0),
                tp_percent=config.get("tp_pct", 0),
                max_premium=config.get("max_premium", 300),
                scan_strikes=10,
                config=config,
            )

            if trade_mode == "LIVE":
                self.trade_manager.attach_state_managers(
                    ce_state=self.ce_state,
                    pe_state=self.pe_state,
                )

            write_audit_log(
                f"[STRATEGY={self.STRATEGY_ID}][ENGINE_READY]"
            )

            # =================================================
            # CRITICAL REGISTRATION
            # =================================================
            BB_ENGINE_REGISTRY.append(self)

            write_audit_log(
                f"[BB_REGISTER_AFTER_APPEND] "
                f"id={id(BB_ENGINE_REGISTRY)} "
                f"size={len(BB_ENGINE_REGISTRY)}"
            )

        except Exception as e:
            write_audit_log(f"[BB_CONSTRUCTOR_FATAL] {repr(e)}")
            raise

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
                f"[STRATEGY={self.STRATEGY_ID}]"
                f"[FUTURES_SUBSCRIBED] token={self.fut_token}"
            )
        except Exception as e:
            write_audit_log(f"[BB][FUT_SUB_ERROR] {e}")

    # ==================================================
    # DISPATCH ENTRY POINT
    # ==================================================

    def on_tick(self, token: int, ltp: float, ts: int):

        self._ensure_futures_subscription()

        if token == self.fut_token:

            LTPStore.update(self.fut_symbol, ltp)

            candle = self.builder.on_tick(ltp, ts)

            if candle:
                self._process_candle(candle)

    # ==================================================
    # PROCESS CANDLE
    # ==================================================

    def _process_candle(self, candle):

        indicators = self.indicator_bundle.update(candle)

        signal = self.signal_engine.update(
            close=candle.close,
            indicators=indicators,
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
            f"[BB_SIGNAL] "
            f"close={candle.close} "
            f"action={signal.action} "
            f"reason={signal.reason} "
            f"reject={signal.rejection_reason}"
        )


        if signal.action:
            self.trade_manager.handle_signal(signal)
