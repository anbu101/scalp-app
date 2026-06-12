# backend/app/api/signal_routes.py
"""
PHASE 2b (part 2) REPLACEMENT — masks /signals/last for non-admin licenses.

Snapshot shape (from app/trading/signal_snapshot.py):
    {slot: {slot, symbol, action, reason, price, time}}

The ONLY narrating field is `reason` — it explains WHY a signal fired or
was skipped, which is strategy logic. Everything else (symbol, BUY/SKIPPED,
price, time) is an operational fact about the user's own running
strategies and stays visible.

ADMIN ui_level -> raw snapshot, byte-identical to pre-license behavior
(BB_V1 isolation: this route reads state, never writes; no engine touched).
Non-admin     -> reason stripped from every slot.
Blocked       -> {} (fail closed, matches futures_candles pattern).
"""

from fastapi import APIRouter

from app.trading.signal_snapshot import get_signal_snapshot
from app.license import license_state

router = APIRouter(tags=["signals"])

_SAFE_FIELDS = ("slot", "symbol", "action", "price", "time")


@router.get("/signals/last")
def last_signals():
    snap = get_signal_snapshot()

    if license_state.ui_level() == "admin":
        return snap

    if not license_state.is_usable():
        return {}

    return {
        slot: {k: v for k, v in d.items() if k in _SAFE_FIELDS}
        for slot, d in snap.items()
    }