# backend/app/jobs/ic_live_eod.py
#
# IC (shared V1/V2) — Scheduled jobs (PRIMARY layer; ICEngine is the
# continuous backstop)
# ============================================================================
# ── IC_SPLIT (2026-08-04) ── each job now ITERATES the IC_REGISTRY, acting
# per instance on that instance's OWN config:
#
#   ic_live_eod_job — 15:25 IST cron (registration id ic_live_eod_squareoff).
#     Per instance:
#     exit_mode "EOD" (IC_V1 default): waits to that instance's exit_time and
#       force-squares-off everything (legacy IC_V1 behavior).
#     exit_mode "NEXT_OPEN" (IC_V2 default): waits to expiry_exit_time and
#       runs expiry_square_off(today) — closes ONLY legs entered TODAY whose
#       expiry is TODAY (DA5). Non-expiry days: clean no-op (legs carry).
#
#   ic_morning_job — 09:08 IST cron.
#     Carry-morning scheduled primary. Only instances with carried legs act
#     (an EOD-mode instance never carries, so IC_V1 is a structural no-op):
#     pre-market GTT teardown (first-candle rule), wait to next_open_time
#     (09:16), then the morning square-off retry loop (bounded; the engine's
#     continuous loop keeps retrying after this job's wait cap — DA2).
#
# The single 15:25 wait loop uses the LATEST deadline across instances and
# processes each instance the moment ITS deadline passes — one scheduler
# thread, no per-strategy cron proliferation.
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


def _instances():
    """[(sid, gm, cfg)] for every initialized IC runtime. Isolated
    try/excepts — one broken instance never blocks the sibling."""
    out = []
    try:
        from app.engine.ic.ic_runtime import IC_STRATEGY_IDS, get_ic_manager
        from app.config.strategy_loader import load_strategy_config
        for sid in IC_STRATEGY_IDS:
            gm = get_ic_manager(sid)
            if gm is None:
                continue
            try:
                cfg = load_strategy_config(sid) or {}
            except Exception:
                cfg = {}
            out.append((sid, gm, cfg))
    except Exception as e:
        write_audit_log(f"[IC_EOD][INSTANCES_ERR] {repr(e)}")
    return out


def _eod_deadline(cfg: dict) -> str:
    exit_mode = str(cfg.get("exit_mode", "NEXT_OPEN") or "NEXT_OPEN").upper()
    if exit_mode == "EOD":
        return cfg.get("exit_time", "15:28")
    return cfg.get("expiry_exit_time", "15:28")


def _eod_one(sid: str, gm, cfg: dict, now_fn) -> None:
    """EOD action for ONE instance (deadline already passed)."""
    try:
        exit_mode = str(cfg.get("exit_mode", "NEXT_OPEN") or "NEXT_OPEN").upper()

        if not gm.has_open_group():
            write_audit_log(f"[IC_EOD][{sid}] no open group at exit deadline "
                            f"— no-op")
            # CAS_NOTIF: backstop status bell removed — this cron is a 15:25 safety
            # sweep; actual closes are announced by the engines at their configured
            # exit times (<=15:15 post-CAS). Bell here rang at 15:25 even when there
            # was nothing to do, which read as a late close. Audit log retained.
            return

        if exit_mode == "EOD":
            n = gm.force_square_off_all(reason="EOD")
            write_audit_log(f"[IC_EOD][{sid}] legacy square-off complete "
                            f"legs={n}")
            # CAS_NOTIF: bell only when the backstop ACTUALLY closed legs.
            if n:
                record_alert("EOD_SQUAREOFF",
                             f"{sid}: EOD square-off complete — {n} leg(s) closed.",
                             severity="info", strategy_id=sid)
            return

        # NEXT_OPEN: expiry-day scoping only (non-expiry legs carry tonight)
        today = now_fn().strftime("%Y-%m-%d")
        n = gm.expiry_square_off(today)
        if n:
            write_audit_log(f"[IC_EOD][{sid}] expiry square-off complete "
                            f"legs={n}")
            # CAS_NOTIF: bell only when the backstop ACTUALLY closed legs.
            if n:
                record_alert("EOD_SQUAREOFF",
                             f"{sid}: expiry-day square-off — {n} leg(s) closed.",
                             severity="info", strategy_id=sid)
        else:
            write_audit_log(f"[IC_EOD][{sid}] NEXT_OPEN mode, no "
                            f"expiring-today legs — legs (if any) will carry "
                            f"(ONE_NIGHT_MAX)")
    except Exception as e:
        write_audit_log(f"[IC_EOD][{sid}][ERROR] {repr(e)}")


