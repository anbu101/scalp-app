#!/usr/bin/env python3
# apply_broker_session_guard.py — fence BROKER_SESSION_GUARD_20261022
# ============================================================================
# 2026-10-22: Kite was not logged in. Two gaps surfaced:
#
# 1. IC_V2 PAPER morning close booked a FAKE ₹0 trade. _close_leg did
#        px = self._premium(...) or leg.entry_price
#    so with no feed the 09:16 NEXT_OPEN close booked every carried leg at
#    entry. LIVE was already right (strict path: order failure → leg stays
#    OPEN, retry, critical alert). FIX: a STRICT (morning) PAPER close with
#    no fresh price now behaves like LIVE — leg stays OPEN, the engine's
#    2 s retry loop + IC_MORNING_STUCK alert (every 120 s) keep going, and
#    the leg closes at the first REAL price once the feed is back. Every
#    other path that still has to fall back to entry (forced EOD/MTM with no
#    price, a LIVE fill with no reported price) now raises a bell warning
#    IC_NOPRICE_EXIT naming the leg — never silent again.
#
# 2. No pre-market alarm for a missing Kite login. NEW job
#    jobs/broker_session_watch.py, every 2 min on trading days 08:45–15:30:
#    WARNING at 08:45 if not logged in, CRITICAL at 09:10 (naming open
#    positions + LIVE strategies), CRITICAL if the session is lost mid-day,
#    WARNING if logged in but trading disabled while a strategy is LIVE,
#    INFO when the session comes back. Once per transition per day.
#
# Files (backend/app and, if present, desktop/src-tauri/backend/app):
#   engine/ic/ic_group_manager.py          (edit)
#   engine/ic/test_ic_group_manager.py     (2 new tests)
#   jobs/broker_session_watch.py           (new)
#   api_server.py                          (import + scheduler registration)
#
# Run from the repo root:  python3 apply_broker_session_guard.py
# Idempotent, staged all-or-nothing, .bak-BROKER_SESSION_GUARD_20261022,
# py_compile gate, behavioural tests (watchdog decision table; IC suite incl.
# the new tests via pytest when installed).
# ============================================================================

from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import types

FENCE = "BROKER_SESSION_GUARD_20261022"
WATCH_FENCE = "BROKER_SESSION_WATCH_20261022"
TREES = ["backend/app", "desktop/src-tauri/backend/app"]

F_IC = "engine/ic/ic_group_manager.py"
F_IC_TEST = "engine/ic/test_ic_group_manager.py"
F_WATCH = "jobs/broker_session_watch.py"
F_API = "api_server.py"
EDIT_FILES = [F_IC, F_IC_TEST, F_API]

PREREQ = {
    F_IC: "def morning_square_off(self) -> int:",
    F_IC_TEST: "def test_v2g4b_morning_strict_retry",
    F_API: "id=\"scalpv5_eod_watchdog\", replace_existing=True,",
}

