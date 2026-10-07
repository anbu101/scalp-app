# backend/app/backtest/vet/test_vet_dte_lots.py
#
# ── VET_DTE_LOTS_20261005 ── per-DTE lot multiplier on the VET_V1 runner,
# exercised end-to-end on a synthetic corpus on the REAL epoch clock (Jan–Mar
# 2024, Thursday era, real expiry calendar / symbol builder / CandleSource /
# corpus trading calendar). The decisive check is OFF ≡ ORIGINAL: with the
# apply script's backup (backtest_vet_runner.py.bak-VET_DTE_LOTS_20261005)
# next to the runner, the original runner must produce row-for-row and
# diag-for-diag the same output as the patched runner with the knob unset.
#
# DTE here = trading sessions from the ENTRY day to the expiry of the
# contract actually entered — the number the Sessions-to-Expiry breakdown
# (trading_calendar.attach_dte) shows for that trade. Rolls and entries
# bumped past roll_time land on the next series, so they read DTE 5.
#
# Run from backend/:  python app/backtest/vet/test_vet_dte_lots.py

from __future__ import annotations

import importlib.util
import math
import os
import random
import sqlite3
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3]))          # backend/

from app.backtest.engine.expiry_calendar import expected_expiry_for_day  # noqa: E402
from app.backtest.util.nifty_symbol import build_nifty_symbol            # noqa: E402
from app.backtest.vet import backtest_vet_runner as NEWR                 # noqa: E402
from app.backtest.repo import trading_calendar as TC                     # noqa: E402

FENCE = "VET_DTE_LOTS_20261005"
IST = 19800
_TZ = timezone(timedelta(seconds=IST))
LOT = 65
BASE_LOTS = 10
OK = [0]
FAILS = []


def check(name, cond):
    if cond:
        OK[0] += 1
        print("ok  ", name)
    else:
        FAILS.append(name)
        print("FAIL", name)


def _ds(d):
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST


def _N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs(call, S, K, tau, iv=0.13):
    if tau <= 0:
        return max(0.05, (S - K) if call else (K - S))
    sd = iv * math.sqrt(tau)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    c = S * _N(d1) - K * _N(d1 - sd)
    return max(0.05, c if call else c - S + K)


def build_corpus(path, sessions=60, seed=11):
    """Every weekday is a session. Each day carries the day's front weekly AND
    the next one, so rolls and post-roll-time entries have a real series."""
    schema = (HERE.parents[1] / "repo" / "schema.sql").read_text()
    c = sqlite3.connect(path)
    c.executescript(schema)
    rnd = random.Random(seed)
    S, d, n, drift, tok, rows = 21500.0, date(2024, 1, 1), 0, 0.0, {}, []
    ins = "INSERT OR REPLACE INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
    while n < sessions:
        if d.weekday() < 5:
            n += 1
            if n % 4 == 1:
                drift = rnd.choice([-1, 1]) * rnd.uniform(0.25, 0.6)
            if n > 5 and rnd.random() < 0.2:
                drift = -drift
            d0 = _ds(d)
            e1 = expected_expiry_for_day(d)
            e2 = expected_expiry_for_day(e1 + timedelta(days=1))
            atm = round(S / 50) * 50
            strikes = [atm + 50 * i for i in range(-12, 13)]
            for m in range(375):
                ts = d0 + (9 * 60 + 15 + m) * 60
                o = S
                S = S + drift + rnd.gauss(0, 2.2)
                rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", 0.0, "", o,
                             max(o, S) + 0.5, min(o, S) - 0.5, S, 0, 0))
                for exp in (e1, e2):
                    tau = max(0.0, (_ds(exp) + 15.5 * 3600 - ts) / (365 * 86400))
                    for K in strikes:
                        for typ in ("CE", "PE"):
                            sym = build_nifty_symbol(exp, K, typ)
                            t = tok.setdefault(sym, 1000 + len(tok))
                            po = _bs(typ == "CE", o, K, tau + 60 / (365 * 86400))
                            pc = _bs(typ == "CE", S, K, tau)
                            rows.append((t, ts, "NIFTY", sym, typ, float(K), exp.isoformat(),
                                         po, max(po, pc) * 1.002, min(po, pc) * 0.998, pc, 10, 10))
            if len(rows) > 400000:
                c.executemany(ins, rows)
                rows = []
        d += timedelta(days=1)
    c.executemany(ins, rows)
    c.commit()
    c.close()


CFG = {"timeframe_minutes": 5, "lots": BASE_LOTS, "underlying": "NIFTY",
       "rollover_enabled": True, "roll_time": "15:00"}


def run(mod, db, extra=None):
    cfg = dict(CFG)
    cfg.update(extra or {})
    return mod.run_vet_backtest(
        db_path=db, strategy_id="VET_V1", underlying="NIFTY",
        date_from=date(2024, 1, 22), date_to=date(2024, 3, 22), config_override=cfg)


