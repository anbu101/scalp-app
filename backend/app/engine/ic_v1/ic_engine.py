# backend/app/engine/ic_v1/ic_engine.py
#
# IC_V1 — Engine (entry scheduler + price watcher + carry/EOD state machine)
# ============================================================================
# IC_V1 has NO signal pipeline: no candles, no indicators, no selection loop.
# One scheduled entry per day, then watch the legs until exit.
#
#   PRICE FEED = REST POLL, NOT A 7th WEBSOCKET (unchanged; house doctrine).
#
# DAILY STATE MACHINE (evaluated every poll, IST fixed +05:30) — IC_V2
# semantics (locked 2026-07-26):
#
#   0. process_due() — scheduled MTC activations (+60s) and adjustment
#      placements (adjust_delay_s) fire here, every iteration.
#
#   1. CARRY MORNING (a carried group is open — restored at boot or held
#      in-memory across the night):
#      a. PRE-MARKET (< 09:15): cancel every carried leg's overnight GTTs
#         (first-candle rule: NOTHING may fire in 09:15–09:16). Retried
#         every iteration until all-clear; failure alerts CRITICAL.
#      b. 09:15 → next_open_time (09:16): HOLD. LTPs are polled for the UI
#         but NO exit evaluation runs (gm.set_carry_hold + no on_tick).
#      c. >= next_open_time: morning_square_off() retry loop — STRICT
#         market closes, shorts first, the SOLE exit executor (DA2: retries
#         until the broker cooperates, CRITICAL alert cadence while stuck).
#         The 09:18 entry stays blocked (open-book gate) until this
#         reconciles — a late morning close eats into/skips the entry
#         window by design (D8: block entry until resolved).
#
#   2. ENTRY WINDOW [entry_time, +grace]: unchanged, EXCEPT the attempt is
#      NOT consumed while the book is still open (carry morning overrun) —
#      the engine waits inside the window and LATE naturally skips the day.
#
#   3. SESSION END (exit_mode = NEXT_OPEN):
#      * expiry-day backstop: >= expiry_exit_time (15:28) →
#        expiry_square_off(today) — closes ONLY legs entered TODAY whose
#        expiry is TODAY (DA5). Continuous (APScheduler-death mitigation).
#      * carry commit: >= CARRY_COMMIT_HM (15:30:30) with open legs →
#        commit_carry(): DA5 assert + persisted snapshot (DA1). Legs stay
#        open in memory; GTTs stay armed at the broker overnight (D3).
#      (exit_mode = EOD keeps the legacy continuous 15:28 full square-off.)
#
#   4. OPEN-GROUP price watch → REST-poll LTPs → LTPStore + on_tick().
#
# BOOT RESTORE (DA1): start() loads the carry snapshot if present. Same-day
# restart → plain restore. One night → restore + morning path takes over.
# gap > 3 days (weekend-adjusted alarm) → CRITICAL but STILL restore and
# close (closing late is strictly better than not closing). gap > 7 days →
# refuse + CRITICAL (something is deeply wrong; human decides).
# ============================================================================

import threading
import time
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config, load_strategy_config_ex
from app.risk.strategy_max_loss_guard import resolve_execution_mode
from app.marketdata.ltp_store import LTPStore

from app.engine.ic_v1.ic_selection import (
    snapshot_weekly_chain, select_ic_strikes, build_chain_candidates,
)
from app.engine.ic_v1.ic_group_manager import ICGroupManager, DEFAULT_LEGS, STRATEGY_ID
from app.engine.ic_v1 import ic_carry_store

IST = timezone(timedelta(minutes=330))    # house rule: fixed offset, no pytz

IDLE_POLL_S    = 5
OPEN_POLL_S    = 4
MORNING_POLL_S = 2                        # tight loop while the 09:16 close retries
ENTRY_GRACE_S  = 120                      # config-overridable: entry_late_grace_s

