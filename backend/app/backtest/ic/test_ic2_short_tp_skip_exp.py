# backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py — fence IC2_SHORT_TP_SKIP_EXP_20261015
#
# IC_V1/IC_V2 backtest: "Short-leg TP: skip expiry day" (config key
# short_tp_skip_expiry). On the contract's own expiry day the SELL legs'
# tp_val is zeroed before the session runs; every other day is untouched.
#
#   1. _short_tp_off (pure)            only SELL legs with TP>0, copies, no mutation
#   2. OFF ≡ ORIGINAL runner (.bak)    key absent / False → row-for-row, same diag keys
#   3. ON with no short TP             no-op ≡ ORIGINAL; 0 days counted
#   4. ON with short TP (NEXT_OPEN + adjustments, the sealed shape):
#        no short ENTERED on an expiry day exits TP (the plain-TP run has some)
#        legs entered on non-expiry days ≡ plain-TP run row-for-row (incl. ·ADJ)
#        legs entered on expiry days    ≡ no-TP run row-for-row   (incl. ·ADJ)
#        adjustment-leg TP still fires on expiry day (only SELL legs touched)
#        diag counts the expiry days it acted on
#   5. EOD mode (IC_V1 shape): same split + parallel ×2 ≡ serial
#
# Synthetic corpus in a temp dir; never touches backtest.db.
# Run: cd backend && python3 app/backtest/ic/test_ic2_short_tp_skip_exp.py

from __future__ import annotations

import atexit
import importlib.util
import os
import random
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[3])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

FENCE = "IC2_SHORT_TP_SKIP_EXP_20261015"
FAILS: list = []
OK = [0]
IST = 5 * 3600 + 30 * 60
OPEN_M = 9 * 60 + 15


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


DDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""


def _ds(d):
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST


def _day(ts):
    return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST)).date()


