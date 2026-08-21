# backend/app/jobs/eod_safety.py
#
# ── EOD_1515_FIX_20260821 BEGIN (new module) ────────────────────────────────
# Born 2026-08-19/20 incident: BB_V1/BB_V2/SCALP_V1 paper rows carried
# overnight two days running and were closed next morning by SL, not by EOD.
# Root causes fixed in this change set:
#   (1) scalp_live_eod_job was registered NOWHERE (lost in the D2 scheduler
#       relocation 2026-08-17) — re-registered at 15:15 in api_server.py.
#   (2) The whole EOD fleet depended on the app being awake at the exact cron
#       second (APScheduler default misfire_grace_time = 1s). A sleeping Mac
#       at 15:25 silently skipped EVERY EOD job with no alert and no retry —
#       api_server.py now sets misfire_grace_time=3600 + coalesce.
#   (3) Nothing at boot cleaned a prior-day OPEN paper row, so engines
#       resumed yesterday's position as live next morning (the 09:48 SL exit
#       symptom).
#
# This module supplies the two remaining layers:
#   * boot_close_stale_paper_rows()  — startup sweep, runs BEFORE strategy
#     launches: force-closes any OPEN non-exempt paper_trades row whose
#     entry_time is before today's IST midnight. exit_reason=STALE_EOD_SWEEP
#     so these rows are identifiable in the Trades screen and exports.
#     Exit price is best-effort (LTPStore is usually cold at boot →
#     entry-price fallback, gross P&L 0) — the goal is STATE HYGIENE: the
#     row should have died yesterday; it must not be resumed as a live
#     position today. A non-zero sweep also fires ONE Telegram CRITICAL,
#     because it means yesterday's EOD layer failed.
#   * eod_open_row_watchdog_job()    — 15:35 IST cron: if any OPEN
#     non-exempt paper row remains after all intraday EODs (15:15 primaries,
#     15:25 generic sweep, 15:26 TSG, 15:28 PST), it now SELF-HEALS
#     (EOD_OBS_20260821, after the 2026-08-21 BB_V2 carry): force-closes the
#     survivors as EOD_WATCHDOG_FORCECLOSE (paper rows only — DB state, no
#     broker calls), THEN screams (audit + bell CRITICAL + Telegram
#     CRITICAL) so the primary-job failure still gets investigated. NFO
#     trades to 15:40, so a subscribed symbol still gets a live LTP; a cold
#     one falls back to entry price — either beats an overnight carry.
#
# EXEMPTIONS: reuses OVERNIGHT_EXEMPT_STRATEGIES from
# app.db.paper_trade_squareoff (single source of truth: IC_V2 / TSG_V1 /
# TMA_V2 own an overnight or self-timed lifecycle and must never be swept
# or alerted on by generic code).
#
# FAIL DIRECTION: both entry points are EXIT-path utilities → never raise.
# A broken sweep must not block boot; a broken watchdog must not kill the
# scheduler thread.
#
# TZ: IST derived from UTC via pytz, matching app.utils.market_hours —
# host-timezone-safe, no DST (IST is fixed +5:30).
# ── EOD_1515_FIX_20260821 END (header) ──────────────────────────────────────

from datetime import datetime

import pytz

from app.event_bus.audit_logger import write_audit_log
from app.db.paper_trade_squareoff import OVERNIGHT_EXEMPT_STRATEGIES

IST = pytz.timezone("Asia/Kolkata")

EXIT_REASON_STALE = "STALE_EOD_SWEEP"
EXIT_REASON_WATCHDOG = "EOD_WATCHDOG_FORCECLOSE"  # ── EOD_OBS_20260821 ──


def _ist_midnight_epoch_today() -> int:
    """Epoch seconds of 00:00:00 IST today. Derived from UTC (host-tz safe)."""
    now_ist = datetime.now(pytz.utc).astimezone(IST)
    midnight = IST.localize(
        datetime(now_ist.year, now_ist.month, now_ist.day, 0, 0, 0)
    )
    return int(midnight.timestamp())


def _select_open_nonexempt_rows(conn, extra_where: str = "", params: tuple = ()):
    placeholders = ",".join("?" for _ in OVERNIGHT_EXEMPT_STRATEGIES)
    sql = f"""
        SELECT paper_trade_id, strategy_name, symbol, entry_price, entry_time
        FROM paper_trades
        WHERE state = 'OPEN'
          AND (strategy_name IS NULL
               OR strategy_name NOT IN ({placeholders}))
          {extra_where}
    """
    return conn.execute(
        sql, tuple(OVERNIGHT_EXEMPT_STRATEGIES) + params
    ).fetchall()


def _notify_critical_safe(message: str, strategy_id: str = "") -> None:
    """Telegram CRITICAL, lazily imported, never raises."""
    try:
        from app.api.telegram_api import notify_critical
        payload = {"severity": "critical", "message": message}
        if strategy_id:
            payload["strategy_id"] = strategy_id
        notify_critical(payload)
    except Exception as e:
        write_audit_log(f"[EOD_SAFETY][TELEGRAM][WARN] notify failed: {e!r}")


