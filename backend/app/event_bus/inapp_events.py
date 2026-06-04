"""
IN-APP EVENT BUS
Path: app/event_bus/inapp_events.py

A tiny, thread-safe, in-memory ring buffer of recent trade events for the
in-app audio/toast notification system. No DB, no external dependencies.

These events are recorded at the SAME call sites that fire Telegram
notifications (the four notify_* functions in app/api/telegram_api.py), so
every strategy (BB_V1, BB_V2, SCALP_V1, SCALP_V2, HA_V1) and both modes
(LIVE + PAPER) are covered automatically.

ALERTS:
    In addition to the four trade events (ENTER/TP/SL/EXIT), the bus carries
    operational ALERT events for the "needs attention" cases that a user must
    not have to find in the logs: rejected/dead entries, partial fills needing
    manual action, GTT placement failures, and fill timeouts. These ride the
    SAME feed and cursor; the frontend renders them with a severity
    (info / warning / error), a short title, and a message, and keeps them in a
    persistent in-session notification center (a bell with a badge).

    Alert events carry three extra fields:
        severity : "info" | "warning" | "error"   (default "warning")
        code     : a short machine tag, e.g. "DEAD_ENTRY", "PARTIAL_FILL"
        message  : a human-readable, plain-English description

    The four trade events leave these as None, so existing behaviour is
    unchanged.

EDGE-TRIGGERED ALERTS (record_alert_once / clear_alert_once):
    Some conditions are evaluated on a loop (broker disconnect checked every
    few seconds, relay monitor polling, max-loss gate re-checked on every
    signal). Firing record_alert on every evaluation would FLOOD the bell with
    the same message. record_alert_once() fires only on the TRANSITION into the
    alerted state for a given key, and suppresses repeats until clear_alert_once()
    resets that key (typically on recovery, or daily). This makes the alerts
    "edge-triggered" rather than "level-triggered".

    Example (relay monitor):
        # all relays down (called repeatedly while down):
        record_alert_once("relay_down", "RELAY_DOWN",
                          "All order relays are DOWN — orders cannot reach the broker.",
                          severity="error")
        # relay recovers:
        clear_alert_once("relay_down")                 # arms the next down-alert
        record_alert("RELAY_UP", "Order relay back online.", severity="info")

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
from typing import List, Dict, Optional, Set

# Keep the buffer small — the frontend polls every few seconds and only needs
# recent events. 50 is plenty to never miss one between polls.
_MAX_EVENTS = 50

_lock = threading.Lock()
_events: List[Dict] = []
_next_id = 1

# Active edge-triggered alert keys (a key here means "already alerted, suppress
# repeats until cleared"). Guarded by its own lock.
_once_lock = threading.Lock()
_active_once: Set[str] = set()


# Valid event types — kept loose on purpose; the frontend maps these to
# sounds/toasts. Unknown types are still delivered (frontend can default).
EVENT_ENTER = "ENTER"
EVENT_TP    = "TP"
EVENT_SL    = "SL"
EVENT_EXIT  = "EXIT"
EVENT_ALERT = "ALERT"   # operational "needs attention" events


# Alert severities (string constants for convenience at call sites).
SEV_INFO    = "info"
SEV_WARNING = "warning"
SEV_ERROR   = "error"


def record_event(
    event_type: str,
    strategy_id: str = "",
    symbol: str = "",
    side: Optional[str] = None,
    mode: str = "live",
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    pnl: Optional[float] = None,
    severity: Optional[str] = None,
    code: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    """
    Append an event to the ring buffer. Never raises — notifications must never
    break the trading path, so any error is swallowed.

    The first eight parameters are unchanged (trade events). The last three
    (severity / code / message) are used by ALERT events; trade events leave
    them None.
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
                # Alert-only fields (None for trade events).
                "severity":    (severity or None),
                "code":        (code or None),
                "message":     (message or None),
            }
            _next_id += 1
            _events.append(evt)
            # Trim to the last _MAX_EVENTS
            if len(_events) > _MAX_EVENTS:
                del _events[: len(_events) - _MAX_EVENTS]
    except Exception:
        # Best-effort only.
        pass


def record_alert(
    code: str,
    message: str,
    severity: str = SEV_WARNING,
    strategy_id: str = "",
    symbol: str = "",
    mode: str = "live",
) -> None:
    """
    Convenience wrapper for operational alerts. Keeps trade-manager call sites
    to a single readable line, e.g.:

        record_alert("DEAD_ENTRY",
                     f"{symbol} sell rejected — no position opened.",
                     severity="error", strategy_id="SCALP_V1",
                     symbol=symbol, mode="live")

    Never raises. Fires EVERY time it is called — use record_alert_once() for
    conditions evaluated on a loop.
    """
    record_event(
        EVENT_ALERT,
        strategy_id=strategy_id,
        symbol=symbol,
        mode=mode,
        severity=severity,
        code=code,
        message=message,
    )


def record_alert_once(
    key: str,
    code: str,
    message: str,
    severity: str = SEV_WARNING,
    strategy_id: str = "",
    symbol: str = "",
    mode: str = "live",
) -> bool:
    """
    Edge-triggered alert. Fires record_alert() the FIRST time it is called for
    `key`, then suppresses repeats until clear_alert_once(key) is called.

    Returns True if an alert was actually fired (the transition), False if it
    was suppressed (already in the alerted state).

    Use for loop-evaluated conditions so the bell isn't flooded:
        broker disconnect (polled), relay down (monitored), max-loss (re-checked
        on every signal).

    Never raises.
    """
    try:
        with _once_lock:
            if key in _active_once:
                return False           # already alerted; suppress
            _active_once.add(key)
        # Fire outside the lock.
        record_alert(code, message, severity=severity,
                     strategy_id=strategy_id, symbol=symbol, mode=mode)
        return True
    except Exception:
        return False


def clear_alert_once(key: str) -> None:
    """
    Reset an edge-triggered key so the NEXT occurrence of that condition can
    alert again. Call this on recovery (e.g. broker reconnected, relay up) or
    on a daily reset. Never raises. Safe to call even if the key isn't set.
    """
    try:
        with _once_lock:
            _active_once.discard(key)
    except Exception:
        pass


def is_alert_active(key: str) -> bool:
    """True if `key` is currently in the alerted (suppressed) state."""
    try:
        with _once_lock:
            return key in _active_once
    except Exception:
        return False


def reset_alert_keys(prefix: Optional[str] = None) -> None:
    """
    Clear edge-triggered keys. With no prefix, clears ALL keys (use at startup).
    With a prefix, clears only matching keys, e.g. reset_alert_keys("maxloss:")
    at EOD so each strategy's max-loss/profit alert can fire again next session.
    Never raises.
    """
    try:
        with _once_lock:
            if prefix is None:
                _active_once.clear()
            else:
                for k in [k for k in _active_once if k.startswith(prefix)]:
                    _active_once.discard(k)
    except Exception:
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