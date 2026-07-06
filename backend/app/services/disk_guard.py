# backend/app/services/disk_guard.py
"""
DISK GUARD — free-space watchdog
================================
Background daemon that watches free disk space on the volume holding the app's
data (~/.scalp-app: SQLite DBs, WAL, configs, state, logs) and warns EARLY —
before a full disk starts failing SQLite writes mid-trade.

WHY THIS EXISTS (2026-07-06 incident):
  The machine hit 100% disk (1.6 GB free) and, separately, fd exhaustion. The
  first *symptom* the user saw was a naked position — there was NO advance
  warning that the box was starved. A trading terminal must shout while there
  is still runway to act, not surface the problem as a broken trade.

DESIGN (deliberate choices):
  - OWN DEDICATED THREAD, not APScheduler and not the TelegramScheduler. A
    watchdog must not share the failing subsystem it watches: if the disk is
    full or fds are exhausted, the alert path must be as lean as possible —
    one shutil.disk_usage() read and one requests.post per channel. (The
    TelegramScheduler also does DB reads for summaries; we bypass all of it.)
  - PURE OBSERVER. This module NEVER blocks entries, never touches any trading
    path, never force-exits anything. Per product decision, low disk raises
    NOTIFICATIONS only. If disk_usage() itself throws, we log and skip the
    cycle — the guard can never crash the app.
  - EDGE-TRIGGERED. State machine OK -> LOW -> CRITICAL. An alert fires ONLY on
    a state CHANGE (downward escalation or upward recovery), NOT every cycle.
    A box parked at 1.8 GB for a week yields exactly ONE low alert, not one
    every 2 h. (Same model as the relay monitor / record_alert_once.)
  - TWO TIERS. 2 GB -> DISK_LOW (warning). 400 MB -> DISK_CRITICAL (error,
    louder tone). Both advisory; neither gates trading.

ALERT CHANNELS:
  - In-app: record_alert(code=..., severity=..., strategy_id="") -> renders as
    "System · <title>" in the bell (titles added to NotificationProvider's
    ALERT_TITLE map: DISK_LOW / DISK_CRITICAL / DISK_OK).
  - Telegram: sent DIRECTLY to EVERY enabled channel with a chat_id, bypassing
    the criticalAlerts toggle, the schedule window, and mode/strategy filters.
    Storage is INFRASTRUCTURE, not a trade event — a disk emergency must reach
    every channel even if that channel muted critical alerts or set a window
    that has already closed for the day. This is why we do NOT use
    notify_system_alert (which routes through the filtered _fanout path).

REMOVAL: delete this file + the DISK_GUARD block in api_server.py (grep
"DISK_GUARD") + the three DISK_* entries in NotificationProvider's ALERT_TITLE.
"""

import shutil
import threading
import time
from pathlib import Path

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert

# The Telegram module global is read at SEND time (not cached at thread start),
# so a mid-session Telegram settings edit is honored — matching the rest of
# telegram_api.py's behaviour.
from app.api import telegram_api


# --------------------------------------------------------------------
# CONFIG (constants — deliberately not user-tunable; these are safety floors)
# --------------------------------------------------------------------

# Volume to watch: the app-data dir, NOT "/" — this is where the DBs/WAL live.
_WATCH_PATH = Path.home() / ".scalp-app"

# Thresholds (bytes).
_LOW_FLOOR_BYTES      = 2 * 1024 * 1024 * 1024      # 2 GB  -> DISK_LOW
_CRITICAL_FLOOR_BYTES = 400 * 1024 * 1024           # 400 MB -> DISK_CRITICAL

# Cadence.
_FIRST_CHECK_DELAY_S = 60          # let the app settle before the first read
_CHECK_INTERVAL_S    = 2 * 3600    # every 2 hours

# Internal state names.
_STATE_OK       = "OK"
_STATE_LOW      = "LOW"
_STATE_CRITICAL = "CRITICAL"