def day_of(ts):
    return datetime.fromtimestamp(ts, _TZ).date()


_CAL = []


def dte_of(t):
    """corpus-calendar sessions from entry day to the contract's expiry — the
    exact definition the runner and the Sessions-to-Expiry breakdown use
    (a series expiring past the corpus end therefore reads fewer sessions)"""
    return TC.sessions_to_expiry(_CAL, day_of(t.entry_ts).isoformat(), str(t.expiry)[:10])


def rows(r):
    return [asdict(t) for t in r["trades"]]


def main_tests(db):
    _CAL[:] = TC.trading_dates("NIFTY", db)
    check("corpus calendar available", len(_CAL) >= 50)
    # ── 1. OFF ≡ ORIGINAL ────────────────────────────────────────────────
    base = run(NEWR, db)
    bak = HERE.parent / f"backtest_vet_runner.py.bak-{FENCE}"
    if bak.exists():
        tmp = HERE.parent / "_vet_orig_for_dte_test.py"
        tmp.write_text(bak.read_text())
        try:
            spec = importlib.util.spec_from_file_location("app.backtest.vet._vet_orig_for_dte_test", tmp)
            OLDR = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = OLDR      # dataclasses resolve via sys.modules
            spec.loader.exec_module(OLDR)
            for extra in ({}, {"dte_lot_mult": {}}, {"dte_lot_mult": ""},
                          {"eod_square": True}, {"leg_action": "SELL", "hedge_enabled": True},
                          {"sl_pct": 20, "tp_pct": 40, "max_daily_mtm_loss": 15000},
                          {"lot_comp_step_months": 1, "lot_comp_add_lots": 2}):
                a, b = run(OLDR, db, extra), run(NEWR, db, extra)
                check(f"OFF ≡ ORIGINAL rows {extra or 'baseline'}", rows(a) == rows(b) and len(a["trades"]) > 0)
                check(f"OFF ≡ ORIGINAL summary+diag {extra or 'baseline'}", a["summary"] == b["summary"])
                check(f"OFF ≡ ORIGINAL echoed config {extra or 'baseline'}", a["config"] == b["config"])
        finally:
            sys.modules.pop("app.backtest.vet._vet_orig_for_dte_test", None)
            tmp.unlink(missing_ok=True)
    else:
        print(f"note: {bak.name} not found — OFF≡ORIGINAL comparison skipped (the apply script runs it)")
    check("OFF: no dte diag keys, no echoed knob",
          not any(k in base["summary"]["diag_vet"] for k in
                  ("dte_lot_mult", "dte_scaled_entries", "dte_skipped_entries",
                   "dte_skipped_rolls", "dte_unknown_entries"))
          and "dte_lot_mult" not in base["config"])
    bt = base["trades"]
    dtes = {dte_of(t) for t in bt}
    check(f"fixture has entries at several DTEs incl. 0 and 5 (got {sorted(dtes)})",
          {0, 5} <= dtes and len(dtes) >= 4)
    check("fixture has at least one roll", base["summary"]["diag_vet"]["roll_entries"] >= 1)
    check("fixture has a position carried across days",
          any(day_of(t.exit_ts) > day_of(t.entry_ts) for t in bt))

    # ── 2. skip 0DTE ─────────────────────────────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": {"0": 0}})
    t2, dg = r["trades"], r["summary"]["diag_vet"]
    check("0:0 → no trade entered on a 0DTE contract", t2 and all(dte_of(t) != 0 for t in t2))
    check("0:0 → skipped entries counted, nothing scaled",
          dg["dte_skipped_entries"] >= 1 and dg["dte_scaled_entries"] == 0)
    check("0:0 → lots untouched on the other DTEs", all(t.qty == BASE_LOTS * LOT for t in t2))
    check("0:0 → echoed config + diag carry the knob",
          r["config"].get("dte_lot_mult") == {"0": 0.0} and dg["dte_lot_mult"] == {"0": 0.0})
    # a position opened BEFORE its expiry day and still held on that day is
    # managed exactly as before (exits / rolls are never gated by the knob)
    carried = [t for t in bt if dte_of(t) > 0 and day_of(t.exit_ts) == date.fromisoformat(str(t.expiry)[:10])]
    bmap = {(t.symbol, t.entry_ts): t for t in t2}
    same = [(u, bmap[(u.symbol, u.entry_ts)]) for u in carried if (u.symbol, u.entry_ts) in bmap]
    # (the entry tag may differ — a position re-synced at the roll minute after
    # a skipped 0DTE entry reads VET·RESUME where the baseline rolled — so the
    # comparison is on the economics: same exit, price, qty, P&L)
    _ex = lambda t: {k: v for k, v in asdict(t).items() if k != "condition"}
    check("0:0 → a contract carried INTO its expiry day exits there exactly as before",
          same and all(_ex(u) == _ex(v) for u, v in same))

    # ── 3. scale DTE 5 (rolls + post-roll-time entries) ×2 ───────────────
    r = run(NEWR, db, {"dte_lot_mult": "5:2"})
    t3, dg = r["trades"], r["summary"]["diag_vet"]
    five = [t for t in t3 if dte_of(t) == 5]
    check("5:2 → DTE-5 positions at 20 lots, all others 10",
          five and all(t.qty == 2 * BASE_LOTS * LOT for t in five)
          and all(t.qty == BASE_LOTS * LOT for t in t3 if dte_of(t) != 5))
    check("5:2 → rolled positions are sized by the NEW contract's DTE",
          any(t.condition == "VET·ROLL" and t.qty == 2 * BASE_LOTS * LOT for t in t3))
    bmap = {(t.symbol, t.entry_ts): t for t in bt}
    pairs = [(t, bmap[(t.symbol, t.entry_ts)]) for t in five if (t.symbol, t.entry_ts) in bmap]
    check("5:2 → same trade path as baseline (scaling never changes entries/exits)",
          [(t.symbol, t.entry_ts, t.exit_ts) for t in t3] == [(t.symbol, t.entry_ts, t.exit_ts) for t in bt])
    check("5:2 → gross P&L of a DTE-5 trade is exactly 2× the baseline trade",
          pairs and all(abs(t.pnl - 2 * u.pnl) < 0.02 for t, u in pairs))
    check("5:2 → scaled entries counted, nothing skipped",
          dg["dte_scaled_entries"] == len(five) and dg["dte_skipped_entries"] == 0 and dg["dte_skipped_rolls"] == 0)
    check("string form 'dte:mult' parsed", dg["dte_lot_mult"] == {"5": 2.0})

    # ── 4. skip DTE 5 → rolls are NOT taken (flat at roll time), counted ─
    r = run(NEWR, db, {"dte_lot_mult": {"5": 0}})
    t4, dg = r["trades"], r["summary"]["diag_vet"]
    check("5:0 → no DTE-5 contract ever held", t4 and all(dte_of(t) != 5 for t in t4))
    check("5:0 → skipped rolls counted; any roll taken landed on a non-5 DTE",
          dg["dte_skipped_rolls"] >= 1
          and dg["roll_entries"] == sum(1 for t in t4 if t.condition == "VET·ROLL")
          and all(dte_of(t) != 5 for t in t4 if t.condition == "VET·ROLL"))
    check("5:0 → fewer rolls than baseline",
          dg["roll_entries"] < base["summary"]["diag_vet"]["roll_entries"])

    # ── 5. fractional multiplier & rounding ──────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": {"1": 1.5, "2": 0.25}})
    t5 = r["trades"]
    check("1:1.5 → 15 lots; 2:0.25 → max(1, round(2.5)) = 2 lots (never 0 unless explicitly 0)",
          all(t.qty == 15 * LOT for t in t5 if dte_of(t) == 1)
          and all(t.qty == 2 * LOT for t in t5 if dte_of(t) == 2)
          and any(dte_of(t) in (1, 2) for t in t5))

    # ── 6. composes with lot compounding (DTE first, then the comp ratio) ─
    comp = {"lot_comp_step_months": 1, "lot_comp_add_lots": 10}
    a = run(NEWR, db, comp)
    b = run(NEWR, db, dict(comp, dte_lot_mult="5:2"))
    am = {(t.symbol, t.entry_ts): t for t in a["trades"]}
    ok6 = True
    for t in b["trades"]:
        u = am.get((t.symbol, t.entry_ts))
        if u is None:
            ok6 = False
            break
        want = u.qty if dte_of(t) != 5 else None
        if want is not None and t.qty != want:
            ok6 = False
        if dte_of(t) == 5:
            # comp ratio applied to the DTE-scaled lots: round(20 × lots/10)
            r_ = (u.qty // LOT) / BASE_LOTS
            if t.qty != max(1, int(round(2 * BASE_LOTS * r_))) * LOT:
                ok6 = False
    check("compounding × DTE: non-DTE-5 trades keep the comp qty, DTE-5 = comp ratio × 20 lots",
          ok6 and len({t.qty for t in b["trades"]}) >= 3)

    # ── 7. calendar unavailable → fail-open at base lots, counted ────────
    orig_td = TC.trading_dates
    TC.trading_dates = lambda *a, **k: []
    try:
        r = run(NEWR, db, {"dte_lot_mult": {"0": 0, "5": 2}})
    finally:
        TC.trading_dates = orig_td
    dg = r["summary"]["diag_vet"]
    check("empty calendar → base lots everywhere, unknown entries counted, no skips",
          rows(r) == rows(base) and dg["dte_unknown_entries"] >= 1
          and dg["dte_skipped_entries"] == 0 and dg["dte_skipped_rolls"] == 0)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "bt.db")
        print("building synthetic corpus…")
        build_corpus(dbp)
        main_tests(dbp)
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} VET_V1 DTE-LOTS CHECKS PASSED")
