# backend/app/engine/ha_options/ha_signal_engine.py

"""
HA Signal Engine
================

Hardened version with:
- Duplicate candle protection
- Out-of-order protection
- Candle gap reset
- Buffer integrity validation
- Safer debug logging
"""

from dataclasses import dataclass
from typing import Optional
from collections import deque

from app.indicators.heikin_ashi import HACandle
from app.event_bus.audit_logger import write_audit_log


EXPECTED_CANDLE_GAP_SECONDS = 60


# ──────────────────────────────────────────────────────────────────
# Signal result
# ──────────────────────────────────────────────────────────────────

@dataclass
class HAEntrySignal:
    should_enter: bool = False
    condition: Optional[str] = None
    sl_price: Optional[float] = None
    rejection: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Per-symbol evaluator
# ──────────────────────────────────────────────────────────────────

class HAConditionEvaluator:

    def __init__(self):

        # Rolling buffer
        self._buf: deque = deque(maxlen=3)

        # Last RED candle low
        self._last_red_low: Optional[float] = None

        # Last processed timestamp
        self._last_ts: Optional[int] = None

    # ─────────────────────────────────────────────────────────────
    # Reset evaluator
    # ─────────────────────────────────────────────────────────────

    def reset(self):

        self._buf.clear()

        self._last_red_low = None

        self._last_ts = None

        write_audit_log(
            "[HA][RESET] Evaluator state cleared"
        )

    # ─────────────────────────────────────────────────────────────
    # Push CLOSED candle
    # ─────────────────────────────────────────────────────────────

    def push(
        self,
        candle: HACandle,
        ema_low: Optional[float],
    ) -> HAEntrySignal:

        # ─────────────────────────────────────────────────────────
        # Integrity protection
        # ─────────────────────────────────────────────────────────

        if self._last_ts is not None:

            # Duplicate candle
            if candle.ts == self._last_ts:

                write_audit_log(
                    f"[HA][REJECT] DUPLICATE_CANDLE "
                    f"ts={candle.ts}"
                )

                return HAEntrySignal(
                    rejection="DUPLICATE_CANDLE"
                )

            # Reverse order
            if candle.ts < self._last_ts:

                write_audit_log(
                    f"[HA][REJECT] OUT_OF_ORDER_CANDLE "
                    f"current={candle.ts} "
                    f"last={self._last_ts}"
                )

                self.reset()

                return HAEntrySignal(
                    rejection="OUT_OF_ORDER_CANDLE"
                )

            # Candle gap
            if candle.ts > (
                self._last_ts +
                EXPECTED_CANDLE_GAP_SECONDS
            ):

                write_audit_log(
                    f"[HA][REJECT] CANDLE_GAP_RESET "
                    f"current={candle.ts} "
                    f"last={self._last_ts}"
                )

                self.reset()

                return HAEntrySignal(
                    rejection="CANDLE_GAP_RESET"
                )

        # Accept timestamp
        self._last_ts = candle.ts

        # Track RED candle low
        if candle.is_red:
            self._last_red_low = candle.low

        # Push buffer
        self._buf.append(candle)

        # Debug buffer
        #write_audit_log(
         #   f"[HA][BUF] "
          #  f"{[(c.ts, c.close) for c in self._buf]}"
        #)

        # EMA not ready
        if ema_low is None:

            return HAEntrySignal(
                rejection="EMA_NOT_READY"
            )

        # Warmup
        if len(self._buf) < 2:

            return HAEntrySignal(
                rejection="WARMING_UP"
            )

        # ─────────────────────────────────────────────────────────
        # Assign rolling references
        # ─────────────────────────────────────────────────────────

        N = self._buf[-1]
        N1 = self._buf[-2]
        N2 = self._buf[-3] if len(self._buf) >= 3 else None

        # ─────────────────────────────────────────────────────────
        # Buffer integrity assertions
        # ─────────────────────────────────────────────────────────

        assert N1.ts < N.ts

        if N2 is not None:
            assert N2.ts < N1.ts < N.ts

        # ─────────────────────────────────────────────────────────
        # COND1
        # N-1 RED touching EMA
        # N GREEN
        # ─────────────────────────────────────────────────────────

        if (
            N1.is_red
            and N1.touches_or_crosses_ema_low(ema_low)
            and N.is_green
        ):

            self._log_condition_debug(
                "COND1",
                N,
                N1,
                N2,
                ema_low,
            )

            return HAEntrySignal(
                should_enter=True,
                condition="COND1",
                sl_price=self._last_red_low,
            )

        # ─────────────────────────────────────────────────────────
        # COND2
        # N GREEN touching EMA
        # N-1 RED
        # ─────────────────────────────────────────────────────────

        if (
            N.is_green
            and N.touches_or_crosses_ema_low(ema_low)
            and N1.is_red
        ):

            self._log_condition_debug(
                "COND2",
                N,
                N1,
                N2,
                ema_low,
            )

            return HAEntrySignal(
                should_enter=True,
                condition="COND2",
                sl_price=self._last_red_low,
            )

        # ─────────────────────────────────────────────────────────
        # COND3
        # N GREEN touching EMA
        # N1 GREEN not touching EMA
        # N2 RED
        # ─────────────────────────────────────────────────────────

        if (
            N2 is not None
            and N.is_green
            and N.touches_or_crosses_ema_low(ema_low)
            and N1.is_green
            and not N1.touches_or_crosses_ema_low(ema_low)
            and N2.is_red
        ):

            self._log_condition_debug(
                "COND3",
                N,
                N1,
                N2,
                ema_low,
            )

            return HAEntrySignal(
                should_enter=True,
                condition="COND3",
                sl_price=self._last_red_low,
            )

        return HAEntrySignal(
            rejection=self._describe_rejection(
                N,
                N1,
                N2,
                ema_low,
            )
        )

    # ─────────────────────────────────────────────────────────────
    # Debug logger
    # ─────────────────────────────────────────────────────────────

    def _log_condition_debug(
        self,
        cond: str,
        N: HACandle,
        N1: HACandle,
        N2: Optional[HACandle],
        ema_low: float,
    ):

        # ── HA_COND_LOG_MUTE BEGIN ── muted 2026-07-15.
        # Warmup replay (universe × up to 100 candles via warmup_from_db →
        # evaluator.push) printed one [HA][CONDx] line per historical match,
        # flooding the audit log on every restart. Outcome is still visible
        # via [HA][SIGNAL_FIRED] / [HA][NO_ENTRY] in ha_tick_engine.
        # To re-enable: delete the `return` and uncomment the block below.
        return
        # write_audit_log(
        #     f"[HA][{cond}] "
        #     f"N(ts={N.ts},green={N.is_green},"
        #     f"low={N.low},close={N.close}) | "
        #     f"N1(ts={N1.ts},red={N1.is_red},"
        #     f"low={N1.low},close={N1.close}) | "
        #     f"N2(ts={N2.ts if N2 else None}) | "
        #     f"EMA={ema_low:.2f}"
        # )
        # ── HA_COND_LOG_MUTE END ──

    # ─────────────────────────────────────────────────────────────
    # Rejection helper
    # ─────────────────────────────────────────────────────────────

    def _describe_rejection(
        self,
        N: HACandle,
        N1: HACandle,
        N2: Optional[HACandle],
        ema_low: float,
    ) -> str:

        parts = []

        if not N.is_green:
            parts.append(
                f"N_RED(close={N.close:.2f})"
            )

        if not N.touches_or_crosses_ema_low(ema_low):
            parts.append(
                f"NO_EMA_TOUCH("
                f"low={N.low:.2f},"
                f"ema={ema_low:.2f})"
            )

        if not parts:
            parts.append("NO_PATTERN_MATCH")

        return " | ".join(parts)

    @property
    def last_red_low(self) -> Optional[float]:
        return self._last_red_low


