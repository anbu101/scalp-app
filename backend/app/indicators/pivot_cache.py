# backend/app/indicators/pivot_cache.py

from datetime import date, timedelta
from typing import Dict, Tuple, Optional

from kiteconnect import KiteConnect

from app.engine.bb_options.futures_resolver import resolve_current_month_banknifty_fut
from app.event_bus.audit_logger import write_audit_log


class PivotCache:

    _cache: Dict[Tuple[str, date], Dict[str, float]] = {}
    _kite: Optional[KiteConnect] = None

    @classmethod
    def initialize(cls, kite: KiteConnect):
        cls._kite = kite
        write_audit_log("[PIVOT] Kite session initialized")

    @classmethod
    def _get_previous_trading_day(cls, kite, token: int) -> Optional[date]:
        candidate = date.today() - timedelta(days=1)

        for _ in range(10):
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
                write_audit_log(f"[PIVOT] Probe failed for {candidate}: {e}")

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

    @classmethod
    def get_pivots(cls, symbol: str) -> Optional[Dict[str, float]]:

        if not cls._ensure_kite():
            return None

        today = date.today()
        key   = (symbol, today)

        if key in cls._cache:
            return cls._cache[key]

        resolved = resolve_current_month_banknifty_fut()
        if not resolved:
            write_audit_log("[PIVOT] FUT resolver failed")
            return None

        token, fut_symbol = resolved

        prev_day = cls._get_previous_trading_day(cls._kite, token)
        if not prev_day:
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
            write_audit_log(f"[PIVOT] No historical data for {prev_day}")
            cls._cache[key] = None
            return None

        candle = data[0]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        # Standard pivot formulas
        pp = (h + l + c) / 3

        r1 = 2 * pp - l
        r2 = pp + (h - l)               # NEW

        s1 = 2 * pp - h
        s2 = pp - (h - l)               # NEW
        s3 = s1 - (h - l)               # NEW  (= 2*pp - 2h + l)

        pivots = {
            "pp": pp,
            "r1": r1,
            "r2": r2,                   # NEW
            "s1": s1,
            "s2": s2,                   # NEW
            "s3": s3,                   # NEW
        }

        cls._cache[key] = pivots

        write_audit_log(
            f"[PIVOT-FROZEN] {fut_symbol} prev_day={prev_day} "
            f"H={h} L={l} C={c} "
            f"PP={pp:.2f} "
            f"R2={r2:.2f} R1={r1:.2f} "
            f"S1={s1:.2f} S2={s2:.2f} S3={s3:.2f}"
        )

        return pivots