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
        """
        Inject live Zerodha DATA or TRADE kite session.
        Should be called during startup OR after successful login.
        """
        cls._kite = kite
        write_audit_log("[PIVOT] Kite session initialized")

    # ==================================================
    # INTERNAL
    # ==================================================

    @classmethod
    def _get_previous_trading_day(cls) -> date:
        d = date.today() - timedelta(days=1)

        # Skip weekends
        while d.weekday() >= 5:
            d -= timedelta(days=1)

        return d

    @classmethod
    def _ensure_kite(cls) -> bool:
        """
        Ensure kite session is available.
        Tries dynamic injection from existing ZerodhaManager.
        """
        if cls._kite:
            return True

        try:
            # Import here to avoid circular dependency
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

        prev_day = cls._get_previous_trading_day()

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
            write_audit_log("[PIVOT] No historical data returned")
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
            f"[PIVOT-FROZEN] {fut_symbol} "
            f"H={h} L={l} C={c} R1={r1} S1={s1}"
        )

        return pivots
