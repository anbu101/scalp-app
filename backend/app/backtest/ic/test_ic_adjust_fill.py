# backend/app/backtest/ic/test_ic_adjust_fill.py — fence IC_ADJ_FILL_OPEN_20261101
#
# IC_V2 adjustment buy price (config key adjust_fill):
#   "open"  (default) strike pick + fill at the OPEN of the activation minute,
#           that minute's candle is watched (the fill came first)
#   "close" pick + fill at that candle's CLOSE, watched from the next candle —
#           exactly the runner before 1 Nov 2026
#
#   engine  1. spike candle stops the short IN that minute at exactly the SL
#              (high ≥ SL, close below it) — the existing intrabar check
#           2. the adjustment follows from that minute (+ adjust_delay_s)
#           3. open → fill = open; close → fill = close; engine default = close
#           4. open watches the activation candle: its low hits the ADJ SL →
#              stopped in that minute AT the SL; close mode is not
#           5. target mirror: activation-candle high hits the ADJ TP → TP
#           6. synthetic adjustment (fill_price, no candles) identical in both
#   runner  7. key absent ≡ "open" (row-for-row)
#           8. "close" ≡ ORIGINAL runner (.bak), diag keys identical
#           9. open vs close: shorts + wings identical, only ·ADJ legs move
#          10. every ·ADJ entry = open (resp. close) of its own candle, and the
#              strike is the highest such price ≤ cap at that minute
#          11. diag adjust_fill only in open mode; IC_V1 (no adjust) identical
#
# Synthetic corpus in a temp dir; never touches backtest.db.
# Run: cd backend && python3 app/backtest/ic/test_ic_adjust_fill.py

from __future__ import annotations

import atexit
import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[3])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

FENCE = "IC_ADJ_FILL_OPEN_20261101"
FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


# ───────────────────────────── engine ─────────────────────────────
E = 1_793_000_000 - 1_793_000_000 % 60          # a real epoch minute (real-clock rule)


def c(ts, o, h, l, cl):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": cl}


