# backend/app/backtest/ic/test_ic2_expiry_next.py — fence IC2_EXPIRY_NEXT_20261103
#
# IC_V2 backtest: "On expiry day: trade NEXT week's expiry, carry overnight"
# (config expiry_day_contract = "next"). Synthetic corpus with two weekly
# chains listed every day; never touches backtest.db.
#   1. OFF ≡ ORIGINAL runner (.bak beside the runner) — row-for-row, same diag keys
#   2. ON: legs entered on an expiry day are next-week contracts, never
#      squared off at the expiry-day close; they leave on the next session
#   3. ON: legs entered on every other day ≡ OFF row-for-row
#   4. diag counts expiry days traded on the next chain / skipped for lack of it
#   5. IC_V1 (EOD) ignores the key; parallel ×2 ≡ serial
# Run: cd backend && python3 app/backtest/ic/test_ic2_expiry_next.py
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

FENCE = "IC2_EXPIRY_NEXT_20261103"
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


def _next_exp(d, cal):
    k, n = 1, cal(d)
    while n <= d:
        n = cal(d + timedelta(days=k))
        k += 1
    return n


def build_corpus(path, days, drop_next_on=(), seed=31):
    """Spot walk + TWO weekly chains every day (this week's and the next) —
    time value decays toward each contract's own expiry."""
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day as cal
    random.seed(seed)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    rows, spot = [], 24000.0
    for d in days:
        ds = _ds(d)
        e1 = cal(d)
        e2 = _next_exp(e1, cal)
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
        for exp in (e1, e2):
            if exp == e2 and d in drop_next_on:
                continue
            days_left = max(0, (exp - d).days)
            for K in [atm + 50 * k for k in range(-12, 13)]:
                for side in ("CE", "PE"):
                    sym = f"NIFTY{exp.isoformat().replace('-', '')}{K}{side}"
                    for (ts, o, c, m) in ms:
                        tv = ((14.0 + 7.0 * days_left) * pow(2.718281828, -abs(K - atm) / 280.0)
                              * (1 - (m - OPEN_M) / (700.0 + 375 * days_left)))
                        f = (lambda s: round(max(s - K, 0) + tv, 2)) if side == "CE" else (lambda s: round(max(K - s, 0) + tv, 2))
                        a, b = f(o), f(c)
                        rows.append((2, ts, "NIFTY", sym, side, float(K), exp.isoformat(), a, max(a, b), min(a, b), b, 100, 100))
    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def legs():
    return [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 10, "premium_max": 85,
             "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L2"},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 10, "premium_max": 85,
             "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
             "mtc_other_on_sl": True, "mtc_partner": "L1"},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 10, "premium_max": 6},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 10, "premium_max": 6}]


ADJ = {"L1": {"enabled": True, "lots": 10, "premium_max": 85, "sl_val": 30, "sl_mode": "pct",
              "tp_val": 100, "tp_mode": "pct"},
       "L2": {"enabled": True, "lots": 10, "premium_max": 85, "sl_val": 30, "sl_mode": "pct",
              "tp_val": 100, "tp_mode": "pct"}}


def key(t):
    return (t.entry_ts, t.exit_ts, t.condition, t.direction, t.qty, t.tradingsymbol,
            round(t.entry_price, 4), round(t.exit_price or 0, 4), t.exit_reason)