WATCH_SRC = '''# backend/app/jobs/broker_session_watch.py
# ── BROKER_SESSION_WATCH_20261022 ──
# ============================================================================
# 2026-10-22: Kite was not logged in. Nothing said so until a human looked at
# the header chip at 09:35 — by then IC_V2's 09:16 carry close had already run
# with no prices, and TMA/TMA2/VET were waiting silently for a session. With
# LIVE strategies that is an unmanaged book.
#
# This job is the alarm. Trading days only, every 2 min, 08:45–15:30 IST:
#   * not logged in at/after 08:45 → WARNING  (bell + Telegram)
#   * still not logged in at 09:10 → CRITICAL (bell + Telegram), naming the
#     open positions and the LIVE strategies that depend on the session
#   * session LOST mid-day (was ready, now not) → CRITICAL
#   * logged in but trading DISABLED while a strategy is LIVE → WARNING
#   * session back after any of the above → INFO
# Each alert fires once per transition per day — never a flood.
#
# Signal = the same check that drives the header's "No Broker" chip
# (zerodha_manager.is_trade_ready()), after the accessor's token-rotation
# check so a fresh morning login is seen immediately. Never raises.
# ============================================================================

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

try:
    from app.event_bus.audit_logger import write_audit_log
except Exception:                                    # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_START_MIN = 8 * 60 + 45
ESCALATE_MIN = 9 * 60 + 10
WINDOW_END_MIN = 15 * 60 + 30

_STATE: Dict = {}


def _fresh_state(day: str) -> Dict:
    return {"day": day, "warned": False, "critical": False, "was_ready": False,
            "lost": False, "td_warned": False}


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def evaluate(now_min: int, *, logged_in: bool, trade_ready: bool,
             live_ids: List[str], open_summary: str,
             state: Dict) -> List[Tuple[str, str]]:
    """Pure decision. Returns [(severity, message)] and mutates state."""
    out: List[Tuple[str, str]] = []
    if not (WINDOW_START_MIN <= now_min <= WINDOW_END_MIN):
        return out
    pos = f" Open positions: {open_summary}." if open_summary else ""
    live = (f" LIVE strategies affected: {', '.join(live_ids)}."
            if live_ids else "")

    if trade_ready:
        if state["warned"] or state["critical"] or state["lost"] or state["td_warned"]:
            out.append(("info", f"Kite session ready at {_hm(now_min)} — "
                                f"strategies can price and trade again."))
            state.update(warned=False, critical=False, lost=False, td_warned=False)
        state["was_ready"] = True
        return out

    if logged_in:
        # token valid, trading switched off — only matters when something is LIVE
        if live_ids and not state["td_warned"]:
            state["td_warned"] = True
            out.append(("warning", f"Kite is logged in but TRADING IS DISABLED "
                                   f"(Connections) — LIVE strategies cannot "
                                   f"place orders.{live}{pos}"))
        return out

    if state["was_ready"]:
        if not state["lost"]:
            state["lost"] = True
            out.append(("critical", f"Kite session LOST at {_hm(now_min)} — "
                                    f"strategies have no prices and cannot place "
                                    f"orders. Log in to Kite again now.{live}{pos}"))
        return out

    if now_min >= ESCALATE_MIN:
        if not state["critical"]:
            state["critical"] = state["warned"] = True
            out.append(("critical", f"Kite STILL NOT LOGGED IN at {_hm(now_min)} — "
                                    f"the market opens 09:15. Carried positions "
                                    f"cannot be closed or marked until you log "
                                    f"in.{live}{pos}"))
        return out
    if not state["warned"]:
        state["warned"] = True
        out.append(("warning", f"Kite not logged in yet ({_hm(now_min)}) — log "
                               f"in before 09:15.{live}{pos}"))
    return out


# ── real-world signal readers (each fail-soft) ──────────────────────────────
def _live_strategy_ids() -> List[str]:
    try:
        from app.strategy.strategy_registry import STRATEGIES
        from app.risk import execution_modes as xm
        from app.license import license_state
    except Exception:
        return []
    out = []
    for sid in STRATEGIES:
        try:
            if (license_state.license_allows_strategy(sid)
                    and xm.wants_live(xm.strategy_mode(sid))):
                out.append(sid)
        except Exception:
            continue
    return out


_OPEN_QUERIES = [
    ("SELECT strategy_name, COUNT(*) FROM paper_trades WHERE state='OPEN' "
     "GROUP BY strategy_name", None),
    ("SELECT strategy_id, COUNT(*) FROM trades WHERE exit_time IS NULL "
     "GROUP BY strategy_id", None),
    ("SELECT COUNT(*) FROM tma_trades WHERE status='OPEN'", "TMA_V1"),
    ("SELECT COUNT(*) FROM tma2_trades WHERE status='OPEN'", "TMA_V2"),
    ("SELECT COUNT(*) FROM vet_trades WHERE status='OPEN'", "VET_V1"),
]


def _open_summary(db_path: Optional[str] = None) -> str:
    try:
        if db_path is None:
            from app.utils.app_paths import DB_PATH
            db_path = str(DB_PATH)
        c = sqlite3.connect(db_path, timeout=5)
    except Exception:
        return ""
    counts: Dict[str, int] = {}
    try:
        for q, sid in _OPEN_QUERIES:
            try:
                rows = c.execute(q).fetchall()
            except Exception:
                continue
            for r in rows:
                k, n = (sid, r[0]) if sid else (r[0], r[1])
                if k and n:
                    counts[str(k)] = max(counts.get(str(k), 0), int(n))
    finally:
        c.close()
    return ", ".join(f"{k} {v} leg{'s' if v != 1 else ''}"
                     for k, v in sorted(counts.items()))


def _emit(severity: str, msg: str) -> None:
    write_audit_log(f"[BROKER_WATCH][{severity.upper()}] {msg}")
    if severity == "critical":
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"message": msg, "severity": "error"})   # mirrors to bell
        except Exception as e:
            write_audit_log(f"[BROKER_WATCH] critical notify failed: {e!r}")
        return
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert("BROKER_SESSION", msg, severity=severity)
    except Exception:
        pass
    try:
        from app.api.telegram_api import notify_system_alert
        notify_system_alert({"message": msg, "severity": severity})
    except Exception as e:
        write_audit_log(f"[BROKER_WATCH] notify failed: {e!r}")


def broker_session_watch_job(manager=None) -> None:
    try:
        from app.utils.market_hours import is_trading_day
        if not is_trading_day():
            return
        now = datetime.now(IST)
        now_min = now.hour * 60 + now.minute
        if not (WINDOW_START_MIN <= now_min <= WINDOW_END_MIN):
            return
        day = now.date().isoformat()
        if _STATE.get("day") != day:
            _STATE.clear()
            _STATE.update(_fresh_state(day))

        trade_ready = False
        if manager is not None:
            try:
                manager.get_kite()               # TOKEN_ROTATE: see a fresh login now
            except Exception:
                pass
            try:
                trade_ready = bool(manager.is_trade_ready())
            except Exception:
                trade_ready = False
        logged_in = trade_ready
        if not trade_ready:
            try:
                from app.brokers.zerodha_auth import is_token_valid
                logged_in = bool(is_token_valid())
            except Exception:
                logged_in = False

        needs_context = not trade_ready
        alerts = evaluate(now_min, logged_in=logged_in, trade_ready=trade_ready,
                          live_ids=_live_strategy_ids() if needs_context else [],
                          open_summary=_open_summary() if needs_context else "",
                          state=_STATE)
        for sev, msg in alerts:
            _emit(sev, msg)
    except Exception as e:
        try:
            write_audit_log(f"[BROKER_WATCH][ERROR] {e!r}")
        except Exception:
            pass
'''