def t_engine():
    print("── 1–6. engine ──")
    from app.backtest.ic.ic_v1_engine import simulate_session, norm_leg, norm_adjust
    short = [norm_leg({"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 1, "premium_max": 100,
                       "sl_val": 42, "sl_mode": "pct"})]
    s_c = [c(E - 60, 80, 80, 80, 80), c(E, 80, 84, 79, 82),
           c(E + 60, 83, 120, 82, 105),             # high 120 ≥ SL 113.6, close 105 below it
           c(E + 120, 105, 108, 101, 106), c(E + 180, 106, 107, 104, 105)]
    acfg = {"L1": norm_adjust({"lots": 1, "premium_max": 110, "sl_val": 30, "tp_val": 100})}

    def run(adj_candles, fill=None, picks_extra=None):
        pick = {"symbol": "ADJ", "strike": 23000, "expiry": "2026-11-03", "candles": adj_candles}
        pick.update(picks_extra or {})
        kw = {} if fill is None else {"adjust_fill": fill}
        r = simulate_session(short, {"L1": [dict(x) for x in s_c]}, {"L1": "S"}, E, None, exit_mode="NEXT_OPEN",
                             adjust_on_sl=True, adjust_cfg=acfg, adjust_picks={"L1": pick}, **kw)
        tr = {t["leg"]: t for t in r["trades"]}
        co = r["carry_out"]
        return tr, co

    quiet = [c(E + 120, 100, 104, 99, 104), c(E + 180, 104, 106, 103, 105), c(E + 240, 105, 105, 104, 104)]
    for fill in ("open", "close"):
        tr, co = run(quiet, fill)
        s = tr["L1"]
        check(f"[{fill}] spike minute stops the short in THAT minute", s["exit_ts"] == E + 60 and s["exit_reason"] == "SL",
              (s["exit_ts"] - E, s["exit_reason"]))
        check(f"[{fill}] …at exactly the SL price (113.6), not the high or the close",
              abs(s["exit_price"] - 80 * 1.42) < 1e-9, s["exit_price"])
        a = co.get("L1A") or tr.get("L1A")
        check(f"[{fill}] adjustment opens at stop minute + 60 s", a and a["entry_ts"] == E + 120, a and a["entry_ts"] - E)
        want = 100.0 if fill == "open" else 104.0
        check(f"[{fill}] adjustment filled at the candle {fill.upper()} ({want})", a and a["entry_price"] == want,
              a and a["entry_price"])
    tr, co = run(quiet)
    a = co.get("L1A") or tr.get("L1A")
    check("engine default (no kwarg) = close — every pre-1-Nov caller unchanged", a and a["entry_price"] == 104.0,
          a and a["entry_price"])

    dip = [c(E + 120, 100, 104, 69, 104), c(E + 180, 104, 106, 103, 105), c(E + 240, 105, 105, 104, 104)]
    tr, co = run(dip, "open")
    a = tr.get("L1A")
    check("[open] activation candle low 69 ≤ ADJ SL 70 → stopped IN that minute",
          a is not None and a["exit_ts"] == E + 120 and a["exit_reason"] == "SL", a and (a["exit_ts"] - E, a["exit_reason"]))
    check("[open] …at exactly the ADJ SL price (70.0)", a is not None and abs(a["exit_price"] - 70.0) < 1e-9,
          a and a["exit_price"])
    tr, co = run(dip, "close")
    check("[close] same candles: the activation candle is NOT watched (no exit there)",
          "L1A" in co or (tr.get("L1A") and tr["L1A"]["exit_ts"] != E + 120), tr.get("L1A"))

    spike = [c(E + 120, 100, 205, 99, 120), c(E + 180, 120, 121, 119, 120), c(E + 240, 120, 120, 119, 119)]
    tr, co = run(spike, "open")
    a = tr.get("L1A")
    check("[open] target mirror: activation high 205 ≥ TP 200 → TP in that minute at 200",
          a is not None and a["exit_reason"] == "TP" and a["exit_ts"] == E + 120 and abs(a["exit_price"] - 200.0) < 1e-9,
          a and (a["exit_reason"], a["exit_ts"] - E, a["exit_price"]))
    tr, co = run(spike, "close")
    check("[close] target mirror off: no TP in the activation minute",
          not (tr.get("L1A") and tr["L1A"]["exit_ts"] == E + 120 and tr["L1A"]["exit_reason"] == "TP"))

    out = {}
    for fill in ("open", "close"):
        tr, co = run([], fill, {"symbol": "SYN-NIFTY-23000CE", "fill_price": 91.5, "synthetic": True,
                                "synth_kind": "adjust"})
        out[fill] = (tr, co)
        a = co.get("L1A") or tr.get("L1A")
        check(f"[{fill}] synthetic adjustment (no range) books its fill_price", a and a["entry_price"] == 91.5,
              a and a["entry_price"])
    check("synthetic adjustment identical in both modes", out["open"] == out["close"])


