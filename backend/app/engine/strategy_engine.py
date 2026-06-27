# backend/app/engine/strategy_engine.py
#
# SCALP_V1 — Option SHORT SELLING mode
#
# DESYNC FIX (mode-aware): on_candle() no longer self-asserts in_trade at
# signal time. Truth comes from the recorded trade:
#   LIVE  → TradeStateManager registry slot holding this symbol in a live state
#   PAPER → open paper_trades row (state='OPEN' AND exit_price IS NULL)
# A router-dropped signal leaves NO engine state, so the symbol stays free.

from dataclasses import dataclass
from typing import Optional
from datetime import date, timedelta

from app.event_bus.audit_logger import write_audit_log
from app.utils.candle_debug_logger import CandleDebugLogger
from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19


# Live-holding states (mirror trade_state_manager constants; keep in sync).
_LIVE_OPEN_STATES = ("BUY_PLACED", "SELL_PLACED", "PROTECTED")


@dataclass
class Signal:
    is_sell: bool = False        # SCALP_V1 short entry signal
    is_exit: bool = False
    exit_reason: Optional[str] = None
    entry_price: Optional[float] = None
    sl: Optional[float] = None   # above entry — premium rising = loss
    tp: Optional[float] = None   # below entry — premium falling = profit


class StrategyEngine:
    """
    Pine-parity SHORT SELL engine (OPTION chart only).

    HARD RULE:
    ✅ Trade ONLY current-week expiry
    ❌ Ignore next-week expiry

    IN-TRADE TRUTH:
    self.in_trade is NEVER self-asserted at signal time. It is derived from the
    recorded trade — TradeStateManager registry (LIVE) or paper_trades (PAPER) —
    so a router-dropped signal cannot leave a phantom position.

    SL PARAMETERS (terminology — config JSON keys unchanged for on-disk safety):
      RISK_MIN_SL  (json: min_sl_points)
          Floor on risk_distance. Skip the entry if risk_distance < RISK_MIN_SL.
      RISK_MAX_SL  (json: risk_max_sl_points)
          Ceiling on risk_distance. Skip the entry if risk_distance > RISK_MAX_SL.
          0 = disabled. Independent of MAX_SL_CAP — this REJECTS the trade.
      MAX_SL_CAP   (json: max_sl_points)
          Clamp on the final sl_price (entry + max_sl_cap). 0/None = disabled.
          Does NOT reject the trade; only caps the stop. Independent of RISK_MAX_SL.
    """

    MIN_RR       = 0.1
    RISK_MIN_SL  = 5.0   # was MIN_SL — floor on risk_distance (json: min_sl_points)
    RISK_MAX_SL  = 0.0   # ceiling on risk_distance (json: risk_max_sl_points); 0 = disabled

    def __init__(self, strategy_id: str, slot_name: str, symbol: str):
        self.strategy_id = strategy_id
        self.slot_name   = slot_name
        self.symbol      = symbol

        # CACHE of recorded-trade truth, refreshed each on_candle().
        self.in_trade    = False
        self.entry_price = None
        self.sl          = None   # above entry
        self.tp          = None   # below entry (= prev red candle low)

        self.debug_logger = CandleDebugLogger(
            symbol=symbol,
            slot=slot_name,
        )

    # =========================
    # Public API
    # =========================

    def on_candle(self, candle, ind: IndicatorEnginePineV19, conditions: dict) -> Signal:
        signal = Signal()
        snap   = ind.snapshot()

        # ── DEBUG LOG (every candle) ──────────────────────────
        self.debug_logger.log(
            candle_ts=candle.end_ts,
            o=candle.open,
            h=candle.high,
            l=candle.low,
            c=candle.close,
            ind=snap or {},
            checks=conditions,
            buy_allowed=conditions.get("cond_all", False),
        )

        # ── SYNC in_trade WITH RECORDED TRUTH ─────────────────
        self._refresh_in_trade()

        # ── EXIT LOGIC (only when a REAL recorded trade exists) ─────────
        if self.in_trade:
            if self.sl is not None and candle.high >= self.sl:
                signal.is_exit     = True
                signal.exit_reason = "SL"
                write_audit_log(
                    f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                    f"EXIT_SL (high={candle.high} >= sl={self.sl})"
                )
                self._reset()
                return signal

            if self.tp is not None and candle.low <= self.tp:
                signal.is_exit     = True
                signal.exit_reason = "TP"
                write_audit_log(
                    f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                    f"EXIT_TP (low={candle.low} <= tp={self.tp})"
                )
                self._reset()
                return signal

            return signal

        # ── ENTRY LOGIC ───────────────────────────────────────

        # Must be a green candle
        if candle.close <= candle.open:
            return signal

        # Indicators must be ready
        if snap is None:
            return signal

        # All conditions gate
        if not conditions.get("cond_all"):
            return signal

        # Must be current-week expiry symbol
        if not self._is_current_week_expiry():
            return signal

        # ── SL distance from previous red candle's LOW ────────
        prev_red_low = ind.find_previous_red_low()
        if prev_red_low is None:
            return signal

        entry_price    = candle.close
        risk_distance  = entry_price - prev_red_low   # how far TP is below entry

        # ── Load config live ──────────────────────────────────
        # NOTE: on-disk JSON keys are unchanged (min_sl_points / max_sl_points /
        # risk_max_sl_points). Local names use the clearer terminology:
        #   risk_min_sl  ← min_sl_points        (floor; reject if below)
        #   risk_max_sl  ← risk_max_sl_points   (ceiling; reject if above; 0=off)
        #   max_sl_cap   ← max_sl_points        (clamp sl_price; 0/None=off)
        risk_min_sl = self.RISK_MIN_SL
        risk_max_sl = self.RISK_MAX_SL
        rr          = self.MIN_RR
        max_sl_cap  = None

        try:
            from app.config.strategy_loader import load_strategy_config
            cfg         = load_strategy_config(self.strategy_id)
            risk_min_sl = cfg.get("min_sl_points",      risk_min_sl)
            risk_max_sl = cfg.get("risk_max_sl_points", risk_max_sl)
            rr          = cfg.get("risk_reward_ratio",  rr)
            max_sl_cap  = cfg.get("max_sl_points")
        except Exception:
            pass

        # ── RISK_MIN_SL: minimum risk-distance guard ──────────
        if risk_distance < risk_min_sl:
            write_audit_log(
                f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                f"SKIP_SIGNAL → risk_distance {risk_distance:.2f} < RISK_MIN_SL {risk_min_sl}"
            )
            return signal

        # ── RISK_MAX_SL BEGIN — maximum risk-distance guard ───
        # Independent of MAX_SL_CAP: this REJECTS the trade outright when the
        # raw risk distance is too wide. 0 = disabled. Checked on the raw
        # risk_distance, BEFORE any sl_price cap is applied.
        if isinstance(risk_max_sl, (int, float)) and risk_max_sl > 0 \
                and risk_distance > risk_max_sl:
            write_audit_log(
                f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                f"SKIP_SIGNAL → risk_distance {risk_distance:.2f} > RISK_MAX_SL {risk_max_sl}"
            )
            return signal
        # ── RISK_MAX_SL END ───────────────────────────────────

        # ── Compute SL and TP for the SHORT trade ─────────────
        tp_price = prev_red_low
        sl_price = entry_price + (risk_distance * rr)

        # ── MAX_SL_CAP: clamp the SL distance to max_sl_points ─
        if isinstance(max_sl_cap, (int, float)) and max_sl_cap > 0:
            max_sl_price = entry_price + max_sl_cap
            if sl_price > max_sl_price:
                write_audit_log(
                    f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                    f"MAX_SL_CAP_APPLIED → sl {sl_price:.2f} capped to {max_sl_price:.2f}"
                )
                sl_price = max_sl_price

        # ── EMIT SIGNAL ONLY — DO NOT COMMIT in_trade ─────────
        # Trade state is owned by the recorded trade. If the router records
        # this signal, the next candle's _refresh_in_trade() flips in_trade
        # True. If dropped, nothing is recorded and the symbol stays free.
        signal.is_sell      = True
        signal.entry_price  = entry_price
        signal.sl           = sl_price
        signal.tp           = tp_price

        write_audit_log(
            f"[SCALP-V1][{self.slot_name}][{self.symbol}] SELL_SIGNAL\n"
            f"  entry={entry_price}\n"
            f"  tp={tp_price:.2f}  (prev red low, {risk_distance:.2f} pts below)\n"
            f"  sl={sl_price:.2f}  (entry + {risk_distance:.2f} × rr={rr})"
        )

        return signal

    # =========================
    # In-trade truth (recorded-trade backed)
    # =========================

    def _refresh_in_trade(self):
        """
        Reconcile self.in_trade / self.sl / self.tp with the ACTUAL recorded
        trade for this strategy+symbol.

        Order: LIVE registry first, then PAPER DB. Either lookup failing is
        treated as 'no open trade' (fail-open for entries; duplicates are still
        blocked downstream by the router/slot gate).
        """
        booked_sl = None
        booked_tp = None
        found     = False

        # ── 1. LIVE: TradeStateManager registry ───────────────
        try:
            from app.trading.trade_state_manager import TradeStateManager
            slots = TradeStateManager._REGISTRY.get(self.strategy_id, {})
            for mgr in slots.values():
                t = getattr(mgr, "active_trade", None)
                if (
                    t is not None
                    and t.symbol == self.symbol
                    and getattr(t, "state", None) in _LIVE_OPEN_STATES
                ):
                    booked_sl = t.sl_price
                    booked_tp = t.tp_price
                    found     = True
                    break
        except Exception as e:
            write_audit_log(
                f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                f"IN_TRADE_LIVE_REFRESH_ERR={e!r}"
            )

        # ── 2. PAPER: paper_trades DB (only if no live trade) ──
        if not found:
            try:
                from app.db.paper_trades_repo import get_open_paper_trades_for_symbol
                rows = get_open_paper_trades_for_symbol(
                    strategy_name=self.strategy_id,
                    symbol=self.symbol,
                )
                if rows:
                    row = rows[0]
                    try:
                        booked_sl = row["sl_price"]
                        booked_tp = row["tp_price"]
                    except (KeyError, IndexError, TypeError):
                        try:
                            booked_sl = row[1]
                            booked_tp = row[2]
                        except Exception:
                            pass
                    found = True
            except Exception as e:
                write_audit_log(
                    f"[SCALP-V1][{self.slot_name}][{self.symbol}] "
                    f"IN_TRADE_PAPER_REFRESH_ERR={e!r} — treating as NOT in trade"
                )

        # ── Apply reconciled truth ────────────────────────────
        if found:
            if booked_sl is not None:
                self.sl = booked_sl
            if booked_tp is not None:
                self.tp = booked_tp
            self.in_trade = True
        else:
            if self.in_trade:
                self.in_trade    = False
                self.entry_price = None
                self.sl          = None
                self.tp          = None

    # =========================
    # Helpers
    # =========================

    def _is_current_week_expiry(self) -> bool:
        try:
            today           = date.today()
            days_to_thu     = (3 - today.weekday()) % 7
            current_expiry  = today + timedelta(days=days_to_thu)
            if today.weekday() > 3:
                current_expiry += timedelta(days=7)
            return str(current_expiry.year % 100) in self.symbol
        except Exception:
            return False

    def _reset(self):
        self.in_trade    = False
        self.entry_price = None
        self.sl          = None
        self.tp          = None