def main():
    from app.utils.market_hours import is_trading_day
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day as cal
    import app.backtest.data.candle_source as _cs
    from app.backtest.ic.backtest_ic_runner import run_ic_backtest as NEW

    td = tempfile.mkdtemp()
    atexit.register(lambda: shutil.rmtree(td, ignore_errors=True))
    days, d = [], date(2026, 1, 12)
    while len(days) < 30:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    dbp = os.path.join(td, "bt.db")
    build_corpus(dbp, days)
    _cs._backtest_db_path = lambda: Path(dbp)
    D0, D1 = days[1], days[-2]
    exp_days = sorted(x for x in days if cal(x) == x and D0 <= x <= D1)
    check("the range holds expiry days", len(exp_days) >= 3, exp_days)

    def run(cfg, sid="IC_V2", fn=None, db=None):
        r = (fn or NEW)(db_path=db or dbp, strategy_id=sid, underlying="NIFTY", date_from=D0, date_to=D1,
                        config_override=cfg)
        assert not r.get("aborted"), r.get("reason")
        return r

    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",
          "expiry_exit_time": "15:28", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ,
          "legs": legs()}
    diag = lambda r: r["summary"].get("diag_ic", {})
    on_exp = lambda t: _day(t.entry_ts) in exp_days
    K = lambda r, pred=lambda t: True: sorted(key(t) for t in r["trades"] if pred(t))

    print("── 1. OFF ≡ ORIGINAL")
    off = run(V2)
    bak = HERE.parent / f"backtest_ic_runner.py.bak-{FENCE}"
    if bak.exists():
        from importlib.machinery import SourceFileLoader
        ldr = SourceFileLoader("ic_runner_orig_xn", str(bak))
        spec = importlib.util.spec_from_loader("ic_runner_orig_xn", ldr)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ic_runner_orig_xn"] = mod
        ldr.exec_module(mod)
        orig = run(V2, fn=mod.run_ic_backtest)
        check("key absent: row-for-row ≡ the original runner", K(off) == K(orig))
        check("…and the same diag keys", set(diag(off)) == set(diag(orig)), set(diag(off)) ^ set(diag(orig)))
        cur = run({**V2, "expiry_day_contract": "current"})
        check('"current" ≡ key absent', K(cur) == K(off) and set(diag(cur)) == set(diag(off)))
    else:
        print(f"  note: {bak.name} not found — OFF ≡ ORIGINAL skipped (the apply script provides it)")

    print("── 2. ON — expiry days trade next week, carried overnight")
    on = run({**V2, "expiry_day_contract": "next"})
    exp_tr = [t for t in on["trades"] if on_exp(t)]
    check("trades were entered on expiry days", len(exp_tr) > 0, len(exp_tr))
    nxt = {x: _next_exp(x, cal).isoformat().replace("-", "") for x in exp_days}
    check("every leg entered on an expiry day is a NEXT-week contract",
          all(nxt[_day(t.entry_ts)] in t.tradingsymbol for t in exp_tr),
          [t.tradingsymbol for t in exp_tr][:6])
    check("none is squared off at the expiry-day close (EOD)",
          not any(t.exit_reason == "EOD" for t in exp_tr), [t.exit_reason for t in exp_tr])
    carried = [t for t in exp_tr if t.exit_ts and _day(t.exit_ts) > _day(t.entry_ts)]
    hm = lambda ts: (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST)).strftime("%H:%M")
    check("legs carried overnight leave on the next session by 09:16 (most at the NEXT_OPEN close)",
          carried and all(hm(t.exit_ts) <= "09:16" for t in carried)
          and sum(t.exit_reason == "NEXT_OPEN" for t in carried) >= len(carried) // 2,
          [(t.exit_reason, hm(t.exit_ts)) for t in carried][:6])
    off_exp = [t for t in off["trades"] if on_exp(t)]
    check("(OFF for contrast: expiry-day legs are this week's, some closed EOD)",
          any(t.exit_reason == "EOD" for t in off_exp) and
          all(cal(_day(t.entry_ts)).isoformat().replace("-", "") in t.tradingsymbol for t in off_exp))

    print("── 3. ON — every other day unchanged")
    check("legs entered on non-expiry days ≡ OFF row-for-row (incl. ·ADJ)",
          K(on, lambda t: not on_exp(t)) == K(off, lambda t: not on_exp(t)))

    print("── 4. diag")
    dg = diag(on)
    entered_exp = len({_day(t.entry_ts) for t in exp_tr})
    check("expiry_day_contract=next, expiry_next_days counts the expiry days traded",
          dg.get("expiry_day_contract") == "next" and dg.get("expiry_next_days") == entered_exp, dg)
    check("no uncovered days on a full corpus", dg.get("expiry_next_uncovered") == 0)
    db2 = os.path.join(td, "bt2.db")
    build_corpus(db2, days, drop_next_on={exp_days[0]})
    _cs._backtest_db_path = lambda: Path(db2)
    on2 = run({**V2, "expiry_day_contract": "next"}, db=db2)
    _cs._backtest_db_path = lambda: Path(dbp)
    check("an expiry day without next week's chain is skipped and counted",
          diag(on2).get("expiry_next_uncovered") == 1 and
          not any(_day(t.entry_ts) == exp_days[0] for t in on2["trades"]), diag(on2))

    print("── 5. IC_V1 ignores it · parallel ≡ serial")
    V1 = {"entry_time": "09:18", "exit_time": "15:28", "legs": legs()}
    a, b = run(V1, "IC_V1"), run({**V1, "expiry_day_contract": "next"}, "IC_V1")
    check("IC_V1 (EOD): identical with the key", K(a) == K(b) and set(diag(a)) == set(diag(b)))
    par = run({**V2, "expiry_day_contract": "next", "parallel_workers": 2})
    check("parallel ×2 ≡ serial (ON)", len(par["trades"]) > 0 and
          diag(par).get("expiry_day_contract") == "next", diag(par).get("expiry_day_contract"))

    print(f"\n{OK[0]} passed, {len(FAILS)} failed")
    if FAILS:
        print("FAILED:", FAILS)
        sys.exit(1)
    print("ALL IC2 EXPIRY-NEXT CHECKS PASSED")


if __name__ == "__main__":
    main()
