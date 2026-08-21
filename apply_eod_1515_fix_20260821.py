#!/usr/bin/env python3
# apply_eod_1515_fix_20260821.py
#
# ── EOD_1515_FIX_20260821 ── Assert-anchored edit script.
# Run from repo root:  python3 apply_eod_1515_fix_20260821.py
#
# Edits (backend/app tree only — desktop tree is rsynced by build-scalp.sh):
#   api_server.py
#     E1  import scalp_live_eod_job + eod_safety entry points
#     E2  BackgroundScheduler: job_defaults misfire_grace_time=3600, coalesce
#     E3  register scalp_live_eod_job @ 15:15 + watchdog @ 15:35
#     E4  BB_V1 cron 15:25 → 15:15
#     E5  BB_V2 cron 15:25 → 15:15
#     E6  boot stale-row sweep before the scheduler phase
#   jobs/scalp_live_eod.py
#     E7  trading-day gate + bell only when something actually closed
#
# Every anchor must appear EXACTLY ONCE or the script aborts with no writes.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = ROOT / "backend" / "app" / "api_server.py"
SCALP = ROOT / "backend" / "app" / "jobs" / "scalp_live_eod.py"

EDITS_API = []
EDITS_SCALP = []


def edit(bucket, label, old, new):
    bucket.append((label, old, new))


# ───────────────────────── api_server.py ─────────────────────────

edit(EDITS_API, "E1 imports",
"""from app.jobs.paper_trade_eod import paper_trade_eod_job""",
"""from app.jobs.paper_trade_eod import paper_trade_eod_job
# ── EOD_1515_FIX_20260821 ── scalp_live_eod_job existed but was registered
# NOWHERE (lost in the D2 scheduler move 2026-08-17); eod_safety adds the
# boot stale-row sweep + 15:35 open-row watchdog.
from app.jobs.scalp_live_eod import scalp_live_eod_job
from app.jobs.eod_safety import (
    boot_close_stale_paper_rows,
    eod_open_row_watchdog_job,
)
# ── EOD_1515_FIX_20260821 END (imports) ──""")

edit(EDITS_API, "E2 job_defaults",
"""        scheduler = BackgroundScheduler(timezone="Asia/Kolkata")""",
"""        # ── EOD_1515_FIX_20260821 ── APScheduler's default
        # misfire_grace_time is 1 SECOND: a sleeping Mac / closed app at the
        # cron second silently discarded the entire EOD fleet for the day
        # (2026-08-19/20 overnight-carry incident). One hour of grace +
        # coalesce means a wake-up at e.g. 15:41 still runs each missed EOD
        # exactly once; every close path is idempotent (WHERE state='OPEN' /
        # exit_time IS NULL) and trading-day gated, so a late run is safe.
        scheduler = BackgroundScheduler(
            timezone="Asia/Kolkata",
            job_defaults={"misfire_grace_time": 3600, "coalesce": True},
        )""")

edit(EDITS_API, "E3 scalp + watchdog registration",
"""        scheduler.add_job(
            paper_trade_eod_job, trigger="cron", hour=15, minute=25,
            id="paper_trade_eod_squareoff", replace_existing=True,
        )""",
"""        scheduler.add_job(
            paper_trade_eod_job, trigger="cron", hour=15, minute=25,
            id="paper_trade_eod_squareoff", replace_existing=True,
        )
        # ── EOD_1515_FIX_20260821 BEGIN ──
        # SCALP_V1 EOD re-registered (its docstring always claimed this cron
        # existed; it was lost in the D2 move). 15:15 per CAS doctrine: the
        # index stops continuous trading at 15:15, so spot-signal intraday
        # strategies must be flat by then. Handles paper AND live internally.
        scheduler.add_job(
            scalp_live_eod_job, trigger="cron", hour=15, minute=15,
            id="scalp_v1_live_eod_squareoff", replace_existing=True,
        )
        # Watchdog AFTER every intraday EOD layer (15:15 primaries, 15:25
        # sweep, 15:26 TSG, 15:28 PST): any surviving OPEN non-exempt paper
        # row → bell + Telegram CRITICAL while NFO still trades (to 15:40),
        # so a failed EOD is fixable same-day instead of carrying overnight.
        scheduler.add_job(
            eod_open_row_watchdog_job, trigger="cron", hour=15, minute=35,
            id="eod_open_row_watchdog", replace_existing=True,
        )
        # ── EOD_1515_FIX_20260821 END ──""")

edit(EDITS_API, "E4 BB_V1 cron 15:15",
"""            bb_live_eod_job, trigger="cron", hour=15, minute=25,
            id="bb_live_eod_squareoff", replace_existing=True,""",
"""            # ── EOD_1515_FIX_20260821 ── 15:25 → 15:15. The BB engine has
            # no internal EOD clock (auto_square_off_time is stored but read
            # by no backend code), so THIS cron is BB's primary exit — it now
            # fires at the time the UI has always promised, and by 15:15 per
            # CAS doctrine (index frozen after 15:15).
            bb_live_eod_job, trigger="cron", hour=15, minute=15,
            id="bb_live_eod_squareoff", replace_existing=True,""")

