#!/usr/bin/env python3
# apply_boot_isolation_20260817.py
#
# ── BOOT_ISOLATION_20260817 ──────────────────────────────────────────────
# Replaces _run_heavy_startup() in api_server.py with a phase-isolated
# version. Splices between two unique anchors; writes nothing unless every
# anchor and every post-check passes, in every tree.
#
# WHY
#   2026-08-16/17: `gc_v1_runtime(broker_manager)` raised NameError inside
#   the unguarded launch block. The single outer try/except caught it and
#   re-raised, so startup DIED mid-way. Everything after that line never ran:
#   GC_V1, TMA_V1, BrokerReconciliationJob, and ALL 13 EOD/morning crons.
#   Silent for a full trading session; surfaced only as a stranded position.
#
# WHAT CHANGES
#   D1  Every strategy launch runs inside its own guard. One failure is
#       logged, alerted and recorded — the remaining launches still run.
#   D2  The scheduler is registered BEFORE the standalone launches, and in
#       its own guard. EOD/morning crons are the last-resort safety net and
#       must never be collateral damage from a strategy launch.
#   D3  Any phase failure raises a Telegram CRITICAL immediately and is
#       recorded in app.state.startup_failures, exposed at GET /boot-status.
#       Startup no longer dies silently at 00:41 waiting to be noticed at
#       17:30.
#
# WHAT DOES NOT CHANGE
#   Every launch call, every gate condition, every scheduler job id, trigger
#   and time. Post-checks assert all 13 job ids and all launch calls survive.
# ─────────────────────────────────────────────────────────────────────────

import ast
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
REL = Path("app/api_server.py")
TREES = [REPO / "backend", REPO / "desktop/src-tauri/backend"]

START = "async def _run_heavy_startup():"
END = ("# --------------------------------------------------\n"
       "# STARTUP  (fast path — only what must finish before serving)\n"
       "# --------------------------------------------------")

