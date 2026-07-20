"""
TELEGRAM NOTIFICATION API (FastAPI) — MULTI-CHANNEL
app/api/telegram_api.py

WHAT CHANGED (vs single-config version)
---------------------------------------
The old config was a single flat object: one bot_token, one chat_id, one
strategy_filter, one mode_filter, one set of notification_levels. It now
supports a SHARED bot_token and a LIST of channels, each with its OWN
chat_id, strategy filter (MULTI-SELECT), mode filter, notification toggles,
and a schedule window. Every notification fans out to every channel that
"wants" it.

NEW CONFIG SHAPE
----------------
{
  "bot_token": "<shared>",
  "channels": [
    {
      "id": "channel_1", "name": "Primary", "chat_id": "...",
      "enabled": true,
      "strategy_filter": ["SCALP_V3"],        # [] or ["all"] = all strategies
      "mode_filter": "all",                    # all | live | paper
      "notifications": {                       # the FOUR collapsed types
        "tradeActivity":   true,   # entries + TP + SL + manual exits
        "positionUpdates": false,
        "dailySummary":    true,
        "criticalAlerts":  true    # system alerts + order rejections
      },
      "schedule": { "enabled": false, "start": "09:15", "end": "15:45" }
    },
    { ... channel_2 ... }
  ]
}

NOTIFICATION-TYPE COLLAPSE (7 -> 4)
-----------------------------------
  tradeActivity    <= tradeEntries, tpExits, slExits, manualExits
  positionUpdates  <= positionUpdates
  dailySummary     <= dailySummary
  criticalAlerts   <= systemAlerts, criticalAlerts, ORDER REJECTIONS

SEND RULE (per channel, single source of truth = _iter_active_channels)
-----------------------------------------------------------------------
A channel receives an alert iff ALL hold:
  - channel.enabled
  - schedule passes:  start <= now < end   (STRICT < end; or schedule disabled)
  - the alert's notification type is toggled on for that channel
  - mode matches:     all | live | paper
  - strategy matches:
       * filter is ["all"] / []                 -> always
       * else if alert HAS a strategy_id        -> membership test
       * else (system-wide critical, no sid)    -> bypass strategy check

SCHEDULE
--------
Strict cutoff: start <= now < end. NOTHING bypasses the window (per product
decision) — the daily summary fires at 15:30, so a channel that wants it must
set its window end AFTER 15:30 (e.g. 15:45). The UI hints this.

HEARTBEAT
---------
Removed. (notify_system_alert remains — used by the relay monitor etc. — but
the scheduler no longer emits a periodic heartbeat.)

BACK-COMPAT
-----------
load_telegram_config_from_file() transparently migrates an OLD flat config into
channels[0] ("Primary"), leaving channels[1] empty/disabled. Fail-safe: a bad
file yields an empty (no-channel) config, never a crash.
"""

import requests
from datetime import datetime, date
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, List, Optional