# Rank for escalation/recovery comparison.
_STATE_RANK = {_STATE_OK: 0, _STATE_LOW: 1, _STATE_CRITICAL: 2}


# --------------------------------------------------------------------
# Module state
# --------------------------------------------------------------------

_started = False
_start_lock = threading.Lock()

# Last observed state; None until the first successful reading seeds it.
# Seeding: if the FIRST reading is already LOW/CRITICAL (app launched onto an
# already-starved disk), we DO alert on that first observation — unlike a
# mode-transition seed, a disk that is already low at boot is a real condition
# the user needs to know about immediately.
_last_state = None


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

def _fmt_gb(nbytes: float) -> str:
    return f"{nbytes / (1024 ** 3):.2f} GB"


def _classify(free_bytes: int) -> str:
    if free_bytes < _CRITICAL_FLOOR_BYTES:
        return _STATE_CRITICAL
    if free_bytes < _LOW_FLOOR_BYTES:
        return _STATE_LOW
    return _STATE_OK


def _broadcast_all_channels(message: str) -> int:
    """
    Send `message` to EVERY enabled Telegram channel that has a chat_id,
    bypassing ALL filters (criticalAlerts toggle, schedule window, mode,
    strategy). Storage alerts are infrastructure and must not be gated.

    Reads the live TELEGRAM_CONFIG at call time. Never raises — a Telegram
    failure must not stop the in-app alert or crash the guard. Returns the
    count sent.
    """
    sent = 0
    try:
        cfg = telegram_api.TELEGRAM_CONFIG or {}
        bot_token = (cfg.get("bot_token") or "").strip()
        if not bot_token:
            return 0
        for ch in (cfg.get("channels") or []):
            try:
                if not ch.get("enabled"):
                    continue
                chat_id = (ch.get("chat_id") or "").strip()
                if not chat_id:
                    continue
                if telegram_api.send_telegram_message(bot_token, chat_id, message):
                    sent += 1
            except Exception as e:
                write_audit_log(f"[DISK_GUARD][TG_CH_ERR] {e}")
    except Exception as e:
        write_audit_log(f"[DISK_GUARD][TG_ERR] {e}")
    return sent


def _emit_alert(state: str, free_bytes: int, *, recovery: bool) -> None:
    """
    Fire the in-app alert + Telegram broadcast for a state transition.
    `recovery=True` means we climbed back up (to OK or from CRITICAL to LOW).
    Never raises.
    """
    free_str = _fmt_gb(free_bytes)

    if state == _STATE_CRITICAL:
        code = "DISK_CRITICAL"
        severity = "error"
        human = (
            f"Storage critically low: {free_str} free on the app data volume. "
            f"SQLite writes (trade records, GTT links) may start FAILING. Free "
            f"space now — clear build caches / old logs."
        )
        tg = (
            f"\U0001F6A8 <b>STORAGE CRITICAL</b>\n\n"
            f"Only <b>{free_str}</b> free on the app-data volume.\n"
            f"Trade-record and GTT writes may start failing.\n"
            f"Free space immediately."
        )
    elif state == _STATE_LOW:
        code = "DISK_LOW"
        severity = "warning"
        if recovery:
            human = (
                f"Storage recovered to LOW: {free_str} free. Still below the "
                f"2 GB comfort floor — keep clearing space."
            )
            tg = (
                f"\u26A0\uFE0F <b>STORAGE LOW</b>\n\n"
                f"Recovered to <b>{free_str}</b> free, but still under 2 GB.\n"
                f"Keep an eye on it."
            )
        else:
            human = (
                f"Storage running low: {free_str} free on the app data volume "
                f"(under 2 GB). No impact yet, but clear space soon — a full "
                f"disk fails SQLite writes mid-trade."
            )
            tg = (
                f"\u26A0\uFE0F <b>STORAGE LOW</b>\n\n"
                f"<b>{free_str}</b> free on the app-data volume (under 2 GB).\n"
                f"No impact yet — clear space soon."
            )
    else:  # OK — recovery
        code = "DISK_OK"
        severity = "info"
        human = f"Storage recovered: {free_str} free. Back above the 2 GB floor."
        tg = (
            f"\u2705 <b>STORAGE RECOVERED</b>\n\n"
            f"<b>{free_str}</b> free — back above 2 GB."
        )

    # In-app (system-scoped: strategy_id="" -> renders "System · <title>").
    try:
        record_alert(
            code=code,
            message=human,
            severity=severity,
            strategy_id="",
            symbol="",
            mode="live",
        )
    except Exception as e:
        write_audit_log(f"[DISK_GUARD][INAPP_ERR] {e}")

    # Telegram — direct broadcast to all channels, bypassing filters.
    n = _broadcast_all_channels(tg)
    write_audit_log(
        f"[DISK_GUARD][ALERT] state={state} free={free_str} "
        f"recovery={recovery} telegram_sent={n}"
    )


