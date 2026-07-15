# backend/app/engine/pst/pst_selection_loop.py
#
# ── PST SELECTION LOOP + MINUTE COORDINATOR ── (Phase 1, Delivery 3)
#
# Standalone async loop, launched from api_server EXACTLY like SCALP_V3
# (enabled-flag + license gate, NOT via StrategyRuntimeManager). One loop
# serves BOTH PST_SELL and PST_HEDGE — one WebSocket, one signal engine,
# two managers (D23).
#
# ORDERING (owned by the coordinator, parity-proven by the harness):
#   for each completed minute ts:
#     1. managers.on_minute(ts, spot_candle, chain)   — exits first
#     2. signals = signal_engine.on_spot_candle(spot) — then new signals
#     3. managers.on_signal(sig, chain)               — then entries
#
# BOOT: warmup via Kite historical (pst_live_warmup, fail closed), universe
# resolved from spot LTP + expected weekly expiry, engine day started.
# WATCHDOG: no spot candle for 3 minutes inside session hours → Telegram
# system alert (zombie-WS doctrine: connected ≠ ticking).
# EOD: managers.force_eod fires from the coordinator at exit_time as a
# belt-and-braces layer under the api_server cron job.

from __future__ import annotations

import asyncio
import time  # (backfill uses time.time for IST minutes)
from datetime import datetime, date
from typing import List, Optional

try:
    from app.engine.pst.pst_common import PSTRepo, hm_to_min, ist_day_start
    from app.engine.pst.pst_live_signal_engine import PSTLiveSignalEngine
    from app.engine.pst.pst_live_warmup import fetch_prev_session_spot, fetch_today_spot
    from app.engine.pst.pst_sell_paper_manager import PSTSellPaperManager
    from app.engine.pst.pst_hedge_paper_manager import PSTHedgePaperManager
    from app.engine.pst.pst_tick_engine import PSTTickEngine
except ImportError:  # standalone tests
    from pst_common import PSTRepo, hm_to_min, ist_day_start
    from pst_live_signal_engine import PSTLiveSignalEngine
    from pst_live_warmup import fetch_prev_session_spot, fetch_today_spot
    from pst_sell_paper_manager import PSTSellPaperManager
    from pst_hedge_paper_manager import PSTHedgePaperManager
    from pst_tick_engine import PSTTickEngine
try:
    from app.engine.pst.pst_order_executor import PaperExecutor, LiveExecutor
except ImportError:
    from pst_order_executor import PaperExecutor, LiveExecutor

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)

IST = 5 * 3600 + 30 * 60

# module-level runtime registry (V3's get_manager pattern) — lets the EOD
# cron square off through the SAME managers/executors the loop trades with.
_RUNTIME = {"managers": []}


def get_managers():
    return list(_RUNTIME.get("managers") or [])


class PSTMinuteCoordinator:
    def __init__(self, signal_engine: PSTLiveSignalEngine,
                 managers: List, exit_min: int, notify=None):
        self.sig_engine = signal_engine
        self.managers = managers
        self.exit_min = exit_min
        self.notify = notify
        self.last_spot_seen = 0.0
        self._eod_done_day: Optional[int] = None
        self._warned_quiet = False

    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        # 1) exits / monitoring first
        for m in self.managers:
            m.on_minute(ts, spot_candle, chain)
        # 2) signals from the completed spot candle
        if spot_candle is not None:
            self.last_spot_seen = time.time()
            self._warned_quiet = False
            for sig in self.sig_engine.on_spot_candle(spot_candle):
                # 3) entries
                for m in self.managers:
                    m.on_signal(sig, chain)
        # EOD belt-and-braces
        day = ist_day_start(ts)
        if ts >= day + self.exit_min * 60 and self._eod_done_day != day:
            for m in self.managers:
                m.force_eod(ts)
            self._eod_done_day = day
        # zombie-WS watchdog (session hours only)
        mins = (ts - day) // 60
        in_session = (9 * 60 + 15) <= mins <= (15 * 60 + 30)
        if in_session and self.last_spot_seen and \
                time.time() - self.last_spot_seen > 180 and not self._warned_quiet:
            self._warned_quiet = True
            write_audit_log("[PST][WATCHDOG] no spot candle for 3+ minutes "
                            "during session — possible zombie WebSocket")
            if self.notify:
                try:
                    self.notify("PST: no spot candle for 3+ minutes — "
                                "possible zombie WebSocket, check the app")
                except Exception:
                    pass


