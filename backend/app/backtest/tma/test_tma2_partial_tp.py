# backend/app/backtest/tma/test_tma2_partial_tp.py
#
# ── TMA2_PARTIAL_TP_20261014 ── two-target partial booking on the TMA_V2 runner,
# end-to-end on the synthetic corpus of test_tma_v2_dte_lots (real epoch clock,
# real expiry calendar / symbol builder / CandleSource).
#
# Decisive checks:
#   * OFF ≡ ORIGINAL against the apply script's backup of the runner.
#   * KEEP-SL invariance: booking T1 must not move the REST of the position —
#     every baseline trade either reappears unchanged (T1 never filled) or splits
#     into a TP1 row + a remainder row with the baseline's exit time and price.
#
# Run from backend/:  python app/backtest/tma/test_tma2_partial_tp.py

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3]))          # backend/
sys.path.insert(0, str(HERE.parent))

from app.backtest.tma import backtest_tma_v2_runner as NEWR   # noqa: E402
from app.backtest.tma.tma_v2_engine import monitor_partial_day as MPD   # noqa: E402
import test_tma_v2_dte_lots as H                              # noqa: E402  (corpus builder)

FENCE = "TMA2_PARTIAL_TP_20261014"
LOT = 65
_TZ = timezone(timedelta(seconds=19800))
OK = [0]
FAILS = []


def check(name, cond):
    if cond:
        OK[0] += 1
        print("ok  ", name)
    else:
        FAILS.append(name)
        print("FAIL", name)


BASE = {"mode": "SELL", "trade_mode": "POSITIONAL", "xover_exit_ref": 55,
        "max_extension_pct": 0.8, "ema144_slope_gate": True,
        "s1": {"main": {"premium_max": 200, "lots": 10, "sl_pct": 12, "tp_pct": 10,
                        "sl_unit": "PCT", "tp_unit": "ABS"},
               "hedge": {"premium_max": 5, "lots": 10}}}


def run(mod, db, extra=None, partial=None, main=None):
    cfg = json.loads(json.dumps(BASE))
    cfg.update(extra or {})
    if main:
        cfg["s1"]["main"].update(main)
    if partial is not None:
        cfg["s1"]["partial"] = partial
    return mod.run_tma_v2_backtest(
        db_path=db, strategy_id="TMA_V2", underlying="NIFTY",
        date_from=date(2024, 1, 1), date_to=date(2024, 3, 31), config_override=cfg)


def day(ts):
    return datetime.fromtimestamp(ts, _TZ).date()


def by_pos(r):
    """{(entry_ts, direction): [rows]} — one position = one entry minute"""
    g = defaultdict(list)
    for t in r["trades"]:
        g[(t.entry_ts, t.direction)].append(t)
    return g


def split_ok(base, pp, lots_t1, rest_reason_map, hedge_t1=None):
    """KEEP-SL invariance on the monitored (SELL) leg + hedge accounting."""
    bg, pg = by_pos(base), by_pos(pp)
    booked = untouched = bad = 0
    for (ets, dr), brows in bg.items():
        if dr != "SELL":
            continue
        b = brows[0]
        rows = sorted(pg.get((ets, "SELL"), []), key=lambda t: (t.exit_ts, t.exit_reason != "TP1"))
        if len(rows) == 1 and asdict(rows[0]) == asdict(b):
            untouched += 1
            continue
        if len(rows) == 1:
            r0 = rows[0]
            if (r0.exit_ts, r0.exit_price, r0.exit_reason, r0.qty) == (b.exit_ts, b.exit_price, b.exit_reason, b.qty):
                untouched += 1          # same exit, only the ambiguity flag may differ
                continue
            bad += 1
            continue
        t1r, rest = rows
        ok = (t1r.exit_reason == "TP1" and t1r.qty == lots_t1 * LOT and rest.qty == b.qty - lots_t1 * LOT
              and t1r.exit_ts <= rest.exit_ts and rest.exit_ts == b.exit_ts
              and abs(rest.exit_price - b.exit_price) < 1e-6
              and rest.exit_reason == rest_reason_map.get(b.exit_reason, b.exit_reason)
              and abs(t1r.pnl - (t1r.entry_price - t1r.exit_price) * t1r.qty) < 2.0)   # charges model rounds to tick
        if hedge_t1 is not None:
            hr = sorted(pg.get((ets, "BUY"), []), key=lambda t: t.exit_ts)
            want = [hedge_t1 * LOT, 10 * LOT - hedge_t1 * LOT] if hedge_t1 else [10 * LOT]
            ok = ok and [h.qty for h in hr] == want
            if hedge_t1:
                ok = ok and hr[0].exit_reason == "TP1" and hr[0].exit_ts == t1r.exit_ts
        booked += ok
        bad += (not ok)
    return booked, untouched, bad