edit(EDITS_API, "E5 BB_V2 cron 15:15",
"""            bb_live_eod_v2_job, trigger="cron", hour=15, minute=25,
            id="bb_v2_live_eod_squareoff", replace_existing=True,""",
"""            # ── EOD_1515_FIX_20260821 ── 15:25 → 15:15 (same rationale as
            # BB_V1: cron IS the primary exit; CAS doctrine).
            bb_live_eod_v2_job, trigger="cron", hour=15, minute=15,
            id="bb_v2_live_eod_squareoff", replace_existing=True,""")

edit(EDITS_API, "E6 boot stale sweep",
"""    app.state.startup_phase = "scheduler"
    with _boot_guard("scheduler"):""",
"""    # ── EOD_1515_FIX_20260821 BEGIN ── stale-row sweep BEFORE anything
    # else in this phase and before every strategy launch: a prior-day OPEN
    # paper row must be closed as STALE_EOD_SWEEP before an engine can
    # resume it as a live position (the 2026-08-21 09:48 SL-on-yesterday's-
    # row symptom). Own guard: a sweep failure must not take the scheduler
    # down with it.
    app.state.startup_phase = "stale_paper_sweep"
    with _boot_guard("stale_paper_sweep"):
        boot_close_stale_paper_rows()
    # ── EOD_1515_FIX_20260821 END ──
    app.state.startup_phase = "scheduler"
    with _boot_guard("scheduler"):""")

# ─────────────────────── jobs/scalp_live_eod.py ───────────────────────

edit(EDITS_SCALP, "E7a trading-day gate",
"""def scalp_live_eod_job():
    write_audit_log("[EOD][SCALP] Square-off triggered")""",
"""def scalp_live_eod_job():
    # ── EOD_1515_FIX_20260821 ── TRADING_DAY_GATE pattern: this job was
    # the ONLY EOD job without the holiday/weekend guard (it predated
    # TRADING_DAY_GATE_20260816 and was unregistered when that fix landed).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        write_audit_log("[EOD][SCALP] non-trading day — no-op")
        return
    write_audit_log("[EOD][SCALP] Square-off triggered")""")

edit(EDITS_SCALP, "E7b bell only on real closes",
"""    # ── In-app bell alert ────────────────────────────────────────
    record_alert(
        "EOD_SQUAREOFF",
        f"SCALP_V1: end-of-day square-off complete — {total_closed} position(s) closed.",
        severity="info",
        strategy_id=STRATEGY_ID,
    )""",
"""    # ── In-app bell alert ────────────────────────────────────────
    # ── EOD_1515_FIX_20260821 ── CAS_NOTIF discipline (mirrors the BB
    # jobs): no bell when there was nothing to close — a daily 15:15 bell
    # with "0 closed" reads as a late close and trains alert-blindness.
    if total_closed > 0:
        record_alert(
            "EOD_SQUAREOFF",
            f"SCALP_V1: end-of-day square-off complete — {total_closed} position(s) closed.",
            severity="info",
            strategy_id=STRATEGY_ID,
        )""")


def apply(path: Path, edits):
    src = path.read_text(encoding="utf-8")
    # Pass 1: every anchor unique BEFORE any write.
    for label, old, _ in edits:
        n = src.count(old)
        if n != 1:
            print(f"ABORT [{path.name}] anchor for {label!r} matched {n}× "
                  f"(need exactly 1). NO FILES WRITTEN.")
            sys.exit(1)
    # Pass 2: apply.
    for label, old, new in edits:
        src = src.replace(old, new, 1)
        print(f"  applied {label} → {path.name}")
    path.write_text(src, encoding="utf-8")


def main():
    for p in (API, SCALP):
        if not p.exists():
            print(f"ABORT: {p} not found — run from repo root.")
            sys.exit(1)
    apply(API, EDITS_API)
    apply(SCALP, EDITS_SCALP)
    # Post-verification: marker + registration counts.
    api_txt = API.read_text(encoding="utf-8")
    checks = [
        ("scalp_v1_live_eod_squareoff", 1),
        ("eod_open_row_watchdog", 3),   # import + callable + job id
        ("misfire_grace_time", 2),      # comment + kwarg
        ("boot_close_stale_paper_rows()", 1),
        ('bb_live_eod_job, trigger="cron", hour=15, minute=15', 1),
        ('bb_live_eod_v2_job, trigger="cron", hour=15, minute=15', 1),
        ('minute=25,\n            id="bb_live_eod_squareoff"', 0),
    ]
    ok = True
    for needle, want in checks:
        got = api_txt.count(needle)
        status = "OK " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{status}] api_server.py contains {needle!r} ×{got} (want {want})")
    scalp_txt = SCALP.read_text(encoding="utf-8")
    got = scalp_txt.count("is_trading_day")
    print(f"  [{'OK ' if got >= 1 else 'FAIL'}] scalp_live_eod.py trading-day gate ×{got}")
    if not ok:
        print("POST-CHECK FAILED — inspect diffs before building.")
        sys.exit(1)
    print("ALL EDITS APPLIED + VERIFIED. Copy eod_safety.py into "
          "backend/app/jobs/ before building.")


if __name__ == "__main__":
    main()