# Carry commit instant: strictly after the 15:29 candle completes.
CARRY_COMMIT_HM_S = (15, 30, 30)

# Morning-close stuck-alert cadence (DA2)
MORNING_ALERT_EVERY_S = 120

# Carry age policy (DA1)
CARRY_ALARM_GAP_DAYS  = 3    # > this → extra CRITICAL (more than one night)
CARRY_REFUSE_GAP_DAYS = 7    # > this → refuse restore, human decides


def now_ist() -> datetime:
    return datetime.now(IST)


def hm_to_dt(hm: str, ref: datetime) -> datetime:
    """'09:18' → today's aware datetime at 09:18:00 IST."""
    h, m = hm.strip().split(":")
    return ref.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def entry_window_state(now: datetime, entry_hm: str, grace_s: int) -> str:
    """Pure: 'BEFORE' | 'IN_WINDOW' | 'LATE' relative to the entry instant."""
    entry_dt = hm_to_dt(entry_hm, now)
    if now < entry_dt:
        return "BEFORE"
    if (now - entry_dt).total_seconds() <= grace_s:
        return "IN_WINDOW"
    return "LATE"


class ICEngine:

    def __init__(self, group_manager: ICGroupManager, broker_manager):
        self.gm = group_manager
        self.broker = broker_manager
        self._running = False
        self._attempt_date: Optional[str] = None   # in-memory once-per-day guard
        self._thread: Optional[threading.Thread] = None
        # ── IC_V2 ── carry-morning bookkeeping
        self._premarket_clear_date: Optional[str] = None  # date GTTs verified gone
        self._last_morning_alert_ts: float = 0.0
        self._entry_wait_logged_ts: float = 0.0
        # wire the adjustment chain provider (DA3)
        self.gm.attach_chain_provider(self._chain_provider)

    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._restore_carry_if_any()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="ICV1Engine")
        self._thread.start()
        write_audit_log("[IC][ENGINE] started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                slept = self._step(now_ist())
            except Exception as e:
                write_audit_log(f"[IC][ENGINE][STEP_ERR] {repr(e)}")
                slept = IDLE_POLL_S
            time.sleep(slept)

    # ------------------------------------------------------------------
    def _cfg(self) -> dict:
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # BOOT RESTORE (DA1)
    # ------------------------------------------------------------------
    def _restore_carry_if_any(self):
        payload = ic_carry_store.load_carry()
        if payload is None:
            if ic_carry_store.carry_exists():
                # file present but unreadable/version-mismatched: NEVER
                # silently drop a live overnight book.
                write_audit_log("[IC][CARRY][UNREADABLE] snapshot present but "
                                "unreadable — MANUAL intervention required")
                record_alert("IC_CARRY_UNREADABLE",
                             "IC_V1: overnight carry snapshot UNREADABLE — "
                             "check positions in Kite and square off manually.",
                             severity="error", strategy_id=STRATEGY_ID)
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({"message":
                        "IC_V1: carry snapshot unreadable at boot. If an "
                        "overnight IC position exists, close it MANUALLY.",
                        "severity": "error"})
                except Exception:
                    pass
            return

        entry_date_s = str(payload.get("entry_date") or "")
        try:
            gap_days = (now_ist().date() - date.fromisoformat(entry_date_s)).days
        except Exception:
            gap_days = 0

        if gap_days > CARRY_REFUSE_GAP_DAYS:
            write_audit_log(f"[IC][CARRY][REFUSED] snapshot {gap_days}d old — "
                            f"human intervention required")
            record_alert("IC_CARRY_STALE",
                         f"IC_V1: carry snapshot is {gap_days} days old — "
                         f"NOT auto-restored. Verify Kite positions manually.",
                         severity="error", strategy_id=STRATEGY_ID)
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({"message":
                    f"IC_V1: refusing to restore a {gap_days}-day-old carry "
                    f"snapshot. Check Kite positions NOW.",
                    "severity": "error"})
            except Exception:
                pass
            return

        ok = self.gm.restore_carry_payload(payload)
        if not ok:
            record_alert("IC_CARRY_RESTORE_FAIL",
                         "IC_V1: carry snapshot restore FAILED — verify "
                         "Kite positions manually.",
                         severity="error", strategy_id=STRATEGY_ID)
            return

        if gap_days > CARRY_ALARM_GAP_DAYS:
            # more than a weekend-length gap: ONE_NIGHT_MAX was violated by
            # the app being closed. Close ASAP; say so loudly.
            record_alert("IC_CARRY_OVERDUE",
                         f"IC_V1: carried position is {gap_days} days old "
                         f"(ONE_NIGHT_MAX exceeded — app was closed). It "
                         f"will be squared off at the next open window.",
                         severity="error", strategy_id=STRATEGY_ID)
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({"message":
                    f"IC_V1: overnight carry is {gap_days} days old — "
                    f"closing at the next 09:16 window. Verify in Kite.",
                    "severity": "error"})
            except Exception:
                pass
        write_audit_log(f"[IC][ENGINE] carry restored (entry_date="
                        f"{entry_date_s} gap_days={gap_days})")

    # ------------------------------------------------------------------
    # ADJ chain provider (DA3): fresh snapshot at activation time
    # ------------------------------------------------------------------
    def _chain_provider(self):
        kite_data = self.broker.get_data_kite()
        api_key, access_token = self._data_creds()
        expiry, rows, ltps = snapshot_weekly_chain(kite_data, api_key, access_token)
        if expiry is None:
            return None, [], [], {}
        ce, pe, tokens = build_chain_candidates(rows, ltps)
        return expiry, ce, pe, tokens

    # ------------------------------------------------------------------
    def _step(self, now: datetime) -> float:
        """One scheduler iteration. Returns seconds to sleep."""
        cfg          = self._cfg()
        entry_hm     = cfg.get("entry_time", "09:18")
        grace        = int(cfg.get("entry_late_grace_s", ENTRY_GRACE_S))
        exit_mode    = str(cfg.get("exit_mode", "NEXT_OPEN") or "NEXT_OPEN").upper()
        next_open_hm = cfg.get("next_open_time", "09:16")
        expiry_hm    = cfg.get("expiry_exit_time", "15:28")
        legacy_hm    = cfg.get("exit_time", "15:28")
        today        = now.strftime("%Y-%m-%d")

        # 0) scheduled MTC / adjustment activations — every iteration
        if self.gm.has_open_group():
            self.gm.process_due(int(time.time()))

        # 1) CARRY MORNING state machine
        if self.gm.has_carried_open():
            next_open_dt = hm_to_dt(next_open_hm, now)
            open_0915_dt = hm_to_dt("09:15", now)

            if now < open_0915_dt:
                # 1a. pre-market GTT teardown (retry until clear; idempotent)
                self.gm.set_carry_hold(True)
                if self._premarket_clear_date != today:
                    try:
                        if self.gm.premarket_cancel_gtts():
                            self._premarket_clear_date = today
                    except Exception as e:
                        write_audit_log(f"[IC][ENGINE][PREMARKET_ERR] {e!r}")
                return IDLE_POLL_S

            if now < next_open_dt:
                # 1b. FIRST-CANDLE HOLD: prices for the UI, no exits.
                self.gm.set_carry_hold(True)
                if self._premarket_clear_date != today:
                    # degraded: teardown never completed pre-market (broker
                    # late?) — keep trying, a GTT firing here violates the
                    # first-candle rule and must stay loud.
                    try:
                        if self.gm.premarket_cancel_gtts():
                            self._premarket_clear_date = today
                    except Exception as e:
                        write_audit_log(f"[IC][ENGINE][PREMARKET_ERR] {e!r}")
                self._poll_ltps(update_only=True)
                return MORNING_POLL_S

            # 1c. >= next_open: the SOLE exit executor, retry loop (DA2)
            self.gm.set_carry_hold(False)
            remaining = self.gm.morning_square_off()
            if remaining > 0:
                if time.time() - self._last_morning_alert_ts >= MORNING_ALERT_EVERY_S:
                    self._last_morning_alert_ts = time.time()
                    record_alert("IC_MORNING_STUCK",
                                 f"IC_V1: morning square-off retrying — "
                                 f"{remaining} carried leg(s) still open.",
                                 severity="error", strategy_id=STRATEGY_ID)
                    try:
                        from app.api.telegram_api import notify_critical
                        notify_critical({"message":
                            f"IC_V1: 09:16 square-off NOT complete — "
                            f"{remaining} leg(s) still open, retrying. "
                            f"Check broker session / Kite.",
                            "severity": "error"})
                    except Exception:
                        pass
                return MORNING_POLL_S
            # carry fully closed → fall through (entry window may follow)

        # 2) entry window — attempt NOT consumed while the book is open
        if self._attempt_date != today:
            state = entry_window_state(now, entry_hm, grace)
            if state == "IN_WINDOW":
                if self.gm.has_open_group():
                    # carry-morning overrun (D8: block until resolved).
                    if time.time() - self._entry_wait_logged_ts >= 30:
                        self._entry_wait_logged_ts = time.time()
                        write_audit_log("[IC][ENGINE] entry window open but "
                                        "book not flat — waiting (D8)")
                    return MORNING_POLL_S
                self._attempt_date = today
                self._attempt_entry(cfg)
                return IDLE_POLL_S
            if state == "LATE":
                self._attempt_date = today
                write_audit_log(f"[IC][ENGINE] woke LATE (> {grace}s past "
                                f"{entry_hm}) — skipping day (late-entry guard)")
                record_alert("IC_LATE_SKIP",
                             f"IC_V1: engine past entry window ({entry_hm} "
                             f"+{grace}s) — no entry today",
                             severity="warning", strategy_id=STRATEGY_ID)
                return IDLE_POLL_S

        # 3) session-end handling for TODAY-entered legs
        if self.gm.has_open_group():
            if exit_mode == "EOD":
                # legacy continuous backstop — full square-off
                if now >= hm_to_dt(legacy_hm, now):
                    write_audit_log("[IC][ENGINE] EOD backstop firing (legacy)")
                    self.gm.force_square_off_all(reason="EOD")
                    return IDLE_POLL_S
            else:
                # NEXT_OPEN: expiry-day 15:28 closes ONLY today's expiring
                # legs (DA5) — continuous backstop.
                if now >= hm_to_dt(expiry_hm, now):
                    n = self.gm.expiry_square_off(today)
                    if n:
                        write_audit_log(f"[IC][ENGINE] expiry square-off "
                                        f"closed {n} leg(s)")
                # carry commit strictly after session end
                h, m, s = CARRY_COMMIT_HM_S
                commit_dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                if now >= commit_dt and self.gm.has_open_group() \
                        and not self.gm.carry_committed():
                    mode = "PAPER" if self.gm.is_paper() else "LIVE"
                    ok = self.gm.commit_carry(mode)
                    write_audit_log(f"[IC][ENGINE] carry commit → {ok}")
                    return IDLE_POLL_S

        # 4) open-group price watch
        if self.gm.has_open_group():
            self._poll_ltps()
            return OPEN_POLL_S

        return IDLE_POLL_S

    # ------------------------------------------------------------------
    def _attempt_entry(self, cfg: dict):
        # DEGRADED-READ GUARD (HA precedent: _ex loader for the mode-
        # sensitive decision).
        try:
            cfg_ex, degraded = load_strategy_config_ex(STRATEGY_ID)
            if degraded:
                write_audit_log("[IC][ENGINE] CONFIG DEGRADED at entry — "
                                "skipping day (fail closed)")
                record_alert("IC_MODE_DEGRADED",
                             "IC_V1: config unreadable at entry time — day "
                             "SKIPPED (fail closed). Check the config file.",
                             severity="error", strategy_id=STRATEGY_ID)
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({"message":
                        "IC_V1: config unreadable at entry time — no entry "
                        "today (fail closed).", "severity": "error"})
                except Exception:
                    pass
                return
            cfg = cfg_ex   # clean read is authoritative for this attempt
        except Exception as e:
            write_audit_log(f"[IC][ENGINE] config read raised {e!r} — skip day")
            return

        raw_mode = (cfg.get("trade_execution_mode") or "PAPER").strip().upper()
        if raw_mode == "OFF":
            write_audit_log("[IC][ENGINE] mode=OFF — no entry today")
            return

        mode, degraded = resolve_execution_mode(STRATEGY_ID)
        if degraded:
            record_alert("IC_MODE_DEGRADED",
                         "IC_V1: config unreadable — forced PAPER this session",
                         severity="error", strategy_id=STRATEGY_ID)

        # market-day sanity (fail open: gates + selection still protect)
        try:
            from app.utils.market_hours import is_market_open
            if not is_market_open():
                write_audit_log("[IC][ENGINE] market closed — no entry")
                return
        except Exception:
            pass

        try:
            if not self.broker.is_ready():
                write_audit_log("[IC][ENGINE] broker not ready at entry — skip day")
                record_alert("IC_NO_ENTRY", "IC_V1: broker not ready at entry time",
                             severity="error", strategy_id=STRATEGY_ID)
                return
            kite_data = self.broker.get_data_kite()
            api_key, access_token = self._data_creds()
        except Exception as e:
            write_audit_log(f"[IC][ENGINE] broker access failed: {e} — skip day")
            return

        expiry, rows, ltps = snapshot_weekly_chain(kite_data, api_key, access_token)
        if expiry is None:
            record_alert("IC_NO_ENTRY", "IC_V1: chain snapshot failed — no entry",
                         severity="error", strategy_id=STRATEGY_ID)
            return

        legs_cfg = cfg.get("legs") or DEFAULT_LEGS
        ce, pe, tokens = build_chain_candidates(rows, ltps)
        selection = select_ic_strikes(legs_cfg, ce, pe, tokens, expiry)

        opened = self.gm.enter_day(selection, mode=mode)
        write_audit_log(f"[IC][ENGINE] entry attempt mode={mode} opened={opened}")

    def _data_creds(self):
        """api_key/access_token for instruments freshness check; best-effort."""
        try:
            k = self.broker.get_data_kite()
            return getattr(k, "api_key", None), getattr(k, "access_token", None)
        except Exception:
            return None, None

    # ------------------------------------------------------------------
    def _poll_ltps(self, update_only: bool = False):
        """REST-poll LTPs for open legs. update_only=True (first-candle
        hold): LTPStore refresh for the UI, NO exit evaluation."""
        core = self.gm.current_group()
        if core is None:
            return
        legs = core.open_legs()
        if not legs:
            return
        symbols = {}
        for leg in legs:
            rt = self.gm.leg_runtime(leg.leg_id)
            symbols[f"NFO:{leg.symbol}"] = (rt.get("token", 0), leg.symbol)
        try:
            kite = self.broker.get_data_kite()
            data = kite.ltp(list(symbols.keys()))
        except Exception as e:
            write_audit_log(f"[IC][ENGINE][LTP_POLL_ERR] {e}")
            return
        for key, (token, sym) in symbols.items():
            row = data.get(key) or {}
            ltp = float(row.get("last_price") or 0.0)
            if ltp <= 0:
                continue
            try:
                LTPStore.update(sym, ltp)
            except Exception:
                pass
            if not update_only:
                self.gm.on_tick(token, ltp)