def die(msg):
    print(f"\n[{FENCE}] ABORT: {msg}\nNothing was written.")
    sys.exit(1)


NOPRICE_METHOD = (
    "    # ── BROKER_SESSION_GUARD_20261022 ── no-price exits are never silent\n"
    "    def _noprice_note(self, leg, reason: str, *, deferred: bool) -> None:\n"
    "        rt = self._rt.setdefault(leg.leg_id, {})\n"
    "        now = time.time()\n"
    "        if deferred:\n"
    "            if now - float(rt.get(\"_noprice_log_ts\") or 0) >= 60:\n"
    "                rt[\"_noprice_log_ts\"] = now\n"
    "                write_audit_log(f\"[IC][{self.strategy_id}][NOPRICE][DEFER] \"\n"
    "                                f\"{leg.leg_id} {leg.symbol} {reason}: no fresh \"\n"
    "                                f\"price — leg stays OPEN, retrying (never \"\n"
    "                                f\"booked at entry)\")\n"
    "            return\n"
    "        write_audit_log(f\"[IC][{self.strategy_id}][NOPRICE][AT_ENTRY] \"\n"
    "                        f\"{leg.leg_id} {leg.symbol} {reason}: closed with NO \"\n"
    "                        f\"price — booked at entry {leg.entry_price}\")\n"
    "        if not rt.get(\"_noprice_alerted\"):\n"
    "            rt[\"_noprice_alerted\"] = True\n"
    "            record_alert(\"IC_NOPRICE_EXIT\",\n"
    "                         f\"{self.strategy_id}: {leg.symbol} closed ({reason}) \"\n"
    "                         f\"with NO market price — booked at entry \"\n"
    "                         f\"{leg.entry_price}; this leg's P&L is not real. \"\n"
    "                         f\"Check the broker session.\",\n"
    "                         severity=\"warning\", strategy_id=self.strategy_id,\n"
    "                         symbol=leg.symbol)\n"
    "\n"
)

