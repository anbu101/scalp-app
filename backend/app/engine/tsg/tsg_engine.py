# backend/app/engine/tsg/tsg_engine.py
#
# TSG_V1 — Engine (entry scheduler + minute evaluator)  — LD2/LD3/LD8
# ============================================================================
# Mirrors ic_engine.py minus everything TSG lacks (no carry, no GTTs, no
# MTC). One scheduled entry per day, then ONE evaluation per minute at
# hh:mm:02 on the last completed 1m close (LD2 — backtest parity), plus a
# continuous EOD backstop (APScheduler-silent-death mitigation, LD8).
#
# DAILY STATE MACHINE (poll loop, IST fixed +05:30, no pytz):
#   1. ENTRY WINDOW [entry_time, +grace]: resolve mode via
#      resolve_execution_mode (LIVE only when positively confirmed —
#      degraded reads alert and drop to PAPER), snapshot the weekly chain
#      (fail closed), gm.enter_day(). LATE skips the day with an alert.
#   2. OPEN BOOK: at each minute boundary (second >= 2, minute changed)
#      → gm.on_minute(now).
#   3. EOD: now >= exit_time → gm.square_off_all("EOD"), continuously
#      retried while anything stays open.
# ============================================================================

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config
from app.risk.strategy_max_loss_guard import resolve_execution_mode

from app.engine.tsg.tsg_manager import TsgManager, STRATEGY_ID

IST = timezone(timedelta(minutes=330))

IDLE_POLL_S = 5
OPEN_POLL_S = 2
DISPLAY_POLL_S = 4          # panel LTP/IV refresh cadence (display only)
ENTRY_GRACE_S = 120


def now_ist() -> datetime:
    return datetime.now(IST)


def hm_to_dt(hm: str, ref: datetime) -> datetime:
    h, m = hm.strip().split(":")
    return ref.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def entry_window_state(now: datetime, entry_hm: str, grace_s: int) -> str:
    entry_dt = hm_to_dt(entry_hm, now)
    if now < entry_dt:
        return "BEFORE"
    if (now - entry_dt).total_seconds() <= grace_s:
        return "IN_WINDOW"
    return "LATE"


class TsgEngine:

    def __init__(self, manager: TsgManager, broker_manager):
        self.gm = manager
        self.broker = broker_manager
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_eval_minute: Optional[str] = None   # "YYYY-MM-DD HH:MM"
        self._last_display_ts: float = 0.0
        self._late_alert_date: Optional[str] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop,
                                        name="tsg-engine", daemon=True)
        self._thread.start()
        write_audit_log("[TSG][ENGINE] started")

    def stop(self):
        self._running = False

    def _cfg(self) -> dict:
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception:
            return {}

    def _chain(self):
        """snapshot_weekly_chain via broker creds; (None, [], {}) on any
        failure — the manager treats that as NO ENTRY (fail closed)."""
        try:
            from app.engine.ic.ic_selection import snapshot_weekly_chain
            kite = self.broker.get_data_kite()
            if kite is None:
                # NEVER silent (2026-08-03 lesson: the first live entry
                # attempt failed with no log line — only the manager's
                # generic NO_ENTRY alert). Name the actual cause.
                write_audit_log("[TSG][CHAIN_FAIL] data kite unavailable "
                                "(broker not connected / not logged in)")
                return (None, [], {})
            # Creds are BEST-EFFORT (IC _data_creds pattern, off the kite
            # object itself): snapshot_weekly_chain only uses them for an
            # instruments-freshness check. Missing creds must NOT block
            # the entry — gating on them caused the 2026-08-03 09:16
            # NO_ENTRY with a healthy broker.
            api_key, access_token = self._data_creds(kite)
            return snapshot_weekly_chain(kite, api_key, access_token)
        except Exception as e:
            write_audit_log(f"[TSG][CHAIN_FAIL] {e!r}")
            return (None, [], {})

    def _data_creds(self, kite=None):
        try:
            k = kite if kite is not None else self.broker.get_data_kite()
            return (getattr(k, "api_key", None),
                    getattr(k, "access_token", None))
        except Exception:
            return (None, None)

    def _quote_many(self, symbols):
        try:
            kite = self.broker.get_data_kite()
            if kite is None or not symbols:
                return {}
            q = kite.quote([f"NFO:{s}" for s in symbols]) or {}
            out = {}
            for k, row in q.items():
                sym = k.split(":", 1)[-1]
                v = float((row or {}).get("last_price") or 0)
                if v > 0:
                    out[sym] = v
            return out
        except Exception as e:
            write_audit_log(f"[TSG][QUOTE_FAIL] {e!r}")
            return {}

    # ── main loop ───────────────────────────────────────────────────────
    def _loop(self):
        self.gm.attach_quote_fn(self._quote_many)
        while self._running:
            try:
                sleep_s = self._step(now_ist())
            except Exception as e:
                write_audit_log(f"[TSG][ENGINE][STEP_ERR] {e!r}")
                sleep_s = IDLE_POLL_S
            time.sleep(max(0.5, sleep_s))

    def _step(self, now: datetime) -> float:
        cfg = self._cfg()
        entry_hm = str(cfg.get("entry_time", "09:16"))
        exit_hm = str(cfg.get("exit_time", "15:26"))
        grace_s = int(cfg.get("entry_late_grace_s", ENTRY_GRACE_S)
                      or ENTRY_GRACE_S)
        today = now.date().isoformat()

        # 3. EOD backstop — continuous while anything stays open (LD8)
        if now.strftime("%H:%M") >= exit_hm and self.gm.has_open_day():
            n = self.gm.square_off_all("EOD")
            if n:
                write_audit_log(f"[TSG][EOD] squared off {n} leg(s)")
            return OPEN_POLL_S

        # 1. entry window
        if not self.gm.latched_today() and not self.gm.has_open_day():
            st = entry_window_state(now, entry_hm, grace_s)
            if st == "IN_WINDOW":
                mode, degraded = resolve_execution_mode(STRATEGY_ID)
                if degraded:
                    record_alert("TSG_MODE_DEGRADED",
                                 "TSG_V1 config unreadable — degraded to "
                                 "PAPER for today's entry",
                                 severity="error", strategy_id=STRATEGY_ID,
                                 mode="paper")
                if str(cfg.get("trade_execution_mode", "OFF")).upper() \
                        == "OFF":
                    return IDLE_POLL_S
                self.gm.enter_day(self._chain(), mode=mode)
                return OPEN_POLL_S
            if st == "LATE" and self._late_alert_date != today:
                self._late_alert_date = today
                write_audit_log("[TSG][ENTRY] window missed — day skipped")
            return IDLE_POLL_S

        # 2. minute evaluator (LD2): hh:mm with second >= 2, once per minute
        if self.gm.has_open_day():
            evaluated = False
            if now.second >= 2:
                key = now.strftime("%Y-%m-%d %H:%M")
                if key != self._last_eval_minute:
                    self._last_eval_minute = key
                    self.gm.on_minute(now)
                    self._last_display_ts = time.time()
                    evaluated = True
            # display-only LTP/IV refresh between evaluations (never
            # evaluates exits — see TsgManager.refresh_display)
            if not evaluated and \
                    time.time() - self._last_display_ts >= DISPLAY_POLL_S:
                self._last_display_ts = time.time()
                try:
                    self.gm.refresh_display()
                except Exception as e:
                    write_audit_log(f"[TSG][DISPLAY_ERR] {e!r}")
            return 1.0
        return IDLE_POLL_S