def main_tests(db):
    base = run(NEWR, db)
    check("fixture: baseline has SELL positions", sum(1 for t in base["trades"] if t.direction == "SELL") >= 8)

    # ── 1. OFF ≡ ORIGINAL ────────────────────────────────────────────────
    bak = HERE.parent / f"backtest_tma_v2_runner.py.bak-{FENCE}"
    if bak.exists():
        tmp = HERE.parent / "_tma2_orig_for_pp_test.py"
        tmp.write_text(bak.read_text())
        try:
            spec = importlib.util.spec_from_file_location("app.backtest.tma._tma2_orig_for_pp_test", tmp)
            OLDR = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(OLDR)
            for extra in ({}, {"trade_mode": "INTRADAY"}, {"mode": "BUY"},
                          {"dte_lot_mult": "4:2", "max_loss_per_trade": 5000, "sl_streak_count": 2}):
                a = run(OLDR, db, extra)
                for p in (None, {}):
                    b = run(NEWR, db, extra, partial=p)
                    tag = f"{extra or 'sealed-shape'} partial={p}"
                    check(f"OFF ≡ ORIGINAL rows {tag}",
                          [asdict(t) for t in a["trades"]] == [asdict(t) for t in b["trades"]] and len(a["trades"]) > 0)
                    check(f"OFF ≡ ORIGINAL summary+diag {tag}", a["summary"] == b["summary"])
        finally:
            tmp.unlink(missing_ok=True)
    else:
        print(f"note: {bak.name} not found — OFF≡ORIGINAL skipped (the apply script runs it)")
    check("OFF: no pp diag keys", not any(k.startswith("pp_") or k == "partial_booking"
                                          for k in base["summary"]["diag_tma2"]))

    # ── 2. ON, KEEP SL, PROP hedge: T1 ₹60 × 4 lots, T2 ₹10 ──────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4})
    dg = r["summary"]["diag_tma2"]
    t1rows = [t for t in r["trades"] if t.exit_reason == "TP1" and t.direction == "SELL"]
    check("ON: T1 fired on several positions", len(t1rows) >= 3 and dg["pp_t1_hits"] == len(t1rows))
    check("ON: TP1 fills at or through ₹60 (gap → open, never worse than T1)",
          all(t.exit_price <= 60 + 1e-9 and t.tp == 60 for t in t1rows))
    bk, un, bad = split_ok(base, r, 4, {"TP": "TP2"}, hedge_t1=4)
    check(f"ON/KEEP: the rest exits exactly like the baseline (booked {bk}, untouched {un}, bad {bad})",
          bad == 0 and bk == len(t1rows) and bk + un == sum(1 for t in base["trades"] if t.direction == "SELL"))
    check("ON: per-position SELL qty always sums to 10 lots",
          all(sum(t.qty for t in rows) == 10 * LOT for (e, d_), rows in by_pos(r).items() if d_ == "SELL"))
    check("ON: per-position hedge qty always sums to 10 lots",
          all(sum(t.qty for t in rows) == 10 * LOT for (e, d_), rows in by_pos(r).items() if d_ == "BUY"))
    tp2 = [t for t in r["trades"] if t.exit_reason == "TP2" and t.direction == "SELL"]
    check("ON: TP2 rows exist and are counted", tp2 and dg["pp_t2_hits"] == len(tp2))
    check("ON: a plain 'TP' only on positions where T1 was never armed/hit",
          all(len(by_pos(r)[(t.entry_ts, "SELL")]) == 1 for t in r["trades"] if t.exit_reason == "TP" and t.direction == "SELL"))
    check("ON: pp_armed ≥ T1 hits, diag string present",
          dg["pp_armed"] >= dg["pp_t1_hits"] and "T1 60ABS x4L" in dg["partial_booking"])
    check("ON: a T1 booked one day with the rest carried overnight exists",
          any(day(t.exit_ts) < day(u.exit_ts) for t in t1rows for u in by_pos(r)[(t.entry_ts, "SELL")] if u is not t))
    check("ON: entries identical to baseline (booking never changes the slot)",
          sorted({t.entry_ts for t in base["trades"]}) == sorted({t.entry_ts for t in r["trades"]}))

    # ── 3. T1 next to T2 → same-candle T1+T2 must be handled ─────────────
    r = run(NEWR, db, partial={"t1": 11, "lots": 5})
    bk, un, bad = split_ok(base, r, 5, {"TP": "TP2"}, hedge_t1=5)
    check(f"T1 11 / T2 10: invariance holds (booked {bk}, bad {bad}) — same-candle cases are unit-tested below",
          bad == 0 and bk >= 1)

    # ── 4. BE: SL moves to entry after T1 ────────────────────────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4, "after_t1_sl": "BE"})
    dg = r["summary"]["diag_tma2"]
    g = by_pos(r)
    rests = [rows for (e, d_), rows in g.items() if d_ == "SELL" and len(rows) == 2]
    rest_rows = [max(rows, key=lambda t: (t.exit_ts, t.exit_reason != "TP1")) for rows in rests]
    check("BE: remainder rows carry SL = entry", rest_rows and all(abs(t.sl - t.entry_price) < 0.01 for t in rest_rows))
    check("BE: no plain 'SL' after a booked T1 — it is SL_BE",
          all(t.exit_reason != "SL" for t in rest_rows))
    be = [t for t in rest_rows if t.exit_reason == "SL_BE"]
    check(f"BE: SL_BE exits ({len(be)}) fill at/through entry and match the diag count",
          all(t.exit_price >= t.entry_price - 1e-9 for t in be) and dg["pp_be_exits"] == len(be))

    # ── 5. hedge KEEP: wing held in full ─────────────────────────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4, "hedge": "KEEP"})
    bk, un, bad = split_ok(base, r, 4, {"TP": "TP2"}, hedge_t1=0)
    check(f"hedge KEEP: no TP1 hedge rows, full wing to the final exit (bad {bad})",
          bad == 0 and bk >= 3 and not any(t.exit_reason == "TP1" and t.direction == "BUY" for t in r["trades"]))

    # ── 6. scaling with DTE × lots ───────────────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": "4:2"}, partial={"t1": 60, "lots": 4})
    g = by_pos(r)
    big = [rows for (e, d_), rows in g.items() if d_ == "SELL" and sum(t.qty for t in rows) == 20 * LOT and len(rows) == 2]
    check("DTE 4:2: 20-lot entries split 8 / 12", big and all(sorted(t.qty for t in rows) == [8 * LOT, 12 * LOT] for rows in big))

    # ── 7. BUY mode, % units ─────────────────────────────────────────────
    r = run(NEWR, db, {"mode": "BUY"}, partial={"t1": 20, "lots": 3},
            main={"premium_max": 150, "sl_pct": 30, "tp_pct": 60, "tp_unit": "PCT"})
    t1b = [t for t in r["trades"] if t.exit_reason == "TP1"]
    check("BUY: T1 rows at ≥ entry × 1.2, 3 lots, no hedge rows",
          t1b and all(t.direction == "BUY" and t.qty == 3 * LOT and t.exit_price >= t.entry_price * 1.2 - 0.01 for t in t1b)
          and all(t.direction == "BUY" for t in r["trades"]))

    # ── 8. T2 = 0: the rest rides (no TP2 anywhere) ──────────────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4}, main={"tp_pct": 0})
    check("T2 off: T1 books, remainder never exits on a target",
          any(t.exit_reason == "TP1" for t in r["trades"]) and not any(t.exit_reason in ("TP", "TP2") for t in r["trades"]))

    # ── 9. loud aborts ───────────────────────────────────────────────────
    for p, why in (({"t1": 60, "lots": 10}, "lots"), ({"t1": 8, "lots": 4}, "ABOVE"), ({"t1": 0, "lots": 4}, "> 0")):
        a = run(NEWR, db, partial=p)
        check(f"abort: {p} → {why}", a.get("aborted") and why in a.get("reason", ""))
    a = run(NEWR, db, {"mode": "BUY"}, partial={"t1": 70, "lots": 3}, main={"tp_pct": 60, "tp_unit": "PCT"})
    check("abort: BUY % T1 beyond T2", a.get("aborted") and "nearer" in a.get("reason", ""))


