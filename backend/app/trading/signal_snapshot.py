from datetime import datetime
from typing import Dict, Optional

# In-memory, resets daily (intentional)
_signal_snapshot: Dict[str, dict] = {}


def update_signal(
    slot: str,
    symbol: str,
    action: str,
    reason: str = "",
    price: Optional[float] = None,
):
    _signal_snapshot[slot] = {
        "slot": slot,
        "symbol": symbol,
        "action": action,          # BUY / SKIPPED
        "reason": reason,
        "price": price,
        "time": datetime.now().strftime("%H:%M:%S"),
    }


def get_signal_snapshot():
    return _signal_snapshot