async def pst_selection_loop(zerodha_manager):
    """Launched from api_server when PST_SELL or PST_HEDGE is enabled."""
    from app.config.strategy_loader import load_strategy_config
    from app.strategy.strategy_registry import STRATEGIES
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day
    try:
        from app.engine.pst.pst_common import canonical_db_path
    except ImportError:
        from pst_common import canonical_db_path
    db_path = canonical_db_path()   # data/app.db — get_conn()'s file. NEVER hand-build
    try:
        from app.utils.app_paths import APP_HOME
        capture_dir = str(APP_HOME / "pst_capture")
    except Exception:
        import os
        capture_dir = os.path.expanduser("~/.scalp-app/pst_capture")
    notify = None
    try:
        from app.api.telegram_api import notify_system_alert as _raw_alert
        # notify_system_alert takes a DICT — all PST call sites pass strings,
        # so wrap once here (string → {"message", "severity"}).
        def notify(msg, severity="warning"):
            _raw_alert({"message": str(msg), "severity": severity})
    except Exception:
        pass

    write_audit_log("[PST] selection loop starting (paper phase)")

    # wait for Zerodha session
    kite = None
    for _ in range(240):
        try:
            kite = zerodha_manager.get_kite()
            if kite is not None:
                break
        except Exception:
            pass
        await asyncio.sleep(5)
    if kite is None:
        write_audit_log("[PST] Zerodha never became ready — loop exiting (fail closed)")
        return

    from app.fetcher.zerodha_instruments import load_instruments_df
    instruments_df = load_instruments_df()

    # ── boot warmup (D24) — fail closed on any gap ──
    warm = fetch_prev_session_spot(kite, instruments_df=instruments_df)
    sig_engine = PSTLiveSignalEngine()
    if warm is None or not sig_engine.seed_warmup(
            warm["spot_1m"], warm["day_start"], warm["prev_hlc"]):
        write_audit_log("[PST] warmup unavailable — PST will not trade today (fail closed)")
        if notify:
            try:
                notify("PST paper: warmup unavailable — not trading today")
            except Exception:
                pass
        return
    today = datetime.now().date()
    sig_engine.start_day(int((datetime(today.year, today.month, today.day)
                              - datetime(1970, 1, 1)).total_seconds()) - IST)

    # ── MIDSESSION_BACKFILL BEGIN ── restart during session hours: rebuild
    # the day's spot prefix from Kite historical so replay indicators
    # (SMA9@5m, ST 10×2@3m) match a continuous run. Signals emitted during
    # backfill are DISCARDED — they are minutes old; positions that were
    # never opened stay unopened (conservative by design). Managers only
    # see signals from live candles onward.
    _now_ist_min = (int(time.time()) + IST) % 86400 // 60
    if _now_ist_min > (9 * 60 + 16):
        _bf = fetch_today_spot(kite, instruments_df=instruments_df)
        _fed = 0
        for _c in _bf:
            sig_engine.on_spot_candle(_c)      # returns old signals — dropped
            _fed += 1
        if _fed:
            write_audit_log(f"[PST][BACKFILL] mid-session start: {_fed} spot "
                            f"candles restored (last ts {_bf[-1]['ts']}); "
                            f"seam to live feed ≤1–2 min (historical lag)")
        else:
            write_audit_log("[PST][BACKFILL] mid-session start but no candles "
                            "returned — signal indicators run on a GAPPED "
                            "prefix today; treat signals with suspicion")
    # ── MIDSESSION_BACKFILL END ──

    # ── managers (paper-hardwired; each disabled unless its flag is on) ──
    repo = PSTRepo(db_path)

    # DYNAMIC MODE: every manager gets BOTH executors; the fresh entry-time
    # config read picks per position (no restart after a Settings flip).
    _live_exec = LiveExecutor(zerodha_manager, notify=notify)   # relay-routed (2026-07-15)

    managers = []
    tables = {}
    exit_min = 15 * 60 + 25
    if STRATEGIES.get("PST_SELL", {}).get("enabled", False):
        cfg = load_strategy_config("PST_SELL")
        m = PSTSellPaperManager(cfg, repo, live_executor=_live_exec)
        managers.append(m); tables[id(m)] = "pst_sell_trades"
        exit_min = hm_to_min(cfg.get("exit_time", "15:25"), exit_min)
    if STRATEGIES.get("PST_HEDGE", {}).get("enabled", False):
        cfg = load_strategy_config("PST_HEDGE")
        m = PSTHedgePaperManager(cfg, repo, live_executor=_live_exec)
        managers.append(m); tables[id(m)] = "pst_hedge_trades"
        exit_min = max(exit_min, hm_to_min(cfg.get("exit_time", "15:25"), exit_min))
    managers = [m for m in managers if not getattr(m, "disabled", False)]

    # ── BOOT RECONCILIATION + ADOPTION (fail closed) ──
    today_start = int((datetime(today.year, today.month, today.day)
                       - datetime(1970, 1, 1)).total_seconds()) - IST
    for m in managers:
        table = tables[id(m)]
        rows = repo.open_legs(table)
        stale = [r for r in rows if int(r["entry_ts"]) < today_start]
        for r in stale:
            repo.mark_stale(table, r["id"])
        if stale:
            write_audit_log(f"[PST] {table}: {len(stale)} OPEN leg(s) from a "
                            f"previous session marked STALE — review manually")
            if notify:
                try:
                    notify(f"PST: {len(stale)} stale open leg(s) in {table} from a "
                           f"previous session — marked STALE, review")
                except Exception:
                    pass
        todays = [r for r in rows if int(r["entry_ts"]) >= today_start]
        if not todays:
            continue
        if str(todays[0].get("mode", "PAPER")).upper() == "LIVE":
            # broker cross-check: net position must match the rows exactly
            try:
                sym = todays[0]["tradingsymbol"]
                want = sum(int(r["qty"]) for r in todays)
                if isinstance(m, PSTSellPaperManager):
                    want = -want
                net = 0
                for p in kite.positions().get("net", []):
                    if p.get("tradingsymbol") == sym:
                        net = int(p.get("quantity") or 0)
                if net != want:
                    m.disabled = True
                    write_audit_log(f"[PST][RECON] {table}: broker net {net} != "
                                    f"expected {want} for {sym} — manager DISABLED "
                                    f"(fail closed), resolve manually")
                    if notify:
                        try:
                            notify(f"PST RECON MISMATCH {sym}: broker {net} vs "
                                   f"app {want} — {table} disabled, resolve manually")
                        except Exception:
                            pass
                    continue
                write_audit_log(f"[PST][RECON] {table}: broker matches app "
                                f"({net} {sym}) — adopting; TP falls back to "
                                f"app-monitored (resting order ids not persisted)")
            except Exception as e:
                m.disabled = True
                write_audit_log(f"[PST][RECON] {table}: positions check failed "
                                f"({e}) — manager DISABLED (fail closed)")
                continue
        m.adopt_rows(todays)
    managers = [m for m in managers if not getattr(m, "disabled", False)]
    if not managers:
        write_audit_log("[PST] no enabled paper managers — loop exiting")
        return

    _RUNTIME["managers"] = managers
    coord = PSTMinuteCoordinator(sig_engine, managers, exit_min, notify=notify)

    # ── universe + websocket ──
    engine = PSTTickEngine(zerodha_manager, instruments_df,
                           coord.on_minute, capture_dir=capture_dir)
    try:
        spot_ltp = float(kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"])
    except Exception as e:
        write_audit_log(f"[PST] spot LTP fetch failed ({e}) — not trading (fail closed)")
        return
    expiry_iso = expected_expiry_for_day(today).isoformat()
    n = engine.resolve_universe(spot_ltp, expiry_iso)
    if n == 0:
        write_audit_log("[PST] empty option universe — not trading (fail closed)")
        return
    engine.start()
    write_audit_log(f"[PST] LIVE (paper): {len(managers)} manager(s), "
                    f"{n} contracts, expiry {expiry_iso}")

    # keep the task alive; the tick engine drives everything on its timer
    while True:
        await asyncio.sleep(60)


async def pst_live_eod_job():
    """api_server cron (15:28): REAL square-off, V3's get_manager pattern.
    Primary path: force_eod through the live manager objects (works even
    with a zombie WebSocket — paper closes at last known close; LIVE
    routes through the executor: cancel resting TP, market exit, actual
    fills booked). Managers unreachable (loop never started / crashed)
    with rows still OPEN → paper rows are marked STALE (no invented
    prices), LIVE rows raise a CRITICAL alert for manual square-off —
    the one scenario this job cannot safely automate is placing orders
    without the loop's confirmed broker session."""
    import time as _time
    closed_via_managers = 0
    for m in get_managers():
        try:
            if m.open_legs or m.pending:
                m.force_eod(int(_time.time()))
                closed_via_managers += 1
        except Exception as e:
            write_audit_log(f"[PST][EOD] manager force_eod failed: {e}")
    if closed_via_managers:
        write_audit_log(f"[PST][EOD] square-off via {closed_via_managers} manager(s)")
    try:
        from app.engine.pst.pst_common import canonical_db_path
    except ImportError:
        from pst_common import canonical_db_path
    repo = PSTRepo(canonical_db_path())
    critical = []
    for table in ("pst_sell_trades", "pst_hedge_trades"):
        for r in (repo.open_legs(table) or []):
            if str(r.get("mode", "PAPER")).upper() == "PAPER":
                repo.mark_stale(table, r["id"])   # honest: no invented price
                write_audit_log(f"[PST][EOD] paper leg {table}#{r['id']} "
                                f"({r['tradingsymbol']}) marked STALE — loop was down")
            else:
                critical.append((table, r))
    if critical:
        msg = "; ".join(f"{t}#{r['id']} {r['tradingsymbol']} qty {r['qty']}"
                        for t, r in critical)
        write_audit_log(f"[PST][EOD][CRITICAL] LIVE legs still OPEN and the "
                        f"loop is unreachable — SQUARE OFF MANUALLY: {msg}")
        try:
            from app.api.telegram_api import notify_system_alert
            notify_system_alert({"message": f"🚨 PST LIVE legs OPEN after EOD, "
                                            f"loop dead — square off manually NOW: {msg}",
                                 "severity": "error"})
        except Exception:
            pass
    else:
        write_audit_log("[PST][EOD] clean — no open PST legs")