NEW_BLOCK = '''# ── BOOT_ISOLATION_20260817 BEGIN ────────────────────────────────────────
# Phase-isolated startup. Rationale in full at the top of
# apply_boot_isolation_20260817.py; short version: on 2026-08-16/17 a single
# NameError in the GC_V1 launch aborted _run_heavy_startup() and took every
# later launch AND all 13 EOD/morning crons down with it, silently, for a
# whole trading session.
#
# INVARIANT: no single strategy launch can prevent another launch, the
# scheduler, or startup completion. Failures are loud, not fatal.

def _boot_alert(label: str, err: BaseException) -> None:
    """
    Telegram CRITICAL for a startup phase failure. Infrastructure alert —
    deliberately NOT gated on any per-strategy notification toggle, mirroring
    services/disk_guard.py. Never raises: a Telegram outage must not turn a
    recoverable boot failure into a fatal one.
    """
    try:
        from app.api import telegram_api
        cfg = telegram_api.TELEGRAM_CONFIG or {}
        bot_token = (cfg.get("bot_token") or "").strip()
        if not bot_token:
            return
        msg = (f"\\U0001F6A8 SCALP BOOT FAILURE\\n\\n"
               f"Phase: {label}\\n"
               f"Error: {type(err).__name__}: {err}\\n\\n"
               f"That component did NOT start. Other components continued. "
               f"Check GET /boot-status.")
        for ch in (cfg.get("channels") or []):
            try:
                if not ch.get("enabled"):
                    continue
                chat_id = (ch.get("chat_id") or "").strip()
                if not chat_id:
                    continue
                telegram_api.send_telegram_message(bot_token, chat_id, msg)
            except Exception as e:
                write_audit_log(f"[BOOT_GUARD][TG_CH_ERR] {e}")
    except Exception as e:
        write_audit_log(f"[BOOT_GUARD][TG_ERR] {e}")


@contextmanager
def _boot_guard(label: str):
    """
    Isolate one startup phase. Logs + alerts + records on failure, then lets
    startup continue. app.state.startup_failures is the machine-readable
    record; /boot-status serves it.
    """
    try:
        yield
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        write_audit_log(f"[SYSTEM][BOOT_FAIL] {label} — {detail}")
        write_audit_log(f"[SYSTEM][BOOT_FAIL][TRACE] {label} — "
                        f"{traceback.format_exc()}")
        try:
            app.state.startup_failures.append({"phase": label,
                                               "error": detail})
        except Exception:
            pass
        _boot_alert(label, e)


async def _run_heavy_startup():
    import time
    _t = time.time()

    def lap(label):
        nonlocal _t
        now = time.time()
        write_audit_log(f"[BOOT-TIMING] {label}: {now - _t:.1f}s")
        _t = now

    app.state.startup_failures = []

    # --------------------------------------------------
    # HOUSEKEEPING
    # --------------------------------------------------
    app.state.startup_phase = "housekeeping"
    with _boot_guard("log_housekeeping"):
        run_log_housekeeping()
        write_audit_log("[SYSTEM] Log housekeeping completed")
        lap("log_housekeeping")

    with _boot_guard("db_housekeeping"):
        run_housekeeping()
        asyncio.create_task(housekeeping_loop())
        write_audit_log("[SYSTEM] DB housekeeping started")
        lap("db_housekeeping")

    with _boot_guard("state_dir"):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        write_audit_log(f"[SYSTEM] State dir = {STATE_DIR}")

    # --------------------------------------------------
    # STRATEGY INIT  (unchanged order/logic + PHASE 2 license gate)
    # --------------------------------------------------
    app.state.startup_phase = "strategies"
    from app.strategy.strategy_registry import STRATEGIES

    for strategy_id, cfg in STRATEGIES.items():

        if not cfg.get("enabled", False):
            write_audit_log(f"[SYSTEM] Strategy {strategy_id} disabled — skipping")
            continue

        # PHASE 2 LICENSE GATE: ADMIN entitlements are ["*"] so this is
        # always True for admin builds — provably identical behavior.
        if not license_state.license_allows_strategy(strategy_id):
            write_audit_log(
                f"[LICENSE] Strategy {strategy_id} not licensed — skipping"
            )
            continue

        if strategy_id == "SCALP_V3":
            write_audit_log(
                "[SYSTEM] SCALP_V3 deferred — launched via standalone selection loop"
            )
            continue

        if strategy_id == "SCALP_V5":
            write_audit_log(
                "[SYSTEM] SCALP_V5 deferred — launched via standalone selection loop"
            )
            continue

        if strategy_id == "GC_V1":
            write_audit_log(
                "[SYSTEM] GC_V1 deferred — launched via standalone runtime"
            )
            continue
        if strategy_id == "TSG_V1":
            write_audit_log(
                "[SYSTEM] TSG_V1 deferred — launched via standalone runtime"
            )
            continue
        if strategy_id in ("IC_V1", "IC_V2"):   # ── IC_SPLIT ──
            write_audit_log(
                f"[SYSTEM] {strategy_id} deferred — launched via "
                f"standalone runtime"
            )
            continue

        # D1: per-strategy isolation. A bad slot/executor for one strategy
        # no longer prevents the others from initialising.
        with _boot_guard(f"strategy_init {strategy_id}"):
            write_audit_log(f"[SYSTEM] Initializing strategy {strategy_id}")

            strategy_executor = get_executor_for_broker(cfg["broker"])

            for slot_name in cfg.get("slots", []):
                TradeStateManager(
                    strategy_id=strategy_id,
                    name=slot_name,
                    executor=strategy_executor,
                    state_file=STATE_DIR / f"{strategy_id}_{slot_name}.json",
                    price_provider=None,
                )

            StrategyRuntimeManager.start(strategy_id, zerodha_manager)
            write_audit_log(f"[SYSTEM] Strategy {strategy_id} runtime started")
            lap(f"strategy {strategy_id}")

    # --------------------------------------------------
    # TRADE RECOVERY
    # --------------------------------------------------
    app.state.startup_phase = "recovery"
    with _boot_guard("recover_trades"):
        recover_trades_from_zerodha()
        lap("recover_trades")

    # --------------------------------------------------
    # ZERODHA INSTRUMENTS + INDEX STATE + PIVOTS
    # --------------------------------------------------
    with _boot_guard("instruments_and_index_state"):
        if zerodha_manager.is_trade_ready():
            kite = (
                zerodha_manager.get_data_kite()
                or zerodha_manager.get_trade_kite()
            )
            if kite:
                app.state.startup_phase = "instruments"
                ensure_instruments_dump(kite.api_key, kite.access_token)
                # Fix 1: capture a DATED snapshot of today's NFO master so future
                # backtests can reconstruct the correct per-day weekly expiry and
                # backfill expired weeklies (whose tokens Kite flushes at expiry).
                try:
                    snapshot_instruments_for_today(kite)
                except Exception as _e:
                    write_audit_log(f"[INSTR_SNAPSHOT][WARN] startup snapshot failed: {_e!r}")
                lap("instruments")

                load_index_prev_close_once(kite)
                seed_index_ltp_once(kite)
                lap("index_state")

                PivotCache.initialize(kite)
                write_audit_log("[PIVOT] PivotCache initialized")
                lap("pivot_cache")

                write_audit_log("[ZERODHA] Instruments + index state loaded")

    # ── INDEX_PREVCLOSE_ROLLOVER BEGIN (watchdog launch) ──
    # Started OUTSIDE the is_trade_ready() gate on purpose:
    #   (a) rolls prev_close over the midnight boundary so a backend
    #       left running overnight never serves a stale reference
    #       (2026-08-13 bug: BANKNIFTY change sign flipped on dash);
    #   (b) self-heals a startup where trade wasn't ready and the
    #       gated loader above never ran — watchdog populates
    #       prev_close once the morning login lands.
    with _boot_guard("index_prev_close_watchdog"):
        asyncio.create_task(index_prev_close_watchdog(zerodha_manager))
        write_audit_log("[SYSTEM] Index prev_close rollover watchdog launched")
    # ── INDEX_PREVCLOSE_ROLLOVER END (watchdog launch) ──

    # --------------------------------------------------
    # SCHEDULER
    # --------------------------------------------------
    # D2: MOVED AHEAD OF THE STANDALONE LAUNCHES (2026-08-17). These crons
    # are the last-resort EOD/morning safety net for every strategy. They
    # previously sat behind ~8 unguarded launch statements, so one typo in a
    # launch line silently deregistered all 13. Registration order has no
    # behavioural effect — cron triggers fire on wall clock — so registering
    # first is strictly safer.
    app.state.startup_phase = "scheduler"
    with _boot_guard("scheduler"):
        scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

        scheduler.add_job(
            paper_trade_eod_job, trigger="cron", hour=15, minute=25,
            id="paper_trade_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            bb_live_eod_job, trigger="cron", hour=15, minute=25,
            id="bb_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            bb_live_eod_v2_job, trigger="cron", hour=15, minute=25,
            id="bb_v2_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            ha_live_eod_job, trigger="cron", hour=15, minute=25,
            id="ha_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            scalp_v3_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalp_v3_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            pst_live_eod_job, trigger="cron", hour=15, minute=28,
            id="pst_live_eod_check", replace_existing=True,
        )
        # ── SCALP_V5 BEGIN ──
        scheduler.add_job(
            scalpv5_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalpv5_live_eod_squareoff", replace_existing=True,
        )
        # ── SCALP_V5 END ──
        # ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
        # ONE EOD job serves BOTH IC instances: fires 15:25, iterates the
        # IC_REGISTRY and waits internally per instance to expiry_exit_time
        # (NEXT_OPEN mode: closes ONLY today-entered expiring legs, DA5) or
        # exit_time (legacy EOD mode: full square-off). Misfire acts
        # immediately. Second layer: each ICEngine's own continuous
        # session-end backstop.
        scheduler.add_job(
            ic_live_eod_job, trigger="cron", hour=15, minute=25,
            id="ic_live_eod_squareoff", replace_existing=True,
        )
        scheduler.add_job(
            tsg_live_eod_job, trigger="cron", hour=15, minute=26,
            id="tsg_v1_live_eod_squareoff", replace_existing=True,
        )
        # ── GC_V1 BEGIN ── EOD backstop cron, UNIQUE id (checklist scar:
        # a cloned id with replace_existing evicts the donor's job). 15:22
        # sits between the engine's ≤15:20 EOD and the 15:25 paper sweep.
        scheduler.add_job(
            gc_live_eod_job, trigger="cron", hour=15, minute=22,
            id="gc_v1_live_eod_squareoff", replace_existing=True,
        )
        # ── GC_V1 END ──
        # IC carry morning (ONE_NIGHT_MAX instances only): fires 09:08 IST —
        # pre-market GTT teardown (first-candle rule), waits to
        # next_open_time (09:16), then the morning square-off retry loop.
        # No-op with no carried legs on any instance (an EOD-mode IC_V1
        # never carries — structural no-op). Second layer: each ICEngine's
        # continuous carry-morning state machine.
        scheduler.add_job(
            ic_morning_job, trigger="cron", hour=9, minute=8,
            id="ic_morning_squareoff", replace_existing=True,
        )
        # ── IC END ──
        # ── TMA_V1 BEGIN ──
        # Layer-three safety net (candle path + coordinator are layers 1-2).
        # trade_mode-aware: INTRADAY/expiry-day → square off; positional
        # carry → no-op; loop dead → STALE paper rows / CRITICAL for live.
        scheduler.add_job(
            tma_live_eod_job, trigger="cron", hour=15, minute=25,
            id="tma_live_eod_squareoff", replace_existing=True,
        )
        # ── TMA_V1 END ──
        # Fix 1: daily dated NFO instrument snapshot (Mon–Fri, 09:05 IST). Builds
        # ~/.scalp-app/state/instruments_history/NFO_YYYY-MM-DD.csv so future
        # backtests can resolve expired weeklies' tokens. Idempotent per day.
        scheduler.add_job(
            snapshot_job_factory(zerodha_manager),
            trigger="cron", day_of_week="mon-fri", hour=9, minute=5,
            id="instruments_daily_snapshot", replace_existing=True,
        )

        scheduler.start()
        write_audit_log("[SYSTEM] All EOD schedulers started)")
        lap("schedulers")

    # --------------------------------------------------
    # STANDALONE STRATEGY LAUNCHES
    # --------------------------------------------------
    # D1: each launch is independently guarded. Gates and call arguments are
    # byte-for-byte unchanged.
    app.state.startup_phase = "launches"

    # --------------------------------------------------
    # PST STANDALONE LAUNCH (paper phase — SELL + HEDGE, one loop)
    # --------------------------------------------------
    # ── LICENSE_GATE_FIX (2026-08-07) ── PST was the ONLY launch site
    # without the Phase-2 license gate; a license without PST still
    # started this loop and took paper trades. Gate now mirrors the
    # sibling strategies. Per-sid enforcement (mixed entitlements +
    # entitlement shrink after boot) lives inside the loop itself.
    with _boot_guard("launch PST"):
        _pst_entitled = [sid for sid in ("PST_SELL", "PST_HEDGE")
                         if STRATEGIES.get(sid, {}).get("enabled", False)
                         and license_state.license_allows_strategy(sid)]
        if _pst_entitled:
            asyncio.create_task(pst_selection_loop(zerodha_manager))
            write_audit_log(f"[SYSTEM] PST standalone selection loop launched (paper) — entitled: {_pst_entitled}")
        elif (STRATEGIES.get("PST_SELL", {}).get("enabled", False)
                or STRATEGIES.get("PST_HEDGE", {}).get("enabled", False)):
            write_audit_log("[SYSTEM][LICENSE] PST enabled but not entitled — loop NOT launched")

    # --------------------------------------------------
    # SCALP_V3 STANDALONE LAUNCH  (mirrors SCALP_V2 + PHASE 2 license gate)
    # --------------------------------------------------
    with _boot_guard("launch SCALP_V3"):
        if STRATEGIES.get("SCALP_V3", {}).get("enabled", False) and \\
                license_state.license_allows_strategy("SCALP_V3"):
            asyncio.create_task(scalp_v3_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] SCALP_V3 standalone selection loop launched")

            # Hedge-GTT reconcile loop: closes a live V3 trade when its hedge
            # SL-only GTT fires at the broker, freeing the single-trade gate.
            # Without this the row stays OPEN until the signal contract hits its
            # own SL/TP or EOD, blocking the next trade. (LIVE only; paper exits
            # via the tick engine's _watch_exit.)
            asyncio.create_task(scalp_v3_gtt_reconcile_loop())
            write_audit_log("[SYSTEM] SCALP_V3 hedge-GTT reconcile loop launched")

    # ── SCALP_V5 BEGIN ──
    # SCALP_V5 STANDALONE LAUNCH (mirrors SCALP_V3 + PHASE 2 license gate).
    # No GTT-reconcile loop: V5 has no hedge SL-only GTT to reconcile — its
    # SL/TP GTT (when present) is handled by the tick watcher's cancel→verify
    # exit path + the TIME exit, and a fired SL/TP OCO leg flattens the
    # position which the next close_trade()/EOD reconciles via ALREADY_FLAT.
    with _boot_guard("launch SCALP_V5"):
        if STRATEGIES.get("SCALP_V5", {}).get("enabled", False) and \\
                license_state.license_allows_strategy("SCALP_V5"):
            asyncio.create_task(scalpv5_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] SCALP_V5 standalone selection loop launched")
    # ── SCALP_V5 END ──

    # ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
    # IC STANDALONE LAUNCH (mirrors SCALP_V5 + PHASE 2 license gate).
    # Time-entry iron condor: no selection loop, no candle pipeline. ONE
    # runtime PER STRATEGY (IC_V1 = legacy EOD condor, IC_V2 = NEXT_OPEN
    # / ONE_NIGHT_MAX + ADJ_ON_MTC); each builds its own group manager +
    # engine (entry scheduler + REST LTP watcher + continuous EOD
    # backstop) + GTT backstop monitor. Defaults ship
    # trade_execution_mode=OFF — launching a runtime with mode OFF places
    # no orders and enters no positions.
    # D1: guarded PER INSTANCE — IC_V1 failing must not strand IC_V2.
    for _ic_sid in IC_STRATEGY_IDS:
        with _boot_guard(f"launch {_ic_sid}"):
            if STRATEGIES.get(_ic_sid, {}).get("enabled", False) and \\
                    license_state.license_allows_strategy(_ic_sid):
                asyncio.create_task(ic_runtime(zerodha_manager, _ic_sid))
                write_audit_log(f"[SYSTEM] {_ic_sid} standalone runtime launched")

    # ── TSG_V1 BEGIN ──
    # TSG_V1 STANDALONE LAUNCH (mirrors IC_V1; LD10 Phase 1).
    with _boot_guard("launch TSG_V1"):
        if STRATEGIES.get("TSG_V1", {}).get("enabled", False) and \\
                license_state.license_allows_strategy("TSG_V1"):
            asyncio.create_task(tsg_v1_runtime(zerodha_manager))
            write_audit_log("[SYSTEM] TSG_V1 standalone runtime launched")
    # ── TSG_V1 END ──

    # ── GC_V1 BEGIN ──
    # GC_V1 STANDALONE LAUNCH (mirrors TSG_V1; LD5/LD15 PAPER phase).
    # 2026-08-17: this line passed `broker_manager`, gc_v1_runtime's own
    # parameter name, which does not exist in this module. The NameError
    # killed startup here. Guarded now, and pyflakes gates the class.
    with _boot_guard("launch GC_V1"):
        if STRATEGIES.get("GC_V1", {}).get("enabled", False) and \\
                license_state.license_allows_strategy("GC_V1"):
            asyncio.create_task(gc_v1_runtime(zerodha_manager))
            write_audit_log("[SYSTEM] GC_V1 standalone runtime launched")
    # ── GC_V1 END ──
    # ── IC END ──

    # ── TMA_V1 BEGIN ──
    # TMA_V1 STANDALONE LAUNCH (mirrors PST + license gate). Triple-EMA
    # credit spread: 3-session EMA warmup, own KiteTicker, parity-by-
    # construction signals (backtest build_signals re-run per minute).
    # Ships mode=PAPER — launching starts paper trading; LIVE is a
    # Settings flip (dynamic mode, stamped per position).
    with _boot_guard("launch TMA_V1"):
        if STRATEGIES.get("TMA_V1", {}).get("enabled", False) and \\
                license_state.license_allows_strategy("TMA_V1"):
            asyncio.create_task(tma_selection_loop(zerodha_manager))
            write_audit_log("[SYSTEM] TMA_V1 standalone selection loop launched")
    # ── TMA_V1 END ──

    # --------------------------------------------------
    # BROKER RECONCILIATION
    # --------------------------------------------------
    app.state.startup_phase = "reconciliation"
    with _boot_guard("broker_reconciliation_thread"):
        threading.Thread(
            target=BrokerReconciliationJob(
                get_executor_for_broker("ZERODHA")
            ).run_forever,
            daemon=True,
        ).start()
        lap("broker_reconciliation_thread")

    # 🔔 TELEGRAM SCHEDULER START
    try:
        telegram_scheduler.start()
        write_audit_log("[TELEGRAM] Scheduler started")
    except Exception as e:
        write_audit_log(f"[TELEGRAM] Scheduler failed to start: {e}")

    # 🛡️ RELAY MONITOR START
    try:
        start_relay_monitor()
        write_audit_log("[RELAY_MONITOR] Started")
    except Exception as e:
        write_audit_log(f"[RELAY_MONITOR] Failed to start: {e}")

    # ── DISK_GUARD BEGIN ── free-space watchdog (own daemon thread)
    try:
        start_disk_guard()
    except Exception as e:
        write_audit_log(f"[DISK_GUARD] Failed to start: {e}")
    # ── DISK_GUARD END ──

    # --------------------------------------------------
    # COMPLETION
    # --------------------------------------------------
    # startup_complete flips True even with failures — the process IS up and
    # serving, and callers need to distinguish "still booting" from "booted,
    # degraded". startup_degraded carries the latter.
    _failures = list(getattr(app.state, "startup_failures", []))
    app.state.startup_complete = True
    app.state.startup_degraded = bool(_failures)
    if _failures:
        app.state.startup_phase = f"complete_with_failures ({len(_failures)})"
        write_audit_log(
            f"[SYSTEM][ERROR] Background startup completed WITH "
            f"{len(_failures)} FAILURE(S): "
            f"{', '.join(f['phase'] for f in _failures)}"
        )
    else:
        app.state.startup_phase = "complete"
        write_audit_log("[SYSTEM] Background startup complete")


@app.get("/boot-status")
def boot_status():
    """
    D3: makes a partial boot answerable without log archaeology.
    degraded=True means the process is serving but at least one component
    did not start. failures[] names them.
    """
    return {
        "startup_complete": getattr(app.state, "startup_complete", False),
        "startup_phase": getattr(app.state, "startup_phase", "unknown"),
        "degraded": getattr(app.state, "startup_degraded", False),
        "failures": getattr(app.state, "startup_failures", []),
    }
# ── BOOT_ISOLATION_20260817 END ──────────────────────────────────────────


'''