# ───────────────────────────── runner ─────────────────────────────
def t_runner():
    print("── 7–11. runner (synthetic corpus) ──")
    from app.utils.market_hours import is_trading_day
    import app.backtest.data.candle_source as _cs
    from app.backtest.ic.backtest_ic_runner import run_ic_backtest as NEW
    from app.backtest.ic.test_ic2_short_tp_skip_exp import build_corpus, legs, ADJ, key

    bak = HERE.parent / f"backtest_ic_runner.py.bak-{FENCE}"
    ORIG = None
    if bak.exists():
        from importlib.machinery import SourceFileLoader
        ldr = SourceFileLoader("ic_runner_orig_adjfill", str(bak))
        spec = importlib.util.spec_from_loader("ic_runner_orig_adjfill", ldr)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ic_runner_orig_adjfill"] = mod
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

    def run(fn, cfg, sid="IC_V2"):
        r = fn(db_path=dbp, strategy_id=sid, underlying="NIFTY", date_from=D0, date_to=D1, config_override=cfg)
        assert not r.get("aborted"), r.get("reason")
        return r

    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",
          "expiry_exit_time": "15:15", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ, "legs": legs(0)}
    K = lambda r, p=lambda t: True: sorted(key(t) for t in r["trades"] if p(t))   # noqa: E731
    is_adj = lambda t: "ADJ" in t.condition                                        # noqa: E731
    diag = lambda r: r["summary"].get("diag_ic", {})                               # noqa: E731

    absent = run(NEW, V2)
    op = run(NEW, {**V2, "adjust_fill": "open"})
    cl = run(NEW, {**V2, "adjust_fill": "close"})
    n_adj = sum(1 for t in op["trades"] if is_adj(t))
    print(f"   adjustments in the open run: {n_adj}")
    check("corpus exercises it: ≥ 3 adjustment legs", n_adj >= 3, n_adj)
    check("key absent ≡ \"open\" (row-for-row)", K(absent) == K(op))
    check("shorts + wings identical in open and close runs", K(op, lambda t: not is_adj(t)) == K(cl, lambda t: not is_adj(t)))
    check("…and the ·ADJ legs differ", K(op, is_adj) != K(cl, is_adj))
    check("diag adjust_fill = open in the open run", diag(op).get("adjust_fill") == "open")
    check("diag has no adjust_fill key in the close run", "adjust_fill" not in diag(cl))

    conn = sqlite3.connect(dbp)

    def bar(sym, ts):
        return conn.execute("SELECT open, close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?", (sym, ts)).fetchone()

    def chain(sym, ts):
        typ, exp = conn.execute("SELECT instrument_type, expiry FROM backtest_candles_1m WHERE tradingsymbol=? LIMIT 1",
                                (sym,)).fetchone()
        return conn.execute("SELECT tradingsymbol, open, close FROM backtest_candles_1m WHERE instrument_type=? "
                            "AND expiry=? AND ts=?", (typ, exp, ts)).fetchall()

    cap = float(ADJ["L1"]["premium_max"])
    for mode, r, col in (("open", op, 0), ("close", cl, 1)):
        bad_px, bad_pick, n = [], [], 0
        for t in r["trades"]:
            if not is_adj(t) or t.synthetic:
                continue
            n += 1
            b = bar(t.tradingsymbol, t.entry_ts)
            if not b or abs(round(b[col], 2) - t.entry_price) > 0.011:
                bad_px.append((t.tradingsymbol, t.entry_price, b))
            el = sorted(((-row[1 + col], row[0]) for row in chain(t.tradingsymbol, t.entry_ts) if 0 < row[1 + col] <= cap))
            best = (-el[0][0], el[0][1]) if el else (None, None)      # select_strike's tie-break: price desc, symbol asc
            if best[1] != t.tradingsymbol:
                bad_pick.append((t.tradingsymbol, best))
        check(f"[{mode}] every ·ADJ entry = the {mode.upper()} of its candle at the activation minute ({n} legs)",
              n > 0 and not bad_px, bad_px[:3])
        check(f"[{mode}] every ·ADJ strike = highest {mode} ≤ cap at that minute", n > 0 and not bad_pick, bad_pick[:3])

    if ORIG:
        o = run(ORIG, V2)
        check("\"close\" ≡ ORIGINAL runner (row-for-row)", K(cl) == K(o))
        check("\"close\": diag keys identical to ORIGINAL", set(diag(cl)) == set(diag(o)))
    else:
        print(f"  ! {bak.name} not found — ORIGINAL comparison skipped")

    V1 = {"entry_time": "09:18", "exit_time": "15:28", "legs": legs(0)}
    v1o, v1c = run(NEW, {**V1, "adjust_fill": "open"}, "IC_V1"), run(NEW, {**V1, "adjust_fill": "close"}, "IC_V1")
    check("IC_V1 (no adjustments): open ≡ close, no diag key", K(v1o) == K(v1c) and "adjust_fill" not in diag(v1o))


if __name__ == "__main__":
    t_engine()
    t_runner()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} IC ADJ-FILL CHECKS PASSED")