NEW_TESTS = '''
# ── BROKER_SESSION_GUARD_20261022 ── PAPER morning close with NO price
# (Kite not logged in) must defer, never book a fake close at entry.
def test_v2g4c_paper_morning_noprice_defers_then_real_price():
    _Cfg.strategy = _v2_cfg()
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    assert m.commit_carry("PAPER")
    payload = ic_carry_store.load_carry(TEST_SID)
    m2, ex2 = make_mgr()
    assert m2.restore_carry_payload(payload)
    ex2.ltp.clear(); LTPStore._d.clear()            # no broker session
    n0 = len(DB["closed"])
    assert m2.morning_square_off() == 4             # all still open
    assert m2.morning_square_off() == 4             # retry loop, still open
    assert len(DB["closed"]) == n0                  # nothing booked
    g2 = m2.current_group()
    assert all(l.state == L_OPEN for l in g2.legs.values())
    assert "IC_NOPRICE_EXIT" not in ALERTS          # a deferral is not a booking
    real = {"N24150CE": 60.0, "N24100PE": 50.0, "N24700CE": 1.0,
            "N23200PE": 1.2}
    ex2.ltp.update(real)                            # login → feed back
    assert m2.morning_square_off() == 0
    assert g2.state == G_CLOSED
    assert all(l.exit_reason == "NEXT_OPEN" for l in g2.legs.values())
    assert sorted(k["exit_price"] for _, k in DB["closed"][n0:]) == \\
        sorted(real.values())                       # REAL prices, not entry


def test_v2g4d_paper_forced_noprice_books_entry_loudly():
    m, ex = make_mgr()
    assert m.enter_day(make_selection(), mode="PAPER")
    ex.ltp.clear(); LTPStore._d.clear()
    assert m.force_square_off_all(reason="EOD") == 4   # EOD still flattens
    assert ALERTS.count("IC_NOPRICE_EXIT") == 4       # …but says so, per leg


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))'''

