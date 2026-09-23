# backend/app/backtest/tma/test_tma_v2_dte_lots.py
#
# ── TMA2_DTE_LOTS_20260922 ── per-DTE lot multiplier on the TMA_V2 runner,
# exercised end-to-end on a synthetic corpus on the REAL epoch clock (Jan–Mar
# 2024, Thursday era, real expiry calendar / symbol builder / CandleSource).
# The decisive check is OFF ≡ ORIGINAL: with the apply script's backup
# (backtest_tma_v2_runner.py.bak-TMA2_DTE_LOTS_20260922) next to the runner,
# the original runner must produce row-for-row and diag-for-diag the same
# output as the patched runner with the knob unset.
#
# Run from backend/:  python app/backtest/tma/test_tma_v2_dte_lots.py

from __future__ import annotations

import importlib.util
import json
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
from app.backtest.tma import backtest_tma_v2_runner as NEWR              # noqa: E402
from app.backtest.repo import trading_calendar as TC                     # noqa: E402

FENCE = "TMA2_DTE_LOTS_20260922"
IST = 19800
_TZ = timezone(timedelta(seconds=IST))
LOT = 65
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


def build_corpus(path, sessions=45, seed=7):
    schema = (HERE.parents[1] / "repo" / "schema.sql").read_text()
    c = sqlite3.connect(path)
    c.executescript(schema)
    rnd = random.Random(seed)
    S, d, n, drift, tok, rows = 21500.0, date(2024, 1, 1), 0, 0.0, {}, []
    ins = "INSERT OR REPLACE INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
    while n < sessions:
        if d.weekday() < 5:
            n += 1
            if n % 6 == 1:
                drift = rnd.choice([-1, 1]) * rnd.uniform(0.25, 0.6)
            if n > 5 and rnd.random() < 0.15:
                drift = -drift
            d0, exp = _ds(d), expected_expiry_for_day(d)
            atm = round(S / 50) * 50
            strikes = [atm + 50 * i for i in range(-16, 17)]
            for m in range(375):
                ts = d0 + (9 * 60 + 15 + m) * 60
                o = S
                S = S + drift + rnd.gauss(0, 2.2)
                rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", 0.0, "", o,
                             max(o, S) + 0.5, min(o, S) - 0.5, S, 0, 0))
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


CFG = {"mode": "SELL", "trade_mode": "POSITIONAL", "xover_exit_ref": 55,
       "max_extension_pct": 0.8, "ema144_slope_gate": True,
       "s1": {"main": {"premium_max": 200, "lots": 10, "sl_pct": 12, "tp_pct": 10,
                       "sl_unit": "PCT", "tp_unit": "ABS"},
              "hedge": {"premium_max": 5, "lots": 10}}}


def run(mod, db, extra=None):
    cfg = json.loads(json.dumps(CFG))
    cfg.update(extra or {})
    return mod.run_tma_v2_backtest(
        db_path=db, strategy_id="TMA_V2", underlying="NIFTY",
        date_from=date(2024, 1, 1), date_to=date(2024, 3, 31), config_override=cfg)


def mains(r):
    return [t for t in r["trades"] if t.direction == "SELL"]


def hedges(r):
    return [t for t in r["trades"] if t.direction != "SELL"]


def day_of(ts):
    return datetime.fromtimestamp(ts, _TZ).date()


