# backend/app/jobs/scalpv5_live_eod.py
#
# SCALP_V5 — End-of-day square-off job.
# ============================================================================
# Scheduled at 15:25 IST (same cron slot as the other strategies). Squares off
# any OPEN V5 trade (paper + live) via the manager's close_trade(EOD) path, then
# resets the V5-local MTM re-entry latch so the next session starts clean.
#
# Isolated: touches only the V5 selection-loop singleton (for the manager) and
# the V5 risk latch. No other strategy is affected. If V5 never ran today, the
# manager is None and this is a no-op.
# ============================================================================

from app.event_bus.audit_logger import write_audit_log


def scalpv5_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        write_audit_log("[EOD][SCALP_V5] non-trading day — no-op")
        return
    try:
        from app.engine.scalpv5.scalpv5_selection_loop import get_manager
        manager = get_manager()
    except Exception as e:
        write_audit_log(f"[V5][EOD][ERROR] could not resolve manager: {e!r}")
        manager = None

    if manager is None:
        write_audit_log("[V5][EOD] manager not available (V5 not running) — nothing to square off")
    else:
        try:
            closed = manager.eod_squareoff()
            write_audit_log(f"[V5][EOD] square-off complete — closed {closed} trade(s)")
        except Exception as e:
            write_audit_log(f"[V5][EOD][ERROR] square-off failed: {e!r}")

    # Reset the V5-local MTM re-entry latch for the next session (always; safe
    # even if the manager was unavailable).
    try:
        from app.engine.scalpv5.scalpv5_manager import reset_v5_risk_latch
        reset_v5_risk_latch()
    except Exception as e:
        write_audit_log(f"[V5][EOD][ERROR] risk-latch reset failed: {e!r}")


# ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ──────────────────────────────────────
# Config-driven square-off time. The 15:25 cron above stays registered as an
# untouched BACKSTOP; this watchdog runs every minute 15:00–15:29 and fires
# the same job the first minute at/after the configured time. Belt AND braces
# is deliberate for a square-off, and scalpv5_live_eod_job is idempotent — a
# second call simply finds nothing open.
_V5_EOD_WATCHDOG_DEFAULT = "15:25"      # == the legacy cron slot
_V5_EOD_WINDOW = (15 * 60, 15 * 60 + 29)   # minutes-from-midnight IST
_v5_eod_fired_on = {"day": None}           # per-day latch: fire ONCE


def _v5_eod_target_minute() -> int:
    """Configured square-off as minutes-from-IST-midnight, clamped to the
    watchdog window. Anything missing/unparseable/out-of-window falls back to
    the legacy 15:25 slot (audited) — a typo must never park the square-off
    outside market hours, and must never disable it."""
    raw = _V5_EOD_WATCHDOG_DEFAULT
    try:
        from app.config.strategy_loader import load_strategy_config
        raw = str((load_strategy_config("SCALP_V5") or {}).get(
            "eod_squareoff_time", _V5_EOD_WATCHDOG_DEFAULT) or
            _V5_EOD_WATCHDOG_DEFAULT).strip()
    except Exception as e:
        write_audit_log(f"[V5][EOD][WATCHDOG] config read failed ({e!r}) — "
                        f"using {_V5_EOD_WATCHDOG_DEFAULT}")
    try:
        hh, mm = raw.split(":")
        mins = int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        write_audit_log(f"[V5][EOD][WATCHDOG] unparseable eod_squareoff_time "
                        f"{raw!r} — using {_V5_EOD_WATCHDOG_DEFAULT}")
        return 15 * 60 + 25
    if not (_V5_EOD_WINDOW[0] <= mins <= _V5_EOD_WINDOW[1]):
        write_audit_log(f"[V5][EOD][WATCHDOG] eod_squareoff_time {raw!r} is "
                        f"outside the 15:00–15:29 watchdog window — using "
                        f"{_V5_EOD_WATCHDOG_DEFAULT}")
        return 15 * 60 + 25
    return mins


def scalpv5_eod_tick():
    """Per-minute 15:00–15:29 watchdog. No-op until the configured minute."""
    from datetime import datetime, timedelta, timezone
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        return
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    today = now.date().isoformat()
    if _v5_eod_fired_on["day"] == today:
        return
    if (now.hour * 60 + now.minute) < _v5_eod_target_minute():
        return
    _v5_eod_fired_on["day"] = today          # latch BEFORE running: a raising
    write_audit_log(                         # job must not re-fire every minute
        f"[V5][EOD][WATCHDOG] firing square-off at {now:%H:%M} IST "
        f"(configured {_v5_eod_target_minute() // 60:02d}:"
        f"{_v5_eod_target_minute() % 60:02d})")
    scalpv5_live_eod_job()