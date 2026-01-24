from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.db.sqlite import get_conn

try:
    from app.marketdata.option_tick_state import OptionTickState
except Exception:
    OptionTickState = None


def get_ltp_for_token(token: int) -> Optional[float]:
    """
    Unified LTP provider for OPTIONS.

    Resolution order:
    1. WebSocket tick (if available)
    2. None (DB candle fallback intentionally disabled)

    NOTE:
    - This system does NOT store option candles
    - Missing candle table is EXPECTED
    - No ERROR logs for expected conditions
    """

    # ---------- 1️⃣ Try WS tick ----------
    try:
        if OptionTickState is not None:
            tick = OptionTickState.get(token)
            if tick and tick.last_price:
                return float(tick.last_price)
    except Exception as e:
        write_audit_log(
            f"[LTP_PROVIDER][WARN] WS tick fetch failed token={token} err={e}"
        )

    # ---------- 2️⃣ DB candle fallback DISABLED ----------
    # Your system does not store option candles.
    # Do NOT query non-existent tables.
    return None