def dte_of(t):
    """weekday sessions from entry day to the contract's expiry (the synthetic
    corpus has every weekday, so this equals the corpus-calendar DTE)"""
    d, x, n = day_of(t.entry_ts), date.fromisoformat(str(t.expiry)[:10]), 0
    while d < x:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def main_tests(db):
    # ── 1. OFF ≡ ORIGINAL ────────────────────────────────────────────────
    base = run(NEWR, db)
    bak = HERE.parent / f"backtest_tma_v2_runner.py.bak-{FENCE}"
    if bak.exists():
        tmp = HERE.parent / "_tma2_orig_for_dte_test.py"
        tmp.write_text(bak.read_text())
        try:
            spec = importlib.util.spec_from_file_location("app.backtest.tma._tma2_orig_for_dte_test", tmp)
            OLDR = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(OLDR)
            for extra in ({}, {"dte_lot_mult": {}}, {"dte_lot_mult": ""},
                          {"trade_mode": "INTRADAY"}, {"mode": "BUY"},
                          {"max_loss_per_trade": 5000, "sl_streak_count": 2}):
                a, b = run(OLDR, db, extra), run(NEWR, db, extra)
                check(f"OFF ≡ ORIGINAL rows {extra or 'sealed-shape'}",
                      [asdict(t) for t in a["trades"]] == [asdict(t) for t in b["trades"]] and len(a["trades"]) > 0)
                check(f"OFF ≡ ORIGINAL summary+diag {extra or 'sealed-shape'}", a["summary"] == b["summary"])
        finally:
            tmp.unlink(missing_ok=True)
    else:
        print(f"note: {bak.name} not found — OFF≡ORIGINAL comparison skipped (the apply script runs it)")
    check("OFF: no dte diag keys", not any(k.startswith("dte") or k == "skipped_dte" for k in base["summary"]["diag_tma2"]))
    bm = mains(base)
    dtes = {dte_of(t) for t in bm}
    check("fixture has entries at several DTEs incl. 0 and 4", {0, 4} <= dtes and len(dtes) >= 4)
    check("fixture has a position carried INTO an expiry day",
          any(day_of(t.exit_ts) == date.fromisoformat(str(t.expiry)[:10]) > day_of(t.entry_ts) for t in bm))

    # ── 2. skip 0DTE ─────────────────────────────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": {"0": 0}})
    m, dg = mains(r), r["summary"]["diag_tma2"]
    check("0:0 → no entries on expiry day", m and all(dte_of(t) != 0 for t in m))
    check("0:0 → skipped days + signals counted", dg["dte_skipped_days"] >= 1 and dg["skipped_dte"] >= 1 and dg["dte_scaled_days"] == 0)
    carried = [t for t in bm if day_of(t.exit_ts) == date.fromisoformat(str(t.expiry)[:10]) > day_of(t.entry_ts)]
    same = [t for t in m if any(u.tradingsymbol == t.tradingsymbol and u.entry_ts == t.entry_ts for u in carried)]
    check("0:0 → a position carried into expiry day still exits there exactly as before",
          same and all(asdict(t) == asdict(u) for t in same for u in carried
                       if u.tradingsymbol == t.tradingsymbol and u.entry_ts == t.entry_ts))
    check("0:0 → lots untouched on the other days", all(t.qty == 10 * LOT for t in m))

    # ── 3. scale 4DTE ×2 ─────────────────────────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": "4:2"})
    m, h, dg = mains(r), hedges(r), r["summary"]["diag_tma2"]
    four = [t for t in m if dte_of(t) == 4]
    check("4:2 → 4DTE mains at 20 lots (1300 qty), others 10",
          four and all(t.qty == 20 * LOT for t in four) and all(t.qty == 10 * LOT for t in m if dte_of(t) != 4))
    hmap = {(hh.entry_ts): hh for hh in h}
    check("4:2 → hedge scales with the sold leg", all(hmap[t.entry_ts].qty == 20 * LOT for t in four if t.entry_ts in hmap))
    bmap = {(t.tradingsymbol, t.entry_ts): t for t in bm}
    pairs = [(t, bmap[(t.tradingsymbol, t.entry_ts)]) for t in four if (t.tradingsymbol, t.entry_ts) in bmap]
    check("4:2 → gross P&L of a 4DTE trade is exactly 2× the baseline trade",
          pairs and all(abs(t.pnl - 2 * u.pnl) < 0.02 for t, u in pairs))
    check("4:2 → scaled days counted, nothing skipped", dg["dte_scaled_days"] >= 1 and dg["dte_skipped_days"] == 0)
    check("string form 'dte:mult' parsed", dg["dte_lot_mult"] == {"4": 2.0})

    # ── 4. fractional multiplier & rounding ──────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": {"1": 1.5, "2": 0.25}})
    m = mains(r)
    check("1:1.5 → 15 lots; 2:0.25 → max(1, round(2.5)) = 2 lots (never 0 unless explicitly 0)",
          all(t.qty == 15 * LOT for t in m if dte_of(t) == 1) and all(t.qty == 2 * LOT for t in m if dte_of(t) == 2)
          and any(dte_of(t) in (1, 2) for t in m))

    # ── 5. MAX_LOSS cap uses the day's lots ──────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": "4:2", "max_loss_per_trade": 20000})
    four = [t for t in mains(r) if dte_of(t) == 4]
    check("cap/trade level derived from the SCALED qty (sl ≤ entry + 20000/1300)",
          four and all(t.sl is None or t.sl <= t.entry_price + 20000 / (20 * LOT) + 1e-6 for t in four))

    # ── 6. calendar unavailable → fail-open at base lots, counted ────────
    orig_td = TC.trading_dates
    TC.trading_dates = lambda *a, **k: []
    try:
        r = run(NEWR, db, {"dte_lot_mult": {"0": 0, "4": 2}})
    finally:
        TC.trading_dates = orig_td
    m, dg = mains(r), r["summary"]["diag_tma2"]
    check("empty calendar → base lots everywhere, unknown days counted, no skips",
          all(t.qty == 10 * LOT for t in m) and dg["dte_unknown_days"] >= 1 and dg["dte_skipped_days"] == 0
          and [asdict(t) for t in r["trades"]] == [asdict(t) for t in base["trades"]])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "bt.db")
        print("building synthetic corpus…")
        build_corpus(dbp)
        main_tests(dbp)
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} TMA_V2 DTE-LOTS CHECKS PASSED")
