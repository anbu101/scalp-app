# backend/app/jobs/brk_live_eod.py
#
# ── BRK_V1 — EOD square-off safety-net job ──
# ============================================================================
# Fence BRK_V1_LIVE_20260902. api_server cron 15:16 IST, unique id
# "brk_live_eod_squareoff". PRIMARY exit is the engine's own 15:15 EOD tick
# (the parity path); THIS JOB is the safety net for the day the loop is dead.
#
# LD6: BRK's EOD (15:15) precedes the generic 15:25 paper sweep, so BRK is
# deliberately NOT in OVERNIGHT_EXEMPT_STRATEGIES — for PAPER rows the sweep
# is the second backstop and closes anything both this job and the engine
# missed. LIVE rows are what this job really protects:
#
#   * Manager reachable → eod_squareoff() (double-close is a no-op; the row
#     close path logs a loud SKIP on a non-OPEN row).
#   * Manager unreachable with OPEN LIVE rows → CRITICAL alert for manual
#     square-off — this job has no executor of its own and inventing fills
#     would corrupt the record. OPEN PAPER rows are left for the 15:25
#     generic sweep (honest, and exactly what LD6 designed).
# ============================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.event_bus.audit_logger import write_audit_log

IST = timezone(timedelta(minutes=330))


def _alert(msg: str, severity: str = "error") -> None:
    try:
        from app.api.telegram_api import notify_system_alert
        notify_system_alert({"message": msg, "severity": severity})
    except Exception:
        pass
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert(source="BRK_V1", code="EOD_JOB", message=msg,
                     severity=severity)
    except Exception:
        pass


def brk_live_eod_job() -> None:
    write_audit_log("[BRK][EOD_JOB] 15:16 safety net firing")
    gm = None
    try:
        from app.engine.brk.brk_runtime import get_brk_manager
        gm = get_brk_manager()
    except Exception as e:
        write_audit_log(f"[BRK][EOD_JOB] runtime import failed: {e!r}")
    if gm is not None:
        try:
            n = gm.eod_squareoff()
            write_audit_log(f"[BRK][EOD_JOB] manager squareoff closed={n}")
            return
        except Exception as e:
            write_audit_log(f"[BRK][EOD_JOB] manager squareoff raised: {e!r}")
    # Manager unreachable — check for LIVE rows the engine may have orphaned.
    try:
        from app.db.sqlite import get_conn
        day0 = int(datetime.now(IST).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
        conn = get_conn()
        rows = conn.execute(
            "SELECT paper_trade_id, symbol, trade_mode FROM paper_trades "
            "WHERE strategy_name='BRK_V1' AND state='OPEN' "
            "AND candle_ts >= ?", (day0,)).fetchall()
        live = [dict(r) for r in rows if dict(r)["trade_mode"] == "LIVE"]
        if live:
            syms = ", ".join(r["symbol"] for r in live)
            _alert(f"BRK_V1 EOD: runtime unreachable with OPEN LIVE "
                   f"position(s): {syms} — MANUAL SQUARE-OFF REQUIRED "
                   f"(this job cannot place orders)", "critical")
        elif rows:
            write_audit_log(f"[BRK][EOD_JOB] {len(rows)} OPEN paper row(s) "
                            f"left for the 15:25 generic sweep (LD6)")
    except Exception as e:
        write_audit_log(f"[BRK][EOD_JOB] orphan check failed: {e!r}")