# backend/app/jobs/ic_v1_live_eod.py
#
# IC_V1 — Scheduled jobs (PRIMARY layer; ICEngine is the continuous backstop)
# ============================================================================
# IC_V2 SEMANTICS (2026-07-26):
#
#   ic_v1_live_eod_job — 15:25 IST cron (unchanged registration id).
#     exit_mode "NEXT_OPEN" (default): waits to expiry_exit_time (15:28) and
#       runs expiry_square_off(today) — closes ONLY legs entered TODAY whose
#       expiry is TODAY (DA5). Non-expiry days: clean no-op (legs carry).
#     exit_mode "EOD" (legacy): original behavior — waits to exit_time and
#       force-squares-off everything.
#
#   ic_v1_morning_job — NEW, 09:08 IST cron.
#     Carry-morning scheduled primary: pre-market GTT teardown (first-candle
#     rule), wait to next_open_time (09:16), then the morning square-off
#     retry loop (bounded; the engine's continuous loop keeps retrying after
#     this job's wait cap if the broker is still down — DA2).
#
# LAYERING: either layer alone closes the day; both running is a harmless
# double-check (all close paths are idempotent on closed legs, and
# premarket_cancel_gtts is idempotent).
# MISFIRE: a late fire sees its deadline already past and acts IMMEDIATELY.
# ============================================================================

import time
from datetime import datetime, timedelta, timezone

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert

_IST = timezone(timedelta(minutes=330))
_WAIT_POLL_S = 5
_MAX_WAIT_S  = 15 * 60      # sanity cap: never block a scheduler thread longer


def _now():
    return datetime.now(_IST)


def _hm_deadline(hm: str, now_fn) -> datetime:
    h, m = hm.strip().split(":")
    return now_fn().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def ic_v1_live_eod_job(*, sleep_fn=time.sleep, now_fn=_now):
    """sleep_fn / now_fn injectable for tests."""
    try:
        from app.engine.ic_v1.ic_runtime import get_ic_manager
        from app.config.strategy_loader import load_strategy_config

        gm = get_ic_manager()
        if gm is None:
            write_audit_log("[IC_EOD] manager not initialized — nothing to do")
            return

        try:
            cfg = load_strategy_config("IC_V1") or {}
        except Exception:
            cfg = {}
        exit_mode = str(cfg.get("exit_mode", "NEXT_OPEN") or "NEXT_OPEN").upper()
        legacy_hm = cfg.get("exit_time", "15:28")
        expiry_hm = cfg.get("expiry_exit_time", "15:28")

        deadline = _hm_deadline(legacy_hm if exit_mode == "EOD" else expiry_hm,
                                now_fn)
        waited = 0
        while now_fn() < deadline and waited < _MAX_WAIT_S:
            sleep_fn(_WAIT_POLL_S)
            waited += _WAIT_POLL_S

        if not gm.has_open_group():
            write_audit_log("[IC_EOD] no open group at exit deadline — no-op")
            record_alert("EOD_SQUAREOFF",
                         "IC_V1: EOD ran — no open group to close.",
                         severity="info", strategy_id="IC_V1")
            return

        if exit_mode == "EOD":
            n = gm.force_square_off_all(reason="EOD")
            write_audit_log(f"[IC_EOD] legacy square-off complete legs={n}")
            record_alert("EOD_SQUAREOFF",
                         f"IC_V1: EOD square-off complete — {n} leg(s) closed.",
                         severity="info", strategy_id="IC_V1")
            return

        # NEXT_OPEN: expiry-day scoping only (non-expiry legs carry tonight)
        today = now_fn().strftime("%Y-%m-%d")
        n = gm.expiry_square_off(today)
        if n:
            write_audit_log(f"[IC_EOD] expiry square-off complete legs={n}")
            record_alert("EOD_SQUAREOFF",
                         f"IC_V1: expiry-day square-off — {n} leg(s) closed.",
                         severity="info", strategy_id="IC_V1")
        else:
            write_audit_log("[IC_EOD] NEXT_OPEN mode, no expiring-today legs "
                            "— legs (if any) will carry (ONE_NIGHT_MAX)")

    except Exception as e:
        write_audit_log(f"[IC_EOD][ERROR] {repr(e)}")


def ic_v1_morning_job(*, sleep_fn=time.sleep, now_fn=_now):
    """09:08 IST cron. Carry-morning scheduled primary (engine = backstop)."""
    try:
        from app.engine.ic_v1.ic_runtime import get_ic_manager
        from app.config.strategy_loader import load_strategy_config

        gm = get_ic_manager()
        if gm is None:
            write_audit_log("[IC_MORNING] manager not initialized — nothing to do")
            return
        if not gm.has_carried_open():
            write_audit_log("[IC_MORNING] no carried legs — no-op")
            return

        try:
            cfg = load_strategy_config("IC_V1") or {}
        except Exception:
            cfg = {}
        next_open_hm = cfg.get("next_open_time", "09:16")

        # 1) pre-market GTT teardown (retry to the 09:15 boundary)
        boundary = _hm_deadline("09:15", now_fn)
        while now_fn() < boundary:
            try:
                if gm.premarket_cancel_gtts():
                    break
            except Exception as e:
                write_audit_log(f"[IC_MORNING][PREMARKET_ERR] {e!r}")
            sleep_fn(_WAIT_POLL_S)

        # 2) wait to next_open_time (first-candle rule: NO exits before it)
        deadline = _hm_deadline(next_open_hm, now_fn)
        waited = 0
        while now_fn() < deadline and waited < _MAX_WAIT_S:
            sleep_fn(_WAIT_POLL_S)
            waited += _WAIT_POLL_S

        # 3) morning square-off retry loop (bounded; engine keeps retrying)
        waited = 0
        while waited < _MAX_WAIT_S:
            remaining = gm.morning_square_off()
            if remaining == 0:
                write_audit_log("[IC_MORNING] carry square-off complete")
                record_alert("EOD_SQUAREOFF",
                             "IC_V1: overnight carry closed at next-open.",
                             severity="info", strategy_id="IC_V1")
                return
            sleep_fn(_WAIT_POLL_S)
            waited += _WAIT_POLL_S

        write_audit_log("[IC_MORNING] wait cap hit with legs still open — "
                        "engine loop continues retrying")
        record_alert("IC_MORNING_STUCK",
                     "IC_V1: morning square-off incomplete at job cap — "
                     "engine continues retrying.",
                     severity="error", strategy_id="IC_V1")

    except Exception as e:
        write_audit_log(f"[IC_MORNING][ERROR] {repr(e)}")