# ── pure monitor_partial_day units (hand-built candles; every branch) ────
D0 = 1704252600          # 2024-01-03 09:00 IST on the real epoch clock
def _c(i, o, h, l, c):
    return {"ts": D0 + 60 * i, "open": o, "high": h, "low": l, "close": c}


def _pos(action="SELL", sl=120.0, tp=10.0, t1=60.0, be=False):
    return {"side": "CE", "action": action, "entry_ts": D0 - 60, "entry_price": 100.0,
            "sl_price": sl, "tp_price": tp, "watch_from": D0, "last_close": 100.0,
            "last_ts": D0 - 60, "pp_armed": True, "pp_t1": t1, "pp_be": be}


def unit_tests():
    # T1 then later T2
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 50, 55), _c(1, 55, 56, 30, 31), _c(2, 31, 32, 5, 6)], None, None)
    check("unit: T1 at level, T2 later at level",
          t1["exit_reason"] == "TP1" and t1["exit_price"] == 60 and t1["exit_ts"] == D0
          and r["exit_reason"] == "TP2" and r["exit_price"] == 10 and r["exit_ts"] == D0 + 120 and p["pp_booked"])
    # T1 + T2 in ONE candle → both
    t1, r = MPD(_pos(), [_c(0, 95, 96, 5, 7)], None, None)
    check("unit: T1+T2 same candle → both fill at their levels, same minute",
          t1["exit_price"] == 60 and r["exit_reason"] == "TP2" and r["exit_price"] == 10 and r["exit_ts"] == t1["exit_ts"])
    # gap through T1 only / through both
    t1, r = MPD(_pos(), [_c(0, 40, 45, 35, 38)], None, None)
    check("unit: gap through T1 fills at open, rest survives", t1["exit_price"] == 40 and r is None)
    t1, r = MPD(_pos(), [_c(0, 8, 9, 6, 7)], None, None)
    check("unit: gap through T1 AND T2 → both at the open", t1["exit_price"] == 8 and r["exit_price"] == 8 and r["exit_reason"] == "TP2")
    # SL + T1 same candle → SL whole, ambiguous
    p = _pos(); t1, r = MPD(p, [_c(0, 100, 125, 55, 90)], None, None)
    check("unit: SL + T1 one candle → whole position SL, ambiguous, T1 not booked",
          t1 is None and r["exit_reason"] == "SL" and r["ambiguous_fill"] and r["tp_price"] == 10 and not p.get("pp_booked"))
    # BE: not on the T1 candle, fires after; gap above entry fills at open
    p = _pos(be=True); t1, r = MPD(p, [_c(0, 95, 101, 55, 70), _c(1, 70, 99, 65, 98)], None, None)
    check("unit: BE never evaluated on the T1 candle itself; SL moved to entry",
          t1["exit_price"] == 60 and t1["sl_price"] == 120 and r is None and p["sl_price"] == 100.0)
    t1, r = MPD(p, [_c(2, 99, 100.5, 97, 100)], None, None)
    check("unit: BE stop next candle → SL_BE at entry", t1 is None and r["exit_reason"] == "SL_BE" and r["exit_price"] == 100.0)
    p = _pos(be=True); MPD(p, [_c(0, 95, 96, 55, 70)], None, None)
    t1, r = MPD(p, [_c(1, 105, 106, 104, 105)], None, None)
    check("unit: BE gap above entry fills at the open (105)", r["exit_reason"] == "SL_BE" and r["exit_price"] == 105)
    # KEEP: original SL stays, labelled plain SL
    p = _pos(); MPD(p, [_c(0, 95, 96, 55, 70)], None, None)
    t1, r = MPD(p, [_c(1, 110, 121, 109, 118)], None, None)
    check("unit: KEEP → remainder SL at the original level, plain 'SL'", r["exit_reason"] == "SL" and r["exit_price"] == 120)
    # XOVER due on the T1 candle → rest at that close
    t1, r = MPD(_pos(), [_c(0, 95, 96, 55, 58)], D0, None)
    check("unit: crossover due on the T1 candle → T1 at level, rest XOVER at close",
          t1["exit_price"] == 60 and r["exit_reason"] == "XOVER" and r["exit_price"] == 58)
    # XOVER before T1 → whole position
    t1, r = MPD(_pos(), [_c(0, 95, 96, 80, 85), _c(1, 85, 86, 50, 52)], D0, None)
    check("unit: crossover before T1 → whole position XOVER", t1 is None and r["exit_reason"] == "XOVER" and r["exit_price"] == 85)
    # carry: T1 day 1, armed off; day 2 plain TP → TP2
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 55, 58)], None, None)
    p["watch_from"] = 0
    t1b, r2 = MPD(p, [_c(1440, 30, 31, 9, 9.5)], None, None)
    check("unit: carried remainder hits T2 next session → TP2, no second T1",
          t1 is not None and r is None and t1b is None and r2["exit_reason"] == "TP2" and r2["exit_price"] == 10)
    # hard close after T1
    t1, r = MPD(_pos(), [_c(0, 95, 96, 55, 58), _c(1, 58, 59, 50, 52)], None, D0 + 120, "EOD")
    check("unit: hard close after T1 → rest EOD at last close", r["exit_reason"] == "EOD" and r["exit_price"] == 52)
    # MTM cut: already evaluated (disarmed) before T1 → not re-run; T1 before the cut → still evaluated
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 80, 85), _c(1, 85, 86, 55, 101), _c(2, 101, 102, 100.5, 101)], None, None, mtm_cut_ts=D0)
    check("unit: MTM cut disarmed before T1 is not re-evaluated afterwards", t1 is not None and r is None)
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 55, 101), _c(1, 101, 103, 100.5, 102), _c(2, 102, 103, 101, 102)], None, None, mtm_cut_ts=D0 + 120)
    check("unit: T1 before the cut → cut still applies to the rest (MTM_CUT)", t1 is not None and r["exit_reason"] == "MTM_CUT")
    # BUY mirror
    p = _pos("BUY", sl=70.0, tp=200.0, t1=130.0)
    t1, r = MPD(p, [_c(0, 105, 135, 104, 133), _c(1, 133, 205, 132, 201)], None, None)
    check("unit: BUY mirror — T1 above, T2 above", t1["exit_price"] == 130 and r["exit_reason"] == "TP2" and r["exit_price"] == 200)
    # unarmed = monitor verbatim
    from app.backtest.tma.tma_v2_engine import monitor_position_day as MPOS
    cs = [_c(0, 95, 96, 55, 58), _c(1, 58, 59, 5, 6)]
    q1 = {k: v for k, v in _pos().items() if not k.startswith("pp_")}
    q2 = dict(q1)
    check("unit: no pp keys ≡ monitor_position_day", MPD(q1, cs, None, None) == (None, MPOS(q2, cs, None, None)))


if __name__ == "__main__":
    unit_tests()
    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "bt.db")
        print("building synthetic corpus…")
        H.build_corpus(dbp)
        main_tests(dbp)
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} TMA_V2 PARTIAL-TP CHECKS PASSED")
