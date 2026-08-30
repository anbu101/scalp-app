# backend/app/jobs/vet_live_eod.py
#
# ── VET_V1 — EOD / expiry square-off safety-net job ──
# ============================================================================
# api_server cron (15:25, unique id vet_live_eod_squareoff). PRIMARY exit
# path is the coordinator's boundary check inside vet_selection_loop
# (expiry exit at 15:20, eod_square at exit_time on the candle stream — the
# parity path). THIS JOB is the safety net for the day the loop is dead:
#
#   * Manager reachable →
#       - eod_square ON (intraday): close any open position (double-close is
#         a no-op — close_position on a flat manager returns None).
#       - eod_square OFF (positional): a carry is BY DESIGN — deliberate
#         NO-OP, EXCEPT a position whose contract expires TODAY, which must
#         never survive: expiry_exit fires regardless of mode.
#   * Manager unreachable with rows still OPEN in vet_trades:
#       - intraday PAPER rows → STALE (honest — no invented exit prices);
#       - intraday LIVE rows → CRITICAL alert for manual square-off (this
#         job has no executor of its own and pretending otherwise would
#         fabricate fills);
#       - positional rows → legitimate carry, logged and left alone, EXCEPT
#         same-day-expiry rows which raise the CRITICAL alert.
#
# VET_V1 is in OVERNIGHT_EXEMPT_STRATEGIES, so the generic 15:25 paper sweep
# never touches vet_trades — this job is the ONLY 15:25 authority for VET.
# ============================================================================

import time
from datetime import datetime, timezone, timedelta

from app.event_bus.audit_logger import write_audit_log

IST = timezone(timedelta(hours=5, minutes=30))


def _alert(msg: str, severity: str = "error") -> None:
    try:
        from app.api.telegram_api import notify_system_alert
        notify_system_alert({"message": msg, "severity": severity})
    except Exception:
        pass


def vet_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron trigger is
    # already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        write_audit_log("[EOD][VET] non-trading day — no-op")
        return

    try:
        from app.config.strategy_loader import load_strategy_config
        cfg = load_strategy_config("VET_V1") or {}
    except Exception:
        cfg = {}
    eod_square = bool(cfg.get("eod_square", True))
    today_iso = datetime.now(IST).date().isoformat()

    try:
        from app.engine.vet.vet_selection_loop import get_manager
        m = get_manager()
    except Exception:
        m = None

    # ── layer 1: the live manager ──
    if m is not None:
        try:
            pos = m.pos
            if pos is None:
                write_audit_log("[VET_EOD] clean — no open position")
                return
            exp = str((pos.get("main") or {}).get("expiry") or "")[:10]
            if exp == today_iso:
                m.expiry_exit(int(time.time()))
                write_audit_log("[VET_EOD] same-day-expiry position closed "
                                "via manager")
            elif eod_square:
                m.eod_square_off(int(time.time()))
                write_audit_log("[VET_EOD] intraday square-off via manager")
            else:
                write_audit_log("[VET_EOD] positional carry — deliberate "
                                "no-op (by design)")
            return
        except Exception as e:
            write_audit_log(f"[VET_EOD] manager path FAILED: {e!r} — "
                            f"falling through to DB sweep")

    # ── layer 2: manager unreachable; be honest about what this job can do ──
    try:
        from app.engine.vet.vet_common import VetRepo
        repo = VetRepo()
        legs = repo.open_legs()
    except Exception as e:
        write_audit_log(f"[VET_EOD] repo unreachable: {e!r}")
        return
    if not legs:
        write_audit_log("[VET_EOD] clean — no open rows")
        return

    for leg in legs:
        exp = str(leg.get("expiry") or "")[:10]
        expiring = exp == today_iso
        live = str(leg.get("mode") or "PAPER").upper() == "LIVE"
        if not eod_square and not expiring:
            write_audit_log(f"[VET_EOD] carry left alone (positional): "
                            f"{leg['tradingsymbol']}")
            continue
        if live:
            _alert(f"VET_V1 CRITICAL: LIVE leg {leg['tradingsymbol']} open at "
                   f"15:25 with the loop dead"
                   + (" and it EXPIRES TODAY" if expiring else "")
                   + " — square off MANUALLY at the broker.", "error")
            write_audit_log(f"[VET_EOD] CRITICAL live leg open, loop dead: "
                            f"{leg['tradingsymbol']}")
        else:
            repo.mark_stale(leg["id"], "EOD sweep, loop dead")
            write_audit_log(f"[VET_EOD] paper leg {leg['tradingsymbol']} → "
                            f"STALE (no invented exit prices)")