EDITS = {
    F_IC: [
        ("        if self._paper or self._rt[leg_id].get(\"phantom\"):\n"
         "            px = self._premium(leg.symbol, ltp_hint) or leg.entry_price\n"
         "        else:\n",
         "        if self._paper or self._rt[leg_id].get(\"phantom\"):\n"
         "            px = self._premium(leg.symbol, ltp_hint)\n"
         "            # ── BROKER_SESSION_GUARD_20261022 ── 2026-10-22: no Kite login →\n"
         "            # the 09:16 PAPER morning close booked every carried leg AT ENTRY\n"
         "            # (fake ₹0). A STRICT close with no price now behaves like LIVE:\n"
         "            # leg stays OPEN, engine retries, closes at the first real price.\n"
         "            if px is None:\n"
         "                if strict and not self._rt[leg_id].get(\"phantom\"):\n"
         "                    with self._mutex:\n"
         "                        self._rt[leg_id][\"_exiting\"] = False\n"
         "                    self._noprice_note(leg, reason, deferred=True)\n"
         "                    return False\n"
         "                if not self._rt[leg_id].get(\"phantom\"):\n"
         "                    self._noprice_note(leg, reason, deferred=False)\n"
         "                px = leg.entry_price\n"
         "        else:\n"),
        ("            if px is None:\n"
         "                px = self._premium(leg.symbol, ltp_hint) or leg.entry_price\n",
         "            if px is None:\n"
         "                px = self._premium(leg.symbol, ltp_hint)\n"
         "                if px is None:      # ── BROKER_SESSION_GUARD_20261022 ──\n"
         "                    self._noprice_note(leg, reason, deferred=False)\n"
         "                    px = leg.entry_price\n"),
        ("    def _premium(self, symbol: str, hint: Optional[float]) -> Optional[float]:\n",
         NOPRICE_METHOD +
         "    def _premium(self, symbol: str, hint: Optional[float]) -> Optional[float]:\n"),
    ],
    F_IC_TEST: [
        ("\nif __name__ == \"__main__\":\n    sys.exit(pytest.main([__file__, \"-v\"]))",
         NEW_TESTS),
    ],
    F_API: [
        ("from app.jobs.liveness_ping import liveness_ping_loop      # ← NEW (dead-man's switch)\n",
         "from app.jobs.liveness_ping import liveness_ping_loop      # ← NEW (dead-man's switch)\n"
         "from app.jobs.broker_session_watch import broker_session_watch_job   # ── BROKER_SESSION_GUARD_20261022 ──\n"),
        ("            id=\"scalpv5_eod_watchdog\", replace_existing=True,\n"
         "        )\n",
         "            id=\"scalpv5_eod_watchdog\", replace_existing=True,\n"
         "        )\n"
         "        # ── BROKER_SESSION_GUARD_20261022 ── Kite-login alarm: 08:45 warn,\n"
         "        # 09:10 critical, mid-day loss critical, recovery info.\n"
         "        scheduler.add_job(\n"
         "            broker_session_watch_job, trigger=\"cron\", day_of_week=\"mon-fri\",\n"
         "            hour=\"8-15\", minute=\"*/2\", args=[zerodha_manager],\n"
         "            id=\"broker_session_watch\", replace_existing=True,\n"
         "        )\n"),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
def test_watch(src):
    fails = []

    def check(name, cond):
        print(f"   {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            fails.append(name)

    mod = types.ModuleType("bsw_under_test")
    exec(compile(src, "<broker_session_watch>", "exec"), mod.__dict__)
    ev = mod.evaluate

    def M(h, m):
        return h * 60 + m

    def run(steps, live=("IC_V2",), opened="IC_V2 3 legs"):
        st = mod._fresh_state("2026-10-22")
        return [ev(t, logged_in=li, trade_ready=tr, live_ids=list(live),
                   open_summary=opened, state=st) for t, li, tr in steps]

    r = run([(M(8, 30), False, False)])
    check("W1 08:30 not logged in → silent (before window)", r == [[]])
    r = run([(M(8, 46), False, False), (M(8, 48), False, False)])
    check("W2 08:46 → one WARNING, 08:48 → nothing more",
          len(r[0]) == 1 and r[0][0][0] == "warning" and r[1] == [])
    r = run([(M(8, 46), False, False), (M(9, 10), False, False), (M(9, 12), False, False)])
    check("W3 09:10 → CRITICAL once", bool(r[1]) and r[1][0][0] == "critical" and r[2] == [])
    check("W3b critical names open positions + LIVE strategies",
          "IC_V2 3 legs" in r[1][0][1] and "LIVE strategies affected: IC_V2" in r[1][0][1])
    r = run([(M(9, 20), False, False)])
    check("W4 first look at 09:20 (late boot) → ONLY the critical, no double",
          len(r[0]) == 1 and r[0][0][0] == "critical")
    r = run([(M(8, 46), False, False), (M(9, 10), False, False), (M(9, 36), True, True)])
    check("W5 login at 09:36 → INFO recovery", bool(r[2]) and r[2][0][0] == "info")
    r = run([(M(9, 14), True, True), (M(11, 2), False, False), (M(11, 4), False, False),
             (M(11, 6), True, True)])
    check("W6 ready, then LOST at 11:02 → CRITICAL once, INFO on return",
          r[0] == [] and r[1][0][0] == "critical" and "LOST" in r[1][0][1]
          and r[2] == [] and r[3][0][0] == "info")
    r = run([(M(9, 0), True, False)])
    check("W7 logged in, trading disabled, LIVE strategy → WARNING",
          bool(r[0]) and r[0][0][0] == "warning" and "DISABLED" in r[0][0][1])
    r = run([(M(9, 0), True, False)], live=())
    check("W8 logged in, trading disabled, paper-only fleet → silent", r == [[]])
    r = run([(M(9, 14), True, True), (M(15, 0), True, True)])
    check("W9 healthy day → never a word", r == [[], []])
    r = run([(M(15, 31), False, False)])
    check("W10 after 15:30 → silent", r == [[]])

    # open-position summary on a real sqlite file with the real table names
    import sqlite3
    db = os.path.join(tempfile.mkdtemp(), "app.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE paper_trades (strategy_name TEXT, state TEXT)")
    c.executemany("INSERT INTO paper_trades VALUES (?,?)",
                  [("IC_V2", "OPEN")] * 3 + [("IC_V2", "CLOSED")])
    c.execute("CREATE TABLE tma2_trades (status TEXT)")
    c.executemany("INSERT INTO tma2_trades VALUES (?)", [("OPEN",), ("OPEN",)])
    c.commit(); c.close()
    s = mod._open_summary(db)
    check(f"W11 open summary from DB → {s!r}", s == "IC_V2 3 legs, TMA_V2 2 legs")
    check("W12 missing DB → empty summary, no raise",
          mod._open_summary("/nonexistent/dir/app.db") == "")
    if fails:
        die(f"watchdog tests failed: {fails}")


def test_ic(staged, tree):
    """Run the IC group-manager suite (incl. the 2 new tests) on a temp copy."""
    try:
        import pytest  # noqa: F401
    except ImportError:
        print("   note: pytest not installed — IC suite skipped here "
              "(it was run before delivery). pip3 install pytest to run it.")
        return
    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, "ic")
    shutil.copytree(os.path.join(tree, "engine/ic"), dst,
                    ignore=shutil.ignore_patterns("*.bak-*", "__pycache__"))
    for rel in (F_IC, F_IC_TEST):
        with open(os.path.join(dst, os.path.basename(rel)), "w", encoding="utf-8") as f:
            f.write(staged[os.path.join(tree, rel)][1])
    p = subprocess.run([sys.executable, "-m", "pytest", "-q",
                        "test_ic_group_manager.py", "test_ic_live_core.py"],
                       cwd=dst, capture_output=True, text=True, timeout=600)
    tail = (p.stdout or p.stderr).strip().splitlines()[-1:]
    print(f"   IC suite: {tail[0] if tail else p.returncode}")
    if p.returncode != 0:
        print(p.stdout[-3000:])
        die("IC test suite failed on the patched files")


# ─────────────────────────────────────────────────────────────────────────────
def main():
    if not os.path.isdir("backend/app"):
        die("run from the repo root (the folder that contains backend/)")
    trees = [t for t in TREES if os.path.isdir(t)]
    print(f"[{FENCE}] trees: {', '.join(trees)}")
    if "desktop/src-tauri/backend/app" not in trees:
        print("   note: desktop/src-tauri/backend/app not present — backend tree only")

    staged, applied, total = {}, 0, 0
    for tree in trees:
        wpath = os.path.join(tree, F_WATCH)
        total += 1
        if os.path.exists(wpath):
            if WATCH_FENCE in open(wpath, encoding="utf-8").read():
                applied += 1
            else:
                die(f"{wpath} exists without the fence — refusing to overwrite")
        else:
            staged[wpath] = (None, WATCH_SRC)
        for rel in EDIT_FILES:
            total += 1
            path = os.path.join(tree, rel)
            if not os.path.isfile(path):
                die(f"missing file {path}")
            src = open(path, encoding="utf-8").read()
            if FENCE in src:
                applied += 1
                continue
            if PREREQ[rel] not in src:
                die(f"{path}: expected marker {PREREQ[rel]!r} missing — wrong working copy?")
            new = src
            for i, (old, rep) in enumerate(EDITS[rel], 1):
                n = new.count(old)
                if n != 1:
                    die(f"{path}: edit #{i} anchor found {n} times (need exactly 1)")
                new = new.replace(old, rep, 1)
            staged[path] = (src, new)

    if applied == total:
        print(f"[{FENCE}] already applied everywhere — nothing to do.")
        return
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--"] +
                             [f"backend/app/{f}" for f in EDIT_FILES],
                             capture_output=True, text=True, timeout=20).stdout
        dirty = [ln for ln in out.splitlines() if ln.strip()]
        if dirty:
            die("uncommitted changes on files this script edits:\n   " +
                "\n   ".join(dirty) + "\n   commit/push or revert them first")
    except FileNotFoundError:
        print("   note: git not found — skipping working-copy check")
    if applied:
        die(f"mixed state: fence present in {applied}/{total} targets")

    for path, (_, new) in staged.items():
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
            tf.write(new)
            tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"{path} does not compile:\n{e}")
        finally:
            os.unlink(tmp)
    print(f"[{FENCE}] py_compile OK on {len(staged)} file(s)")

    print(f"[{FENCE}] behavioural tests — broker-session watchdog")
    test_watch(WATCH_SRC)
    print(f"[{FENCE}] behavioural tests — IC no-price close")
    test_ic(staged, "backend/app")

    for path, (old, _) in staged.items():
        if old is not None:
            with open(f"{path}.bak-{FENCE}", "w", encoding="utf-8") as f:
                f.write(old)
    for path, (old, new) in staged.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"   {'created' if old is None else 'wrote'} {path}")
    print(f"\n[{FENCE}] DONE — {len(staged)} file(s), backups *.bak-{FENCE}.")
    print("Rebuild + restart. Tomorrow, if Kite is not logged in by 08:45 you")
    print("get a warning; by 09:10 a critical alert (bell + Telegram).")


if __name__ == "__main__":
    main()
