# backend/app/backtest/bb/bt_indicator_driver.py
#
# Drives the REAL BB indicator bundle + confluence signal engine over historical
# 3m bars, so backtest signals are identical to live BY CONSTRUCTION (we reuse
# the live classes, not a reimplementation).
#
# Two things differ from live, handled here:
#   1) WARMUP: live bundle.__init__ calls _warmup() which reads the live
#      futures_candles DB. In backtest we suppress that (stub fetch_recent_candles
#      to return []) and instead warm up by feeding prior-day corpus 3m bars.
#   2) PIVOTS: live bundle pulls pivots once from PivotCache (live kite). In
#      backtest we SET bundle.pp/r1/r2/s1/s2/s3 directly each sim-day from
#      corpus-computed prior-day pivots, resetting at each day boundary.
#
# V1 vs V2 differences are branched on strategy_id:
#   - bundle class: IndicatorBundle vs IndicatorBundleV2
#   - signal engine: ConfluenceSignalEngine vs ConfluenceSignalEngineV2
#   - engine.update signature: V1 takes candle_open=, V2 does not

from __future__ import annotations
from contextlib import contextmanager
from typing import Dict, Optional, Tuple


@contextmanager
def _suppress_live_warmup(modpaths):
    """Temporarily stub fetch_recent_candles in the given bundle modules so the
    bundle's __init__ _warmup() finds no rows (we warm from the corpus instead)."""
    import importlib
    saved = []
    for mp in modpaths:
        try:
            mod = importlib.import_module(mp)
            saved.append((mod, mod.fetch_recent_candles))
            mod.fetch_recent_candles = lambda *a, **k: []
        except Exception:
            pass
    try:
        yield
    finally:
        for mod, fn in saved:
            mod.fetch_recent_candles = fn


class BBSignalReplay:
    """Feed 3m bars; get (indicators, signal) from the real engine."""

    def __init__(self, strategy_id: str, symbol: str,
                 max_trades_per_side: int = 10):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.is_v2 = (strategy_id == "BB_V2")

        # Import the REAL classes. (In the packaged app these resolve to the live
        # modules; here in the driver we import lazily so tests can inject stubs.)
        from app.marketdata.candle import Candle
        self._Candle = Candle

        if self.is_v2:
            from app.engine.bb_v2.indicator_bundle_v2 import IndicatorBundleV2 as Bundle
            from app.engine.bb_v2.confluence_signal_engine_v2 import ConfluenceSignalEngineV2 as Engine
            bundle_modpaths = ["app.engine.bb_v2.indicator_bundle_v2"]
            self.engine = Engine(max_trades_per_side=max_trades_per_side)
        else:
            from app.engine.bb_options.indicator_bundle import IndicatorBundle as Bundle
            from app.engine.bb_options.confluence_signal_engine import ConfluenceSignalEngine as Engine
            bundle_modpaths = ["app.engine.bb_options.indicator_bundle"]
            self.engine = Engine(max_trades_per_side=max_trades_per_side,
                                 strategy_id=strategy_id)

        # Construct the bundle WITHOUT live warmup.
        with _suppress_live_warmup(bundle_modpaths):
            self.bundle = Bundle(symbol)

    # --- pivots: set per sim-day (prev-day pivots), reset across days ---
    def set_day_pivots(self, pivots: Optional[Dict[str, float]]):
        b = self.bundle
        if not pivots:
            return
        b.pp = pivots.get("pp")
        b.r1 = pivots.get("r1")
        b.s1 = pivots.get("s1")
        if self.is_v2:
            b.r2 = pivots.get("r2")
            b.s2 = pivots.get("s2")
            b.s3 = pivots.get("s3")

    def reset_daily(self):
        """Match live 09:15 reset of the signal engine's daily counters."""
        try:
            self.engine.reset_daily()
        except Exception:
            pass

    def feed(self, start_ts: int, o: float, h: float, l: float, c: float,
             act: bool = True) -> Tuple[dict, object]:
        """Feed one 3m bar. Returns (indicators, signal). If act=False (warmup),
        we still update the bundle but ignore the signal."""
        candle = self._Candle(start_ts=start_ts, end_ts=start_ts + 180,
                              open=o, high=h, low=l, close=c, source="warmup")
        ind = self.bundle.update(candle)
        if not act:
            return ind, None
        if self.is_v2:
            sig = self.engine.update(close=c, indicators=ind)
        else:
            sig = self.engine.update(close=c, indicators=ind, candle_open=o)
        return ind, sig

    # signal-engine state callbacks (the runner calls these on entry/exit so the
    # engine's in_trade flags track the backtest's positions, exactly like live)
    def confirm_entry(self, side: str):
        self.engine.confirm_entry(side)

    def notify_exit(self, side: str):
        self.engine.notify_exit(side)