# backend/app/indicators/pivot_cache.py

from datetime import date, timedelta
from typing import Dict, Tuple, Optional

from kiteconnect import KiteConnect

from app.engine.bb_options.futures_resolver import resolve_current_month_banknifty_fut
from app.event_bus.audit_logger import write_audit_log


class PivotCache:

    _cache: Dict[Tuple[str, date], Dict[str, float]] = {}
    _kite: Optional[KiteConnect] = None

    # ==================================================
    # INITIALIZATION (Call Once at Startup)
    # ==================================================

    @classmethod
    def initialize(cls, kite: KiteConnect):
        cls._kite = kite
        write_audit_log("[PIVOT] Kite session initialized")

    # ==================================================
    # INTERNAL
    # ==================================================

    @classmethod
    def _get_previous_trading_day(cls, kite, token: int) -> Optional[date]:
        """
        Walk backwards from yesterday up to 10 days,
        trying each date against the broker's historical API.
        Returns the most recent date that actually has OHLC data.
        This handles weekends AND market holidays automatically —
        no hardcoded holiday list needed.
        """
        candidate = date.today() - timedelta(days=1)

        for _ in range(10):
            # Skip obvious weekend days first (saves API calls)
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)

            try:
                data = kite.historical_data(
                    instrument_token=token,
                    from_date=candidate,
                    to_date=candidate,
                    interval="day",
                )
                if data:
                    write_audit_log(
                        f"[PIVOT] Previous trading day resolved: {candidate}"
                    )
                    return candidate
            except Exception as e:
                write_audit_log(
                    f"[PIVOT] Probe failed for {candidate}: {e}"
                )

            candidate -= timedelta(days=1)

        write_audit_log("[PIVOT] Could not find a previous trading day in 10 days")
        return None

    @classmethod
    def _ensure_kite(cls) -> bool:
        if cls._kite:
            return True

        try:
            from app.api_server import zerodha_manager

            kite = (
                zerodha_manager.get_data_kite()
                or zerodha_manager.get_trade_kite()
            )

            if kite:
                cls._kite = kite
                write_audit_log("[PIVOT] Kite injected dynamically")
                return True

        except Exception as e:
            write_audit_log(f"[PIVOT] Dynamic injection failed ERR={e}")

        write_audit_log("[PIVOT] Kite not available")
        return False

    # ==================================================
    # PUBLIC
    # ==================================================

    @classmethod
    def get_pivots(cls, symbol: str) -> Optional[Dict[str, float]]:

        if not cls._ensure_kite():
            return None

        today = date.today()
        key = (symbol, today)

        if key in cls._cache:
            return cls._cache[key]

        resolved = resolve_current_month_banknifty_fut()
        if not resolved:
            write_audit_log("[PIVOT] FUT resolver failed")
            return None

        token, fut_symbol = resolved

        # --------------------------------------------------
        # FIX: Walk backwards to find the actual last
        # trading day — handles holidays + weekends.
        # The old code fetched a hardcoded "yesterday" which
        # returned empty on days after a holiday.
        # --------------------------------------------------
        prev_day = cls._get_previous_trading_day(cls._kite, token)
        if not prev_day:
            # Cache a sentinel so we don't keep retrying every candle
            cls._cache[key] = None
            return None

        try:
            data = cls._kite.historical_data(
                instrument_token=token,
                from_date=prev_day,
                to_date=prev_day,
                interval="day",
            )
        except Exception as e:
            write_audit_log(f"[PIVOT] Historical fetch failed ERR={e}")
            return None

        if not data:
            write_audit_log(
                f"[PIVOT] No historical data for {prev_day} "
                f"(this should not happen after _get_previous_trading_day succeeded)"
            )
            # Cache None so warmup loop doesn't hammer the API
            cls._cache[key] = None
            return None

        candle = data[0]

        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        pp = (h + l + c) / 3
        r1 = 2 * pp - l
        s1 = 2 * pp - h

        pivots = {
            "pp": pp,
            "r1": r1,
            "s1": s1,
        }

        cls._cache[key] = pivots

        write_audit_log(
            f"[PIVOT-FROZEN] {fut_symbol} prev_day={prev_day} "
            f"H={h} L={l} C={c} PP={pp:.2f} R1={r1:.2f} S1={s1:.2f}"
        )

        return pivots