# Every scheduler job id that must survive the splice.
JOB_IDS = [
    "paper_trade_eod_squareoff", "bb_live_eod_squareoff",
    "bb_v2_live_eod_squareoff", "ha_live_eod_squareoff",
    "scalp_v3_live_eod_squareoff", "pst_live_eod_check",
    "scalpv5_live_eod_squareoff", "ic_live_eod_squareoff",
    "tsg_v1_live_eod_squareoff", "gc_v1_live_eod_squareoff",
    "ic_morning_squareoff", "tma_live_eod_squareoff",
    "instruments_daily_snapshot",
]

# Every launch call that must survive the splice.
LAUNCH_CALLS = [
    "pst_selection_loop(zerodha_manager)",
    "scalp_v3_selection_loop(zerodha_manager)",
    "scalp_v3_gtt_reconcile_loop()",
    "scalpv5_selection_loop(zerodha_manager)",
    "ic_runtime(zerodha_manager, _ic_sid)",
    "tsg_v1_runtime(zerodha_manager)",
    "gc_v1_runtime(zerodha_manager)",
    "tma_selection_loop(zerodha_manager)",
    "index_prev_close_watchdog(zerodha_manager)",
    "housekeeping_loop()",
]

NEEDED_IMPORTS = [
    ("contextmanager", "from contextlib import contextmanager"),
    ("traceback", "import traceback"),
]