def _force_close_rows(rows, *, exit_reason: str, ctx: str):
    """Close the given OPEN paper rows at LTPStore-or-entry price.
    Shared by the boot sweep and the 15:35 watchdog. Returns
    (closed_count, per_strategy_counts). Never raises past a row."""
    from app.db.paper_trades_repo import close_paper_trade
    try:
        from app.marketdata.ltp_store import LTPStore
    except Exception:
        LTPStore = None

    closed = 0
    by_strategy = {}
    for r in rows:
        trade_id = r["paper_trade_id"]
        symbol = r["symbol"]
        strat = r["strategy_name"] or "UNKNOWN"
        entry = r["entry_price"]

        ltp = None
        if LTPStore is not None:
            try:
                ltp = LTPStore.get(symbol)
            except Exception:
                ltp = None
        if not ltp or ltp <= 0:
            ltp = entry
            write_audit_log(
                f"[EOD_SAFETY][{ctx}][WARN] {symbol}: no LTP — closing at "
                f"entry_price={entry} (gross P&L 0)"
            )

        try:
            close_paper_trade(
                paper_trade_id=trade_id,
                exit_price=float(ltp),
                exit_reason=exit_reason,
            )
            closed += 1
            by_strategy[strat] = by_strategy.get(strat, 0) + 1
            write_audit_log(
                f"[EOD_SAFETY][{ctx}] CLOSED {strat} {symbol} "
                f"trade_id={trade_id} @ {ltp} reason={exit_reason}"
            )
        except Exception as e:
            write_audit_log(
                f"[EOD_SAFETY][{ctx}][ERROR] close failed "
                f"trade_id={trade_id} ERR={e!r}"
            )
    return closed, by_strategy


# ============================================================
# LAYER A — BOOT STALE-ROW SWEEP
# ============================================================

def boot_close_stale_paper_rows() -> int:
    """
    Close every OPEN non-exempt paper row entered BEFORE today (IST).
    Called at startup from api_server (inside its own _boot_guard) BEFORE
    any strategy launch, so an engine can never resume a prior-day row.
    Idempotent; never raises. Returns rows closed.
    """
    try:
        from app.db.sqlite import get_conn

        midnight = _ist_midnight_epoch_today()
        conn = get_conn()
        rows = _select_open_nonexempt_rows(
            conn, extra_where="AND entry_time < ?", params=(midnight,)
        )

        if not rows:
            write_audit_log("[EOD_SAFETY][BOOT] No stale open paper rows — clean")
            return 0

        write_audit_log(
            f"[EOD_SAFETY][BOOT] {len(rows)} STALE open paper row(s) from a "
            f"prior session — yesterday's EOD layer failed; force-closing "
            f"as {EXIT_REASON_STALE} before strategy launches"
        )

        # LTPStore is almost certainly cold at boot -> entry-price
        # fallback inside the shared close helper.
        closed, by_strategy = _force_close_rows(
            rows, exit_reason=EXIT_REASON_STALE, ctx="BOOT"
        )

        if closed:
            detail = ", ".join(f"{k}×{v}" for k, v in sorted(by_strategy.items()))
            msg = (
                f"Boot sweep closed {closed} STALE overnight paper row(s) "
                f"({detail}). Yesterday's EOD square-off did NOT run — check "
                f"whether the app was awake at EOD and /boot-status for a "
                f"scheduler-phase failure."
            )
            _notify_critical_safe(msg)
            try:
                from app.event_bus.inapp_events import record_alert
                record_alert(
                    "STALE_EOD_SWEEP", msg, severity="critical", mode="paper"
                )
            except Exception:
                pass

        write_audit_log(f"[EOD_SAFETY][BOOT] Stale sweep done — closed={closed}")
        return closed

    except Exception as e:
        # Exit-path utility: never block boot.
        write_audit_log(f"[EOD_SAFETY][BOOT][ERROR] sweep failed: {e!r}")
        return 0


# ============================================================
# LAYER B — 15:35 OPEN-ROW WATCHDOG
# ============================================================

def eod_open_row_watchdog_job() -> None:
    """
    15:35 IST cron. All intraday EOD layers have had their chance
    (15:15 primaries, 15:25 generic sweep, 15:26 TSG, 15:28 PST).
    Any surviving OPEN non-exempt paper row = an EOD failure TODAY —
    alert loudly while it is still fixable during market hours
    (NFO trades to 15:40 under CAS). Never raises.
    """
    try:
        # ── TRADING_DAY_GATE_20260816 pattern ── holiday/weekend no-op.
        from app.utils.market_hours import is_trading_day
        if not is_trading_day():
            write_audit_log("[EOD_SAFETY][WATCHDOG] non-trading day — no-op")
            return

        from app.db.sqlite import get_conn
        conn = get_conn()
        rows = _select_open_nonexempt_rows(conn)

        if not rows:
            write_audit_log("[EOD_SAFETY][WATCHDOG] 15:35 check clean — "
                            "no open non-exempt paper rows")
            return

        write_audit_log(
            f"[EOD_SAFETY][WATCHDOG] {len(rows)} paper row(s) survived to "
            f"15:35 — primary EOD layer failed; force-closing now "
            f"(EOD_OBS_20260821 self-heal)"
        )
        closed, by_strategy = _force_close_rows(
            rows, exit_reason=EXIT_REASON_WATCHDOG, ctx="WATCHDOG"
        )
        detail = ", ".join(f"{k}×{v}" for k, v in sorted(by_strategy.items()))

        msg = (
            f"EOD WATCHDOG: {len(rows)} paper row(s) still OPEN at 15:35 "
            f"({detail}) — an EOD square-off layer failed TODAY. "
            f"Force-closed {closed} as EOD_WATCHDOG_FORCECLOSE; "
            f"{len(rows) - closed} could not be closed. Check the [APS] "
            f"lines around 15:15/15:25 for which job failed and why."
        )
        write_audit_log(f"[EOD_SAFETY][WATCHDOG][CRITICAL] {msg}")
        try:
            from app.event_bus.inapp_events import record_alert
            record_alert("EOD_WATCHDOG", msg, severity="critical", mode="paper")
        except Exception:
            pass
        _notify_critical_safe(msg)

    except Exception as e:
        write_audit_log(f"[EOD_SAFETY][WATCHDOG][ERROR] {e!r}")