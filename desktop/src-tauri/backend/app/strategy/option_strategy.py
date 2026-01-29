import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional


# =========================
# ===== CONFIG ============
# =========================

@dataclass
class StrategyConfig:
    timeframe: str = "1m"          # 1m, 3m, 5m etc
    rr_multiplier: float = 1.0     # 1:1 default
    min_sl_pts: float = 5.0        # same as Pine default
    trading_enabled: bool = True   # ON / OFF switch


# =========================
# ===== STATE =============
# =========================

@dataclass
class TradeState:
    in_trade: bool = False
    entry_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    entry_time: Optional[pd.Timestamp] = None


# =========================
# ===== STRATEGY ==========
# =========================

class OptionStrategyV19:
    """
    Implements Pine Script:
    1M Scalp V1.9
    (Option candles only)
    """

    def __init__(self, config: StrategyConfig):
        self.cfg = config
        self.state = TradeState()

    # --------------------------------------------------
    # ===== Indicators
    # --------------------------------------------------

    @staticmethod
    def ema(series: pd.Series, length: int) -> pd.Series:
        return series.ewm(span=length, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, length: int) -> pd.Series:
        return series.rolling(length).mean()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Computes EMA8, EMA20 Low/High, RSI (Wilder + smoothed)
        """

        # EMA 8 (EMA → SMA smoothed)
        ema8_raw = self.ema(df["close"], 8)
        df["ema8"] = self.sma(ema8_raw, 8)

        # EMA20 low/high (EMA → SMA smoothed)
        ema20_low_raw = self.ema(df["low"], 20)
        ema20_high_raw = self.ema(df["high"], 20)
        df["ema20_low"] = self.sma(ema20_low_raw, 9)
        df["ema20_high"] = self.sma(ema20_high_raw, 9)

        # RSI Wilder (len=5)
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1/5, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/5, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi_raw = 100 - (100 / (1 + rs))

        # RSI smoothed (len=5)
        df["rsi"] = self.sma(rsi_raw, 5)

        return df

    # --------------------------------------------------
    # ===== ENTRY CHECK
    # --------------------------------------------------

    def check_entry(self, df: pd.DataFrame) -> bool:
        """
        Uses ONLY the last CLOSED candle
        """

        if not self.cfg.trading_enabled:
            return False

        if self.state.in_trade:
            return False

        c = df.iloc[-1]
        p = df.iloc[-2]

        # 1. Green candle
        if c.close <= c.open:
            return False

        # 2. EMA conditions
        if not (c.close > c.ema8):
            return False

        if not (c.close >= c.ema20_low):
            return False

        if c.close > c.ema20_high:
            return False

        # 3. No touch of EMA20 high if ema8 < ema20_high
        if c.ema8 < c.ema20_high:
            if c.high >= c.ema20_high:
                return False
            if max(c.open, c.close) >= c.ema20_high:
                return False

        # 4. RSI range
        if not (40 <= c.rsi <= 65):
            return False

        # 5. RSI rising
        if not (c.rsi > p.rsi):
            return False

        return True

    # --------------------------------------------------
    # ===== SL CALCULATION
    # --------------------------------------------------

    @staticmethod
    def find_sl(df: pd.DataFrame, entry_price: float) -> Optional[float]:
        """
        SL = Low of most recent RED candle
        (exclude current candle)
        """

        for i in range(len(df) - 2, -1, -1):
            row = df.iloc[i]
            if row.close < row.open and row.low < entry_price:
                return row.low

        return None

    # --------------------------------------------------
    # ===== PROCESS BAR
    # --------------------------------------------------

    def process(self, df: pd.DataFrame) -> Optional[dict]:
        """
        Call this every candle close.
        Returns:
        - BUY
        - EXIT (TP / SL)
        - None
        """

        df = self.compute_indicators(df)

        c = df.iloc[-1]

        # ===== ENTRY =====
        if self.check_entry(df):
            sl = self.find_sl(df, c.close)
            if sl is None:
                return None

            sl_dist = c.close - sl
            if sl_dist < self.cfg.min_sl_pts:
                return None

            self.state = TradeState(
                in_trade=True,
                entry_price=c.close,
                sl=sl,
                tp=c.close + sl_dist * self.cfg.rr_multiplier,
                entry_time=c.name
            )

            return {
                "signal": "BUY",
                "price": c.close,
                "sl": self.state.sl,
                "tp": self.state.tp
            }

        # ===== EXIT =====
        if self.state.in_trade:
            if c.low <= self.state.sl:
                exit_price = self.state.sl
                reason = "SL"
            elif c.high >= self.state.tp:
                exit_price = self.state.tp
                reason = "TP"
            else:
                return None

            self.state = TradeState()  # reset

            return {
                "signal": "EXIT",
                "price": exit_price,
                "reason": reason
            }

        return None