def ic_live_eod_job(*, sleep_fn=time.sleep, now_fn=_now):
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log("[IC_EOD] non-trading day — no-op")
        return
    """15:25 IST cron. sleep_fn / now_fn injectable for tests."""
    try:
        insts = _instances()
        if not insts:
            write_audit_log("[IC_EOD] no IC runtime initialized — nothing to do")
            return

        pending = {sid: (gm, cfg, _hm_deadline(_eod_deadline(cfg), now_fn))
                   for sid, gm, cfg in insts}

        waited = 0
        while pending and waited <= _MAX_WAIT_S:
            for sid in list(pending.keys()):
                gm, cfg, deadline = pending[sid]
                if now_fn() >= deadline:
                    _eod_one(sid, gm, cfg, now_fn)
                    del pending[sid]
            if not pending:
                break
            sleep_fn(_WAIT_POLL_S)
            waited += _WAIT_POLL_S

        # cap hit with deadlines still ahead (clock skew / misconfig): act
        # NOW rather than silently skipping — closing early beats not closing.
        for sid, (gm, cfg, _deadline) in pending.items():
            write_audit_log(f"[IC_EOD][{sid}] wait cap hit before deadline — "
                            f"acting immediately")
            _eod_one(sid, gm, cfg, now_fn)

    except Exception as e:
        write_audit_log(f"[IC_EOD][ERROR] {repr(e)}")


def ic_morning_job(*, sleep_fn=time.sleep, now_fn=_now):
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log("[IC_MORNING] non-trading day — no-op")
        return
    """09:08 IST cron. Carry-morning scheduled primary (engine = backstop).
    Structurally a no-op for EOD-mode instances (they never carry)."""
    try:
        carriers = [(sid, gm, cfg) for sid, gm, cfg in _instances()
                    if gm.has_carried_open()]
        if not carriers:
            write_audit_log("[IC_MORNING] no carried legs on any instance "
                            "— no-op")
            return

        for sid, gm, cfg in carriers:
            try:
                next_open_hm = cfg.get("next_open_time", "09:16")

                # 1) pre-market GTT teardown (retry to the 09:15 boundary)
                boundary = _hm_deadline("09:15", now_fn)
                while now_fn() < boundary:
                    try:
                        if gm.premarket_cancel_gtts():
                            break
                    except Exception as e:
                        write_audit_log(f"[IC_MORNING][{sid}]"
                                        f"[PREMARKET_ERR] {e!r}")
                    sleep_fn(_WAIT_POLL_S)

                # 2) wait to next_open_time (first-candle rule: NO exits
                #    before it)
                deadline = _hm_deadline(next_open_hm, now_fn)
                waited = 0
                while now_fn() < deadline and waited < _MAX_WAIT_S:
                    sleep_fn(_WAIT_POLL_S)
                    waited += _WAIT_POLL_S

                # 3) morning square-off retry loop (bounded; engine keeps
                #    retrying)
                waited = 0
                done = False
                while waited < _MAX_WAIT_S:
                    remaining = gm.morning_square_off()
                    if remaining == 0:
                        write_audit_log(f"[IC_MORNING][{sid}] carry "
                                        f"square-off complete")
                        record_alert("EOD_SQUAREOFF",
                                     f"{sid}: overnight carry closed at "
                                     f"next-open.",
                                     severity="info", strategy_id=sid)
                        done = True
                        break
                    sleep_fn(_WAIT_POLL_S)
                    waited += _WAIT_POLL_S

                if not done:
                    write_audit_log(f"[IC_MORNING][{sid}] wait cap hit with "
                                    f"legs still open — engine loop continues "
                                    f"retrying")
                    record_alert("IC_MORNING_STUCK",
                                 f"{sid}: morning square-off incomplete at "
                                 f"job cap — engine continues retrying.",
                                 severity="error", strategy_id=sid)
            except Exception as e:
                write_audit_log(f"[IC_MORNING][{sid}][ERROR] {repr(e)}")

    except Exception as e:
        write_audit_log(f"[IC_MORNING][ERROR] {repr(e)}")