# --------------------------------------------------------------------
# Core check
# --------------------------------------------------------------------

def _run_one_check() -> None:
    """One free-space read + edge-triggered alerting. Never raises."""
    global _last_state

    try:
        usage = shutil.disk_usage(str(_WATCH_PATH))
        free = int(usage.free)
    except Exception as e:
        # A failed read must NOT crash the guard and must NOT be treated as a
        # state change. Log and skip this cycle.
        write_audit_log(f"[DISK_GUARD][READ_ERR] {e} — skipping this cycle")
        return

    new_state = _classify(free)
    prev = _last_state

    # First observation — seed. Unlike a mode seed, we DO alert if the machine
    # launched onto an already-starved disk (a real, actionable condition).
    if prev is None:
        _last_state = new_state
        if new_state == _STATE_OK:
            write_audit_log(f"[DISK_GUARD][INIT] OK — {_fmt_gb(free)} free")
        else:
            write_audit_log(
                f"[DISK_GUARD][INIT] {new_state} at boot — {_fmt_gb(free)} free"
            )
            _emit_alert(new_state, free, recovery=False)
        return

    if new_state == prev:
        # No change — stay quiet (edge-triggered). Cheap heartbeat to the log
        # only, so the audit trail shows the guard is alive.
        write_audit_log(
            f"[DISK_GUARD][OK_TICK] state={new_state} free={_fmt_gb(free)} (no change)"
        )
        return

    # State changed — determine direction and alert.
    escalating = _STATE_RANK[new_state] > _STATE_RANK[prev]
    _last_state = new_state
    _emit_alert(new_state, free, recovery=not escalating)


# --------------------------------------------------------------------
# Thread loop + public start
# --------------------------------------------------------------------

def _loop() -> None:
    # Settle delay before the first read (mirrors relay monitor's initial sleep).
    time.sleep(_FIRST_CHECK_DELAY_S)
    while True:
        try:
            _run_one_check()
        except Exception as e:
            # Belt-and-suspenders: _run_one_check already guards itself, but the
            # loop must survive anything.
            write_audit_log(f"[DISK_GUARD][LOOP_ERR] {e}")
        time.sleep(_CHECK_INTERVAL_S)


def start_disk_guard() -> None:
    """
    Launch the disk guard's daemon thread. Idempotent — safe to call once at
    startup. Never raises.
    """
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    try:
        t = threading.Thread(target=_loop, daemon=True, name="DiskGuard")
        t.start()
        write_audit_log(
            f"[DISK_GUARD] Started — watching {_WATCH_PATH} "
            f"(low<{_fmt_gb(_LOW_FLOOR_BYTES)}, crit<{_fmt_gb(_CRITICAL_FLOOR_BYTES)}, "
            f"every {_CHECK_INTERVAL_S // 3600}h)"
        )
    except Exception as e:
        write_audit_log(f"[DISK_GUARD][START_ERR] {e}")