# ──────────────────────────────────────────────────────────────────
# Trade state tracker
# ──────────────────────────────────────────────────────────────────

class HASignalEngine:
    """
    Tracks per-side in-trade state and daily trade counts.
    """

    def __init__(self, max_trades_per_side: int = 10):

        self.max_trades_per_side = max_trades_per_side

        self.ce_in_trade: bool = False
        self.pe_in_trade: bool = False

        self.ce_trades_today: int = 0
        self.pe_trades_today: int = 0

    # ─────────────────────────────────────────────────────────────
    # Daily reset
    # ─────────────────────────────────────────────────────────────

    def reset_daily(self):

        self.ce_trades_today = 0
        self.pe_trades_today = 0

        self.ce_in_trade = False
        self.pe_in_trade = False

        write_audit_log(
            "[HA_SIGNAL] Daily reset"
        )

    # ─────────────────────────────────────────────────────────────
    # Entry gate
    # ─────────────────────────────────────────────────────────────

    def can_enter(self, side: str) -> tuple:

        if side == "CE":

            if self.ce_in_trade:
                return False, "CE_ALREADY_IN_TRADE"

            if (
                self.ce_trades_today >=
                self.max_trades_per_side
            ):
                return False, "CE_MAX_TRADES_REACHED"

        elif side == "PE":

            if self.pe_in_trade:
                return False, "PE_ALREADY_IN_TRADE"

            if (
                self.pe_trades_today >=
                self.max_trades_per_side
            ):
                return False, "PE_MAX_TRADES_REACHED"

        return True, "OK"

    # ─────────────────────────────────────────────────────────────
    # State mutations
    # ─────────────────────────────────────────────────────────────

    def confirm_entry(self, side: str):

        if side == "CE":

            self.ce_in_trade = True
            self.ce_trades_today += 1

        elif side == "PE":

            self.pe_in_trade = True
            self.pe_trades_today += 1

    def notify_exit(self, side: str):

        if side == "CE":
            self.ce_in_trade = False

        elif side == "PE":
            self.pe_in_trade = False

    # ─────────────────────────────────────────────────────────────
    # SL / TP checks
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def sl_hit(
        candle_close: float,
        sl_price: float,
    ) -> bool:

        return candle_close <= sl_price

    @staticmethod
    def tp_hit(
        candle_close: float,
        tp_price: float,
    ) -> bool:

        return candle_close >= tp_price