def build_corpus(path, days, seed=23):
    """Spot random walk with a per-day drift (quiet / trending days) and a
    weekly chain: premium = intrinsic + decaying time value, so shorts can
    reach a TP, an SL, or decay quietly into the close."""
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    random.seed(seed)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    rows, spot = [], 24000.0
    for d in days:
        ds = _ds(d)
        exp = expected_expiry_for_day(d).isoformat()
        atm = int(round(spot / 50.0)) * 50
        drift = random.choice((0.0, 0.0, 1, -1))
        drift = drift * random.uniform(0.00015, 0.0004) if drift else 0.0
        ms = []
        for m in range(OPEN_M, 15 * 60 + 30):
            o = spot
            c = spot * (1 + drift + random.gauss(0, 0.0003))
            ts = ds + m * 60
            rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", None, None, o, max(o, c), min(o, c), c, 0, 0))
            ms.append((ts, o, c, m))
            spot = c
        for K in [atm + 50 * k for k in range(-12, 13)]:
            for side in ("CE", "PE"):
                sym = f"NIFTY{exp.replace('-', '')}{K}{side}"
                for (ts, o, c, m) in ms:
                    tv = 60.0 * (1 - (m - OPEN_M) / 700.0)
                    f = (lambda s: round(max(s - K, 0) + tv, 2)) if side == "CE" else (lambda s: round(max(K - s, 0) + tv, 2))
                    a, b = f(o), f(c)
                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp, a, max(a, b), min(a, b), b, 100, 100))
    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def legs(short_tp=0.0):
    return [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 10, "premium_max": 85,
             "sl_val": 42, "sl_mode": "pct", "tp_val": short_tp, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L2"},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 10, "premium_max": 85,
             "sl_val": 42, "sl_mode": "pct", "tp_val": short_tp, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L1"},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 10, "premium_max": 70},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 10, "premium_max": 70}]


ADJ = {"L1": {"enabled": True, "lots": 10, "premium_max": 85, "sl_val": 25, "sl_mode": "pct",
              "tp_val": 20, "tp_mode": "pct"},
       "L2": {"enabled": True, "lots": 10, "premium_max": 85, "sl_val": 25, "sl_mode": "pct",
              "tp_val": 20, "tp_mode": "pct"}}


def key(t):
    return (t.entry_ts, t.exit_ts, t.condition, t.direction, t.qty, t.tradingsymbol,
            round(t.entry_price, 4), round(t.exit_price or 0, 4), t.exit_reason)


def t_helper():
    print("── 1. _short_tp_off ──")
    from app.backtest.ic.backtest_ic_runner import _short_tp_off
    from app.backtest.ic.ic_v1_engine import norm_leg
    src = [norm_leg(l) for l in legs(80)]
    snap = [dict(l) for l in src]
    out, n = _short_tp_off(src)
    check("2 SELL legs switched off", n == 2, n)
    check("SELL legs tp_val → 0", all(l["tp_val"] == 0 for l in out if l["action"] == "SELL"))
    check("BUY legs untouched (same objects)", all(o is s for o, s in zip(out, src) if s["action"] == "BUY"))
    check("SELL legs are copies; input list not mutated", src == snap and all(o is not s for o, s in zip(out, src) if s["action"] == "SELL"))
    check("everything else on the SELL legs unchanged",
          all({k: v for k, v in o.items() if k != "tp_val"} == {k: v for k, v in s.items() if k != "tp_val"}
              for o, s in zip(out, src)))
    out0, n0 = _short_tp_off([norm_leg(l) for l in legs(0)])
    check("no short TP → nothing counted", n0 == 0)


def t_runner():
    print("── 2–5. runner (synthetic corpus) ──")
    from app.utils.market_hours import is_trading_day
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    import app.backtest.data.candle_source as _cs
    from app.backtest.ic.backtest_ic_runner import run_ic_backtest as NEW

    bak = HERE.parent / f"backtest_ic_runner.py.bak-{FENCE}"
    ORIG = None
    if bak.exists():
        from importlib.machinery import SourceFileLoader
        ldr = SourceFileLoader("ic_runner_orig", str(bak))
        spec = importlib.util.spec_from_loader("ic_runner_orig", ldr)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ic_runner_orig"] = mod      # dataclasses resolve the module by name
        ldr.exec_module(mod)
        ORIG = mod.run_ic_backtest

    td = tempfile.mkdtemp()
    atexit.register(lambda: shutil.rmtree(td, ignore_errors=True))
    dbp = os.path.join(td, "bt.db")
    days, d = [], date(2026, 1, 12)
    while len(days) < 34:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    build_corpus(dbp, days)
    _cs._backtest_db_path = lambda: Path(dbp)
    D0, D1 = days[1], days[-1]
    exp_days = {x for x in days if expected_expiry_for_day(x) == x and D0 <= x <= D1}

    def run(fn, cfg, sid="IC_V2"):
        r = fn(db_path=dbp, strategy_id=sid, underlying="NIFTY", date_from=D0, date_to=D1,
               config_override=cfg)
        assert not r.get("aborted"), r.get("reason")
        return r

    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",
          "expiry_exit_time": "15:15", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ}
    V1 = {"entry_time": "09:18", "exit_time": "15:28"}

    def K(r, pred=lambda t: True):
        return sorted(key(t) for t in r["trades"] if pred(t))

    on_exp = lambda t: _day(t.entry_ts) in exp_days
    short = lambda t: t.direction == "SELL" and "ADJ" not in t.condition
    diag = lambda r: r["summary"].get("diag_ic", {})

    for mode, BASE in (("NEXT_OPEN", V2), ("EOD", V1)):
        sid = "IC_V2" if mode == "NEXT_OPEN" else "IC_V1"
        tp = run(NEW, {**BASE, "legs": legs(30)}, sid)
        notp = run(NEW, {**BASE, "legs": legs(0)}, sid)
        skip = run(NEW, {**BASE, "legs": legs(30), "short_tp_skip_expiry": True}, sid)
        ent_exp = {_day(t.entry_ts) for t in tp["trades"] if on_exp(t)}
        n_tp_exp = sum(1 for t in tp["trades"] if on_exp(t) and short(t) and t.exit_reason == "TP")
        n_tp_other = sum(1 for t in tp["trades"] if not on_exp(t) and short(t) and t.exit_reason == "TP")
        print(f"   [{mode}] expiry days entered {len(ent_exp)}; plain-TP run: {n_tp_exp} short TPs on expiry days, {n_tp_other} elsewhere")
        check(f"[{mode}] corpus exercises it: short TPs on expiry AND non-expiry days", n_tp_exp >= 1 and n_tp_other >= 3)
        check(f"[{mode}] ON: no short entered on an expiry day exits TP",
              not any(on_exp(t) and short(t) and t.exit_reason == "TP" for t in skip["trades"]))
        check(f"[{mode}] ON: non-expiry-day legs ≡ plain-TP run (row-for-row)",
              K(skip, lambda t: not on_exp(t)) == K(tp, lambda t: not on_exp(t)))
        check(f"[{mode}] ON: expiry-day legs ≡ no-TP run (row-for-row)",
              K(skip, on_exp) == K(notp, on_exp))
        check(f"[{mode}] ON: run actually differs from plain TP", K(skip) != K(tp))
        check(f"[{mode}] diag counts {len(ent_exp)} expiry days switched off",
              diag(skip).get("short_tp_expiry_days_off") == len(ent_exp) and diag(skip).get("short_tp_skip_expiry") is True,
              (diag(skip).get("short_tp_expiry_days_off"), len(ent_exp)))
        check(f"[{mode}] OFF: no new diag keys", "short_tp_skip_expiry" not in diag(tp)
              and "short_tp_expiry_days_off" not in diag(tp))
        z = run(NEW, {**BASE, "legs": legs(0), "short_tp_skip_expiry": True}, sid)
        check(f"[{mode}] ON with no short TP ≡ no-TP run, 0 days counted",
              K(z) == K(notp) and diag(z).get("short_tp_expiry_days_off") == 0)
        if mode == "NEXT_OPEN":
            adj_tp_exp = [t for t in skip["trades"] if on_exp(t) and "ADJ" in t.condition and t.exit_reason == "TP"]
            check("[NEXT_OPEN] adjustment-leg TP still fires on expiry day", len(adj_tp_exp) >= 1, len(adj_tp_exp))
        if ORIG:
            o = run(ORIG, {**BASE, "legs": legs(30)}, sid)
            check(f"[{mode}] key absent ≡ ORIGINAL runner (row-for-row)", K(o) == K(tp))
            check(f"[{mode}] key absent: diag keys identical to ORIGINAL", set(diag(o)) == set(diag(tp)))
            f = run(NEW, {**BASE, "legs": legs(30), "short_tp_skip_expiry": False}, sid)
            check(f"[{mode}] key False ≡ ORIGINAL", K(f) == K(o))
        else:
            print("  ! .bak not found — ORIGINAL comparison skipped")
        if mode == "EOD":
            p = run(NEW, {**BASE, "legs": legs(30), "short_tp_skip_expiry": True, "parallel_workers": 2}, sid)
            check("[EOD] parallel ×2 ≡ serial", K(p) == K(skip))
            check("[EOD] parallel diag carries the flag + summed count",
                  diag(p).get("short_tp_skip_expiry") is True
                  and diag(p).get("short_tp_expiry_days_off") == diag(skip).get("short_tp_expiry_days_off"),
                  (diag(p).get("short_tp_skip_expiry"), diag(p).get("short_tp_expiry_days_off")))


if __name__ == "__main__":
    t_helper()
    t_runner()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} IC SHORT-TP SKIP-EXPIRY CHECKS PASSED")