def check(label, cond):
    print(f"  [{'ok  ' if cond else 'MISS'}] {label}")
    return cond


def ensure_imports(text):
    """Add contextlib/traceback imports if absent, right after the last
    top-level stdlib import line. Idempotent."""
    lines = text.split("\n")
    for name, stmt in NEEDED_IMPORTS:
        if any(l.strip() == stmt for l in lines):
            continue
        anchor = None
        for i, l in enumerate(lines[:120]):
            if l.startswith("import ") or l.startswith("from "):
                anchor = i
        if anchor is None:
            raise RuntimeError(f"no import anchor for {stmt}")
        lines.insert(anchor + 1, stmt)
    return "\n".join(lines)


def pyflakes_undefined(path):
    r = subprocess.run([sys.executable, "-m", "pyflakes", str(path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode not in (0, 1):
        raise RuntimeError(f"pyflakes could not run: {r.stderr.strip()}")
    if "No module named" in r.stderr:
        raise RuntimeError("pyflakes not installed")
    return [l for l in r.stdout.splitlines() if "undefined name" in l]


def transform(text):
    i = text.index(START)
    j = text.index(END)
    new = text[:i] + NEW_BLOCK + text[j:]
    return ensure_imports(new)


def main():
    staged, all_ok = [], True

    for tree in TREES:
        path = tree / REL
        print(f"\n=== {path} ===")
        if not path.exists():
            print("  [SKIP] tree not present")
            continue
        text = path.read_text(encoding="utf-8")

        all_ok &= check("start anchor unique", text.count(START) == 1)
        all_ok &= check("end anchor unique", text.count(END) == 1)
        all_ok &= check("start precedes end",
                        START in text and END in text
                        and text.index(START) < text.index(END))
        all_ok &= check("gc line already fixed",
                        "gc_v1_runtime(zerodha_manager)" in text)
        all_ok &= check("not already applied",
                        "BOOT_ISOLATION_20260817" not in text)
        if all_ok:
            staged.append((path, text))

    if not staged:
        print("\nABORT: nothing to do.")
        return 1
    if not all_ok:
        print("\nABORT: pre-anchor failure — NOTHING written.")
        return 1

    print("\n=== dry run ===")
    out = []
    for path, text in staged:
        tag = path.parts[-4]
        new = transform(text)
        ok = True
        ok &= check(f"{tag}: guard helper present",
                    "def _boot_guard(" in new)
        ok &= check(f"{tag}: boot-status route present",
                    '@app.get("/boot-status")' in new)
        ok &= check(f"{tag}: all 13 job ids survive",
                    all(f'id="{j}"' in new for j in JOB_IDS))
        ok &= check(f"{tag}: all launch calls survive",
                    all(c in new for c in LAUNCH_CALLS))
        ok &= check(f"{tag}: scheduler now precedes launches",
                    new.index("scheduler.start()") < new.index(
                        "tma_selection_loop(zerodha_manager)"))
        ok &= check(f"{tag}: no bare outer raise left",
                    "Background startup failed" not in new)

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(new)
            tmp = Path(tf.name)
        try:
            py_compile.compile(str(tmp), doraise=True)
            ok &= check(f"{tag}: py_compile", True)
        except py_compile.PyCompileError as e:
            print(f"  [MISS] {tag}: py_compile — {e}")
            ok = False
        try:
            ast.parse(new)
            ok &= check(f"{tag}: ast.parse", True)
        except SyntaxError as e:
            print(f"  [MISS] {tag}: ast.parse — {e}")
            ok = False
        try:
            und = pyflakes_undefined(tmp)
            ok &= check(f"{tag}: pyflakes clean ({len(und)} undefined)",
                        not und)
            for l in und:
                print(f"         {l}")
        except RuntimeError as e:
            print(f"  [MISS] {tag}: {e}")
            ok = False
        tmp.unlink(missing_ok=True)

        all_ok &= ok
        out.append((path, new))

    if not all_ok:
        print("\nABORT: dry-run failure — NOTHING written.")
        return 1

    print("\n=== writing ===")
    for path, new in out:
        path.write_text(new, encoding="utf-8")
        print(f"  written: {path}")

    print("\nDONE. Verify after rebuild:")
    print("  curl -s localhost:8000/boot-status | python3 -m json.tool")
    print("  grep -nE 'All EOD schedulers started|standalone|BOOT_FAIL|"
          "Background startup complete' ~/.scalp-app/logs/$(date +%F).log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