# In-app event bus — recorded BEFORE any channel filtering so the in-app
# audio/toast feed is fully independent of the Telegram channel toggles.
# record_event() never raises (best-effort).
from app.event_bus.inapp_events import (
    record_event,
    EVENT_ENTER,
    EVENT_TP,
    EVENT_SL,
    EVENT_EXIT,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# ═══════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════

# The four collapsed notification keys.
NOTIF_TRADE_ACTIVITY   = "tradeActivity"
NOTIF_POSITION_UPDATES = "positionUpdates"
NOTIF_DAILY_SUMMARY    = "dailySummary"
NOTIF_CRITICAL_ALERTS  = "criticalAlerts"

_ALL_STRATEGY_IDS = {
    "SCALP_V1", "SCALP_V2", "SCALP_V3", "SCALP_V5", "BB_V1", "BB_V2", "HA_V1",
    # ── TMA_V1 BEGIN ── (also backfills PST/IC, absent since their launches:
    # notifications flowed — _strategy_matches is literal membership against
    # the saved filter — but they couldn't be toggled per-strategy)
    "PST_SELL", "PST_HEDGE", "IC_V1", "TMA_V1",
    # ── TMA_V1 END ──
}

# Legacy single-select family values that may exist in an OLD config; mapped so
# a migrated channel still matches the right strategies.
_LEGACY_FAMILIES = {
    "BB":    {"BB_V1", "BB_V2"},
    "SCALP": {"SCALP_V1", "SCALP_V2", "SCALP_V3"},
}

_DEFAULT_NOTIFICATIONS = {
    NOTIF_TRADE_ACTIVITY:   True,
    NOTIF_POSITION_UPDATES: False,
    NOTIF_DAILY_SUMMARY:    True,
    NOTIF_CRITICAL_ALERTS:  True,
}

_DEFAULT_SCHEDULE = {"enabled": False, "start": "09:15", "end": "15:45"}


# ═══════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════

class ChannelNotifications(BaseModel):
    tradeActivity:   bool = True
    positionUpdates: bool = False
    dailySummary:    bool = True
    criticalAlerts:  bool = True


class ChannelSchedule(BaseModel):
    enabled: bool = False
    start:   str  = "09:15"   # "HH:MM" 24h
    end:     str  = "15:45"   # "HH:MM" 24h, STRICT < end


class TelegramChannel(BaseModel):
    id:              str = "channel_1"
    name:            str = "Primary"
    chat_id:         str = ""
    enabled:         bool = False
    strategy_filter: List[str] = []          # [] or ["all"] => all
    mode_filter:     str = "all"             # all | live | paper
    notifications:   ChannelNotifications = ChannelNotifications()
    schedule:        ChannelSchedule = ChannelSchedule()


class TelegramConfigModel(BaseModel):
    bot_token: str = ""
    channels:  List[TelegramChannel] = []


class TelegramTestRequest(BaseModel):
    bot_token: str
    chat_id: str


# ═══════════════════════════════════════════════════════════
#  STORAGE  (+ transparent migration)
# ═══════════════════════════════════════════════════════════

import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".scalp-app" / "telegram_config.json"


def _empty_channel(idx: int) -> dict:
    return {
        "id":              f"channel_{idx}",
        "name":            "Primary" if idx == 1 else "Secondary",
        "chat_id":         "",
        "enabled":         False,
        "strategy_filter": [],
        "mode_filter":     "all",
        "notifications":   dict(_DEFAULT_NOTIFICATIONS),
        "schedule":        dict(_DEFAULT_SCHEDULE),
    }


def _migrate_old_notification_levels(levels: dict) -> dict:
    """
    Collapse the OLD 7-key notification_levels into the new 4 keys.
      tradeActivity   = any of tradeEntries/tpExits/slExits/manualExits ON
      positionUpdates = positionUpdates
      dailySummary    = dailySummary
      criticalAlerts  = systemAlerts OR criticalAlerts
    Missing keys default to the old defaults (mostly ON).
    """
    levels = levels or {}
    trade_activity = any([
        levels.get("tradeEntries", True),
        levels.get("tpExits", True),
        levels.get("slExits", True),
        levels.get("manualExits", True),
    ])
    return {
        NOTIF_TRADE_ACTIVITY:   bool(trade_activity),
        NOTIF_POSITION_UPDATES: bool(levels.get("positionUpdates", False)),
        NOTIF_DAILY_SUMMARY:    bool(levels.get("dailySummary", True)),
        NOTIF_CRITICAL_ALERTS:  bool(levels.get("systemAlerts", True)
                                     or levels.get("criticalAlerts", True)),
    }


def _migrate_old_strategy_filter(val) -> list:
    """
    OLD strategy_filter was a single string: "all" | strategy id | legacy
    family ("bb"/"scalp"). Convert to the new LIST form.
      "all"            -> []           (means all)
      "BB_V1"          -> ["BB_V1"]
      "bb"/"scalp"     -> expanded family list
    """
    if val is None:
        return []
    if isinstance(val, list):
        return val
    s = str(val).strip()
    if not s or s.lower() == "all":
        return []
    up = s.upper()
    if up in _LEGACY_FAMILIES:
        return sorted(_LEGACY_FAMILIES[up])
    return [up]


def _looks_like_old_flat_config(raw: dict) -> bool:
    """An old config has top-level chat_id / notification_levels and NO
    'channels' key."""
    if not isinstance(raw, dict):
        return False
    if "channels" in raw:
        return False
    return ("chat_id" in raw) or ("notification_levels" in raw) or ("strategy_filter" in raw)


def _migrate_flat_to_channels(raw: dict) -> dict:
    """Wrap an old flat config into the new {bot_token, channels[2]} shape."""
    ch1 = _empty_channel(1)
    ch1["chat_id"]         = raw.get("chat_id", "") or ""
    ch1["enabled"]         = bool(ch1["chat_id"])  # enable if it had a chat id
    ch1["strategy_filter"] = _migrate_old_strategy_filter(raw.get("strategy_filter"))
    ch1["mode_filter"]     = raw.get("mode_filter", "all") or "all"
    ch1["notifications"]   = _migrate_old_notification_levels(raw.get("notification_levels"))
    # schedule did not exist before -> default disabled (no behavioural change)
    ch1["schedule"]        = dict(_DEFAULT_SCHEDULE)

    migrated = {
        "bot_token": raw.get("bot_token", "") or "",
        "channels":  [ch1, _empty_channel(2)],
    }
    return migrated


def _normalize_config(raw: dict) -> dict:
    """
    Ensure the in-memory config is always the new shape with exactly 2 channels,
    each carrying all expected keys. Tolerant of partial/hand-edited files.
    """
    if not isinstance(raw, dict):
        return {"bot_token": "", "channels": [_empty_channel(1), _empty_channel(2)]}

    if _looks_like_old_flat_config(raw):
        raw = _migrate_flat_to_channels(raw)

    bot_token = raw.get("bot_token", "") or ""
    channels  = raw.get("channels") or []

    norm_channels = []
    for idx in (1, 2):
        src = channels[idx - 1] if len(channels) >= idx and isinstance(channels[idx - 1], dict) else {}
        base = _empty_channel(idx)
        base["id"]      = src.get("id", base["id"]) or base["id"]
        base["name"]    = src.get("name", base["name"]) or base["name"]
        base["chat_id"] = src.get("chat_id", "") or ""
        base["enabled"] = bool(src.get("enabled", False))
        base["strategy_filter"] = _migrate_old_strategy_filter(src.get("strategy_filter", []))
        base["mode_filter"]     = src.get("mode_filter", "all") or "all"

        notif = src.get("notifications") or {}
        base["notifications"] = {
            NOTIF_TRADE_ACTIVITY:   bool(notif.get(NOTIF_TRADE_ACTIVITY,   _DEFAULT_NOTIFICATIONS[NOTIF_TRADE_ACTIVITY])),
            NOTIF_POSITION_UPDATES: bool(notif.get(NOTIF_POSITION_UPDATES, _DEFAULT_NOTIFICATIONS[NOTIF_POSITION_UPDATES])),
            NOTIF_DAILY_SUMMARY:    bool(notif.get(NOTIF_DAILY_SUMMARY,    _DEFAULT_NOTIFICATIONS[NOTIF_DAILY_SUMMARY])),
            NOTIF_CRITICAL_ALERTS:  bool(notif.get(NOTIF_CRITICAL_ALERTS,  _DEFAULT_NOTIFICATIONS[NOTIF_CRITICAL_ALERTS])),
        }

        sched = src.get("schedule") or {}
        base["schedule"] = {
            "enabled": bool(sched.get("enabled", False)),
            "start":   str(sched.get("start", _DEFAULT_SCHEDULE["start"]) or _DEFAULT_SCHEDULE["start"]),
            "end":     str(sched.get("end",   _DEFAULT_SCHEDULE["end"])   or _DEFAULT_SCHEDULE["end"]),
        }
        norm_channels.append(base)

    return {"bot_token": bot_token, "channels": norm_channels}


def load_telegram_config_from_file():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                raw = json.load(f)
            return _normalize_config(raw)
        except Exception as e:
            print(f"[TELEGRAM] Failed to load config: {e}")
            return {"bot_token": "", "channels": [_empty_channel(1), _empty_channel(2)]}
    return {"bot_token": "", "channels": [_empty_channel(1), _empty_channel(2)]}


def save_telegram_config_to_file(config_dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_dict, f, indent=2)
        print(f"[TELEGRAM] Config saved to {CONFIG_FILE}")
    except Exception as e:
        print(f"[TELEGRAM] Failed to save config: {e}")


# In-memory config (always normalized to the new shape).
TELEGRAM_CONFIG = load_telegram_config_from_file()
print(f"[TELEGRAM] Config loaded from {CONFIG_FILE}")


# ═══════════════════════════════════════════════════════════
#  CHANNEL RESOLUTION  (single source of truth for "who gets this")
# ═══════════════════════════════════════════════════════════

def _bot_token() -> str:
    return (TELEGRAM_CONFIG or {}).get("bot_token", "") or ""


def _channels() -> List[dict]:
    return (TELEGRAM_CONFIG or {}).get("channels", []) or []


def _parse_hhmm(s: str) -> Optional[int]:
    """'HH:MM' -> minutes since midnight, or None if unparseable."""
    try:
        hh, mm = str(s).split(":")
        return int(hh) * 60 + int(mm)
    except Exception:
        return None


def _schedule_passes(schedule: dict, now: datetime) -> bool:
    """STRICT window: start <= now < end. Disabled schedule always passes."""
    if not schedule or not schedule.get("enabled"):
        return True
    start = _parse_hhmm(schedule.get("start", ""))
    end   = _parse_hhmm(schedule.get("end", ""))
    if start is None or end is None:
        return True  # fail-open on a malformed window
    cur = now.hour * 60 + now.minute
    return start <= cur < end


def _strategy_matches(channel: dict, strategy_id: Optional[str]) -> bool:
    """
    Strategy filter rule:
      - filter ["all"] / [] / contains "all"  -> always True
      - alert HAS a strategy_id               -> membership test
      - alert has NO strategy_id (system-wide)-> True (bypass)
    """
    flt = channel.get("strategy_filter", []) or []
    # Normalize: any "all" sentinel or empty list means "all strategies".
    if not flt or any(str(x).lower() == "all" for x in flt):
        return True
    if not strategy_id:
        return True  # system-wide alert bypasses the strategy filter
    sid = str(strategy_id).upper()
    allowed = {str(x).upper() for x in flt}
    # Expand any legacy family tokens that survived.
    expanded = set()
    for a in allowed:
        if a in _LEGACY_FAMILIES:
            expanded |= _LEGACY_FAMILIES[a]
        else:
            expanded.add(a)
    return sid in expanded


def _mode_matches(channel: dict, mode: Optional[str]) -> bool:
    mf = (channel.get("mode_filter", "all") or "all").lower()
    if mf == "all":
        return True
    return mf == (mode or "live").lower()


def _iter_active_channels(notif_key: str,
                          *,
                          mode: Optional[str] = None,
                          strategy_id: Optional[str] = None,
                          now: Optional[datetime] = None):
    """
    Yield (bot_token, chat_id, channel) for every channel that should receive
    an alert of type `notif_key`. This is the ONE place the full send rule
    lives; every notify_* fans out through it.
    """
    token = _bot_token()
    if not token:
        return
    if now is None:
        now = datetime.now()

    for ch in _channels():
        if not ch.get("enabled"):
            continue
        chat_id = ch.get("chat_id", "") or ""
        if not chat_id:
            continue
        if not (ch.get("notifications", {}) or {}).get(notif_key, False):
            continue
        if not _schedule_passes(ch.get("schedule", {}) or {}, now):
            continue
        if not _mode_matches(ch, mode):
            continue
        if not _strategy_matches(ch, strategy_id):
            continue
        yield token, chat_id, ch


def _fanout(notif_key: str, message: str, *,
            mode: Optional[str] = None,
            strategy_id: Optional[str] = None):
    """Send `message` to every channel that wants a `notif_key` alert."""
    sent = 0
    for token, chat_id, _ch in _iter_active_channels(
        notif_key, mode=mode, strategy_id=strategy_id
    ):
        if send_telegram_message(token, chat_id, message):
            sent += 1
    return sent


# ═══════════════════════════════════════════════════════════
#  MARKET HOURS HELPER  (unchanged)
# ═══════════════════════════════════════════════════════════

def _is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 555 <= t < 930   # 09:15 → 15:30


# ═══════════════════════════════════════════════════════════
#  DB HELPERS  (unchanged — self-contained summary queries)
# ═══════════════════════════════════════════════════════════

def _today_midnight_ts() -> int:
    today = date.today()
    return int(datetime(today.year, today.month, today.day, 0, 0, 0).timestamp())


def _query_today_live_summary() -> dict:
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()
        rows = conn.execute(
            """
            SELECT strategy_id, entry_price, exit_price, qty
            FROM trades
            WHERE state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()

        by_strategy: dict = {}
        total_pnl = 0.0
        wins = losses = 0
        for row in rows:
            strategy_id, entry_price, exit_price, qty = row
            pnl = (float(exit_price) - float(entry_price)) * int(qty)
            total_pnl += pnl
            if pnl > 0: wins += 1
            else:       losses += 1
            s = by_strategy.setdefault(strategy_id, {"pnl": 0.0, "count": 0})
            s["pnl"] += pnl
            s["count"] += 1

        v3_count = 0
        try:
            from app.db.scalp_v3_repo import get_closed_live_v3_trades_today
            for r in get_closed_live_v3_trades_today():
                pnl = float(r.get("realized_pnl") or 0)
                total_pnl += pnl
                if pnl > 0: wins += 1
                else:       losses += 1
                v3_count += 1
                s = by_strategy.setdefault("SCALP_V3", {"pnl": 0.0, "count": 0})
                s["pnl"] += pnl
                s["count"] += 1
        except Exception as e:
            print(f"[TELEGRAM] V3 live summary union failed: {e}")

        return {
            "total_pnl":   round(total_pnl, 2),
            "trade_count": len(rows) + v3_count,
            "wins":        wins,
            "losses":      losses,
            "by_strategy": by_strategy,
        }
    except Exception as e:
        print(f"[TELEGRAM] Live summary DB error: {e}")
        return {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "by_strategy": {}}


def _query_today_paper_summary() -> dict:
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()
        rows = conn.execute(
            """
            SELECT strategy_name, pnl_value
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()

        by_strategy: dict = {}
        total_pnl = 0.0
        wins = losses = 0
        for row in rows:
            strategy_name, pnl_value = row
            pnl = float(pnl_value) if pnl_value is not None else 0.0
            total_pnl += pnl
            if pnl > 0: wins += 1
            else:       losses += 1
            s = by_strategy.setdefault(strategy_name, {"pnl": 0.0, "count": 0})
            s["pnl"] += pnl
            s["count"] += 1

        return {
            "total_pnl":   round(total_pnl, 2),
            "trade_count": len(rows),
            "wins":        wins,
            "losses":      losses,
            "by_strategy": by_strategy,
        }
    except Exception as e:
        print(f"[TELEGRAM] Paper summary DB error: {e}")
        return {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "by_strategy": {}}


def _query_open_live_positions() -> list:
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()
        rows = conn.execute(
            """
            SELECT symbol, strategy_id, entry_price, qty, state
            FROM trades
            WHERE state != 'CLOSED'
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()
        return [
            {"symbol": r[0], "strategy_id": r[1], "entry_price": float(r[2]),
             "qty": int(r[3]), "state": r[4]}
            for r in rows
        ]
    except Exception as e:
        print(f"[TELEGRAM] Open positions DB error: {e}")
        return []


def _query_open_paper_positions() -> list:
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()
        rows = conn.execute(
            """
            SELECT symbol, strategy_name, entry_price, qty
            FROM paper_trades
            WHERE state = 'OPEN'
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()
        return [
            {"symbol": r[0], "strategy_name": r[1], "entry_price": float(r[2]), "qty": int(r[3])}
            for r in rows
        ]
    except Exception as e:
        print(f"[TELEGRAM] Open paper positions DB error: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@router.get("/config")
async def get_telegram_config():
    """Return the normalized multi-channel config."""
    cfg = _normalize_config(TELEGRAM_CONFIG)
    return cfg


@router.post("/config")
async def save_telegram_config(config: TelegramConfigModel):
    """
    Persist the multi-channel config. Always normalized to exactly 2 channels
    so the UI and backend agree on shape.
    """
    global TELEGRAM_CONFIG
    incoming = config.dict()
    TELEGRAM_CONFIG = _normalize_config(incoming)
    save_telegram_config_to_file(TELEGRAM_CONFIG)
    return {"success": True, "config": TELEGRAM_CONFIG}


@router.post("/test")
async def test_telegram_connection(request: TelegramTestRequest):
    message = "✅ Scalp Terminal Connected!\n\nThis is a test message."
    success = send_telegram_message(request.bot_token, request.chat_id, message)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to send test message.")
    return {"success": True}


# 🔥 DEBUG — fire a REAL critical alert end-to-end so delivery can be confirmed.
@router.post("/debug/send-critical")
async def debug_send_critical():
    notify_critical({
        "severity": "error",
        "message": "🧪 TEST CRITICAL ALERT — if you see this, critical delivery works.",
    })
    return {"success": True, "message": "Critical alert dispatched to all eligible channels."}


# 🔥 DEBUG — simulate an order rejection end-to-end (no real order involved).
@router.post("/debug/send-rejection")
async def debug_send_rejection():
    notify_order_rejection({
        "strategy_id": None,   # system-wide (no order_id to resolve)
        "symbol": "NIFTY24FEB22000CE",
        "status_message": "Insufficient funds (simulated)",
        "order_id": "TEST-0000",
    })
    return {"success": True, "message": "Rejection alert dispatched to all eligible channels."}


# 🔥 DEBUG — fire all trade-activity event types (tone test for in-app feed).
@router.post("/debug/send-all-event-types")
async def send_all_event_types():
    import asyncio
    base = {
        "strategy_id": "SCALP_V1", "mode": "paper",
        "symbol": "NIFTY24FEB22000CE", "side": "CE", "entry_price": 45.50,
    }
    try:
        notify_trade_entry({**base, "quantity": 50, "sl": 40.00, "tp": 55.00})
        await asyncio.sleep(2.5)
        notify_tp_exit({**base, "exit_price": 55.00, "pnl": 475})
        await asyncio.sleep(2.5)
        notify_sl_exit({**base, "exit_price": 40.00, "pnl": -275})
        await asyncio.sleep(2.5)
        notify_manual_exit({**base, "exit_price": 52.00, "pnl": 325, "exit_reason": "SuperTrend"})
        await asyncio.sleep(2.5)
        notify_manual_exit({**base, "exit_price": 41.00, "pnl": -225, "exit_reason": "SuperTrend"})
        return {"success": True, "message": "Fired ENTER, TP, SL, EXIT(+), EXIT(-)."}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
#  CORE SEND FUNCTION  (unchanged)
# ═══════════════════════════════════════════════════════════

def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM] Send failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════
#  NOTIFICATIONS
#
#  Each trade notification records an in-app event FIRST (independent of
#  Telegram channel toggles), then fans out to channels via the type
#  tradeActivity, carrying mode + strategy_id so per-channel mode/strategy
#  filters apply.
# ═══════════════════════════════════════════════════════════


def _p2(v):
    """Price for display — 2dp, tolerant of None/str (kills float dust like
    244.46000000000004 from level arithmetic)."""
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return v


def notify_trade_entry(trade_data: dict):
    record_event(
        EVENT_ENTER,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
    )

    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"
    sl_val = trade_data.get("sl"); tp_val = trade_data.get("tp")
    sl_str = f"₹{_p2(sl_val)}" if sl_val else "—"
    tp_str = f"₹{_p2(tp_val)}" if tp_val else "—"
    note = trade_data.get("note", "")
    note_line = f"\n⚠️ {note}" if note else ""

    message = f"""
🎯 <b>TRADE ENTRY</b> {mode_badge}

Strategy: {trade_data.get('strategy_id', 'Unknown')}
Symbol: <code>{trade_data.get('symbol')}</code>
Side: {trade_data.get('side')}
Entry: ₹{_p2(trade_data.get('entry_price'))}
Quantity: {trade_data.get('quantity')}

SL: {sl_str} | TP: {tp_str}
Time: {datetime.now().strftime('%H:%M:%S')}{note_line}
""".strip()

    _fanout(NOTIF_TRADE_ACTIVITY, message,
            mode=mode, strategy_id=trade_data.get("strategy_id"))


def notify_tp_exit(trade_data: dict):
    record_event(
        EVENT_TP,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
        exit_price=trade_data.get("exit_price"),
        pnl=trade_data.get("pnl"),
    )

    pnl = trade_data.get("pnl") or 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
{pnl_emoji} <b>TARGET HIT</b> {mode_badge}

Strategy: {trade_data.get('strategy_id')}
Symbol: <code>{trade_data.get('symbol')}</code>
Entry: ₹{_p2(trade_data.get('entry_price'))}
Exit: ₹{_p2(trade_data.get('exit_price'))}

P&L: <b>₹{pnl:,.0f}</b>
Time: {datetime.now().strftime('%H:%M:%S')}
""".strip()

    _fanout(NOTIF_TRADE_ACTIVITY, message,
            mode=mode, strategy_id=trade_data.get("strategy_id"))


def notify_sl_exit(trade_data: dict):
    record_event(
        EVENT_SL,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
        exit_price=trade_data.get("exit_price"),
        pnl=trade_data.get("pnl"),
    )

    pnl = trade_data.get("pnl") or 0
    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
🛑 <b>STOP-LOSS HIT</b> {mode_badge}

Strategy: {trade_data.get('strategy_id')}
Symbol: <code>{trade_data.get('symbol')}</code>
Entry: ₹{_p2(trade_data.get('entry_price'))}
Exit: ₹{_p2(trade_data.get('exit_price'))}

Loss: <b>₹{pnl:,.0f}</b>
Time: {datetime.now().strftime('%H:%M:%S')}
""".strip()

    _fanout(NOTIF_TRADE_ACTIVITY, message,
            mode=mode, strategy_id=trade_data.get("strategy_id"))


def notify_manual_exit(trade_data: dict):
    record_event(
        EVENT_EXIT,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
        exit_price=trade_data.get("exit_price"),
        pnl=trade_data.get("pnl"),
    )

    pnl = trade_data.get("pnl") or 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
{pnl_emoji} <b>POSITION CLOSED</b> {mode_badge}

Strategy: {trade_data.get('strategy_id')}
Symbol: <code>{trade_data.get('symbol')}</code>
Reason: {trade_data.get('exit_reason', 'Manual')}

P&L: <b>₹{pnl:,.0f}</b>
Time: {datetime.now().strftime('%H:%M:%S')}
""".strip()

    _fanout(NOTIF_TRADE_ACTIVITY, message,
            mode=mode, strategy_id=trade_data.get("strategy_id"))


def notify_position_update(update_data: dict = None):
    """
    Position updates — only during market hours. Built PER CHANNEL so each
    channel's mode_filter decides which sections (LIVE / PAPER) it sees. A
    Live-only channel with no live open positions receives nothing.
    """
    if not _is_market_hours():
        return

    live_open  = _query_open_live_positions()
    paper_open = _query_open_paper_positions()
    if not live_open and not paper_open:
        print("[TELEGRAM] Position update skipped — no open positions in DB")
        return

    try:
        from app.marketdata.ltp_store import LTPStore
    except Exception:
        LTPStore = None
    import time as _time

    def _section(title, badge, positions):
        if not positions:
            return None
        unreal = 0.0
        for p in positions:
            result = LTPStore.get_with_timestamp(p["symbol"]) if LTPStore else None
            if result is not None:
                ltp, ts = result
                if (_time.time() - ts) <= 300 and p["entry_price"]:
                    unreal += (ltp - p["entry_price"]) * p["qty"]
        arrow = "▲" if unreal >= 0 else "▼"
        return (f"{badge} <b>{title}</b>\n"
                f"  Open: {len(positions)}\n"
                f"  Unrealized P&L: <b>{arrow} ₹{unreal:+,.0f}</b>")

    # Build once per channel, honoring that channel's mode filter.
    for token, chat_id, ch in _iter_active_channels(NOTIF_POSITION_UPDATES):
        mode = (ch.get("mode_filter", "all") or "all").lower()
        sections = []
        if mode in ("all", "live"):
            s = _section("LIVE", "🟢", live_open)
            if s: sections.append(s)
        if mode in ("all", "paper"):
            s = _section("PAPER", "📄", paper_open)
            if s: sections.append(s)

        if not sections:
            continue  # nothing this channel cares about → silent

        body = "\n\n".join(sections)
        message = (f"📊 <b>POSITION UPDATE</b>\n\n{body}\n\n"
                   f"Time: {datetime.now().strftime('%H:%M:%S')}")
        send_telegram_message(token, chat_id, message)


def notify_critical(alert_data: dict):
    """
    Critical / fatal alerts — GTT failures, DB failures, unprotected positions,
    relay down, etc. Collapsed under the channel 'criticalAlerts' toggle.

    SIGNATURE PRESERVED: existing callers pass {"severity","message"} and
    OPTIONALLY a "strategy_id". If strategy_id is present, the alert respects
    each channel's strategy filter; if absent, it is SYSTEM-WIDE (bypasses the
    strategy filter) so relay/DB emergencies reach every criticalAlerts-on
    channel regardless of filter.
    """
    severity = alert_data.get("severity", "error")
    emoji = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "🚨")
    strategy_id = alert_data.get("strategy_id")  # may be None

    strat_line = f"\nStrategy: {strategy_id}" if strategy_id else ""
    message = f"""
{emoji} <b>CRITICAL ALERT</b>{strat_line}

{alert_data.get('message', '')}

Time: {datetime.now().strftime('%H:%M:%S')}
""".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message, strategy_id=strategy_id)


def notify_order_rejection(alert_data: dict):
    """
    Order-rejection alert (Zerodha REJECTED postback). Routed under the
    'criticalAlerts' channel toggle. strategy_id (resolved by the listener via
    order_id lookup) may be None -> system-wide.

    Expected keys: strategy_id (opt), symbol, status_message, order_id.
    """
    strategy_id = alert_data.get("strategy_id")
    symbol = alert_data.get("symbol", "—")
    reason = alert_data.get("status_message", "") or "Order rejected by broker"
    order_id = alert_data.get("order_id", "")

    strat_line = f"\nStrategy: {strategy_id}" if strategy_id else ""
    oid_line   = f"\nOrder ID: <code>{order_id}</code>" if order_id else ""

    message = f"""
🚫 <b>ORDER REJECTED</b>{strat_line}

Symbol: <code>{symbol}</code>
Reason: {reason}{oid_line}

Time: {datetime.now().strftime('%H:%M:%S')}
""".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message, strategy_id=strategy_id)


def notify_system_alert(alert_data: dict):
    """
    System alert — collapsed under 'criticalAlerts' (the old separate
    systemAlerts toggle no longer exists). Always system-wide (no strategy_id).
    Retained because the relay monitor and other infra call it directly.
    """
    severity = alert_data.get("severity", "info")
    emoji = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "ℹ️")

    message = f"""
{emoji} <b>SYSTEM ALERT</b>

{alert_data.get('message')}

Time: {datetime.now().strftime('%H:%M:%S')}
""".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message)


def notify_daily_summary(summary_data: dict = None):
    """
    TEXT daily summary (fail-open fallback for the card path). Queries LIVE
    (`trades`) and PAPER (`paper_trades`) directly and fans out to channels
    with dailySummary ON. Not strategy-tagged; not mode-filtered (it's a
    combined report).

    NOTE: the card path (telegram_summary_send.send_daily_summary_card) is the
    normal EOD path and is fanned out per-channel by the scheduler. This text
    version is only reached on card failure, and likewise fans out here.
    """
    live  = _query_today_live_summary()
    paper = _query_today_paper_summary()
    combined_pnl   = live["total_pnl"] + paper["total_pnl"]
    combined_emoji = "🟢" if combined_pnl >= 0 else "🔴"

    live_lines = []
    if live["trade_count"] > 0:
        live_lines.append(f"🟢 <b>LIVE</b> — {live['trade_count']} trades · {live['wins']}W/{live['losses']}L")
        for strat, data in live["by_strategy"].items():
            live_lines.append(f"  {strat}: ₹{data['pnl']:+,.0f} ({data['count']} trades)")
        live_lines.append(f"  <b>Subtotal: ₹{live['total_pnl']:+,.0f}</b>")
    else:
        live_lines.append("🟢 <b>LIVE</b> — No trades today")

    paper_lines = []
    if paper["trade_count"] > 0:
        paper_lines.append(f"📄 <b>PAPER</b> — {paper['trade_count']} trades · {paper['wins']}W/{paper['losses']}L")
        for strat, data in paper["by_strategy"].items():
            paper_lines.append(f"  {strat}: ₹{data['pnl']:+,.0f} ({data['count']} trades)")
        paper_lines.append(f"  <b>Subtotal: ₹{paper['total_pnl']:+,.0f}</b>")
    else:
        paper_lines.append("📄 <b>PAPER</b> — No trades today")

    message = f"""
📊 <b>DAILY SUMMARY</b>

{chr(10).join(live_lines)}

{chr(10).join(paper_lines)}

──────────────────
Combined P&L: {combined_emoji} <b>₹{combined_pnl:+,.0f}</b>
Date: {datetime.now().strftime('%d %b %Y')}
""".strip()

    _fanout(NOTIF_DAILY_SUMMARY, message)


# ==========================================================
# DEBUG - MANUAL DAILY SUMMARY
# ==========================================================

@router.get("/debug/run-daily-summary")
async def debug_run_daily_summary(request: Request):
    scheduler = request.app.state.telegram_scheduler
    scheduler.run_daily_summary_now()
    return {"success": True, "message": "Manual daily summary triggered."}