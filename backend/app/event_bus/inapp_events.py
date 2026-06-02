"""
IN-APP EVENT BUS
Path: app/event_bus/inapp_events.py

A tiny, thread-safe, in-memory ring buffer of recent trade events for the
in-app audio/toast notification system. No DB, no external dependencies.

These events are recorded at the SAME call sites that fire Telegram
notifications (the four notify_* functions in app/api/telegram_api.py), so
every strategy (BB_V1, BB_V2, SCALP_V1, SCALP_V2, HA_V1) and both modes
(LIVE + PAPER) are covered automatically.

IMPORTANT: record_event() is called BEFORE the Telegram filter check, so the
in-app feed is fully independent of the Telegram strategy/mode/level toggles.
In-app muting is controlled separately by App Settings (notify_audio /
notify_toast), enforced on the frontend.

The frontend polls GET /api/app/events?after=<id> and fires audio/toast for
each new event.

CURSOR SEMANTICS (the bit that bit us):
    after_id < 0   → GENUINE first poll. Return no backlog, just the current
                     latest id so the client can seed its cursor.
    after_id >= 0  → Return all events with id > after_id.

    The old code treated `after_id <= 0` as "first poll". But the buffer
    starts empty at launch, so the first poll returns latest_id=0, the client
    stored 0, and EVERY subsequent poll re-entered the "first poll" branch and
    silently dropped events — the cursor could never escape 0. Using -1 as the
    first-poll sentinel makes a real cursor of 0 behave like any other cursor.
"""

import threading
import time
from typing import List, Dict, Optional

# Keep the buffer small — the frontend polls every few seconds and only needs
# recent events. 50 is plenty to never miss one between polls.
_MAX_EVENTS = 50

_lock = threading.Lock()
_events: List[Dict] = []
_next_id = 1


# Valid event types — kept loose on purpose; the frontend maps these to
# sounds/toasts. Unknown types are still delivered (frontend can default).
EVENT_ENTER = "ENTER"
EVENT_TP    = "TP"
EVENT_SL    = "SL"
EVENT_EXIT  = "EXIT"


def record_event(
    event_type: str,
    strategy_id: str = "",
    symbol: str = "",
    side: Optional[str] = None,
    mode: str = "live",
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    pnl: Optional[float] = None,
) -> None:
    """
    Append a trade event to the ring buffer. Never raises — notifications must
    never break the trading path, so any error is swallowed.
    """
    global _next_id
    try:
        with _lock:
            evt = {
                "id":          _next_id,
                "ts":          time.time(),
                "event_type":  (event_type or "").upper(),
                "strategy_id": strategy_id or "",
                "symbol":      symbol or "",
                "side":        side,
                "mode":        (mode or "live").lower(),
                "entry_price": entry_price,
                "exit_price":  exit_price,
                "pnl":         pnl,
            }
            _next_id += 1
            _events.append(evt)
            # Trim to the last _MAX_EVENTS
            if len(_events) > _MAX_EVENTS:
                del _events[: len(_events) - _MAX_EVENTS]
    except Exception:
        # Best-effort only.
        pass


def get_events_after(after_id: int = -1) -> Dict:
    """
    Return all events with id > after_id, plus the latest id so the client can
    advance its cursor.

    after_id < 0  → genuine first poll: no backlog, just hand back the current
                    cursor so the client seeds lastId and only acts on events
                    newer than this from here on.
    after_id >= 0 → normal cursor: return events strictly newer than after_id.
    """
    with _lock:
        latest = _events[-1]["id"] if _events else 0

        if after_id < 0:
            # First poll — suppress backlog, seed the client's cursor.
            return {"events": [], "latest_id": latest}

        newer = [e for e in _events if e["id"] > after_id]
        # If the buffer is empty, keep the client's cursor where it is rather
        # than yanking it back to 0.
        latest_out = latest if _events else after_id
        return {"events": newer, "latest_id": latest_out}