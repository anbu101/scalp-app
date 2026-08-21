#!/usr/bin/env python3
# ── TSG_MTM_BASIS_20260821 ── minute-level trace of 2024-04-18 (H<8 legs)
#
# Question: why did BOTH bases exit L2/L4 at 13:27 with pair MTM -40k, when
# the DAILY trigger needed only pair <= -11.4k and POSITION <= -35k?
#
# This script reads the corpus directly and reconstructs the runner's own
# mark semantics (prev-candle-close pointer walk, carry-forward on gaps):
#   1. candle coverage + gaps > 60s for L2 (22300PE) and L4 (22050PE)
#   2. the reconstructed minute MTM series with realized folded in
#   3. the first minute each basis SHOULD fire on available data, and the
#      largest single-step MTM jump (a big jump right at 13:27 after a
#      long gap = stale-mark artifact, not a code bug)
#
# Run from repo root:  python3 tsg_mtm_trace_20260821.py
# Read-only (mode=ro).

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

BT_DB = Path.home() / ".scalp-app" / "backtest" / "backtest.db"
IST = timezone(timedelta(hours=5, minutes=30))

DAY = datetime(2024, 4, 18, tzinfo=IST)
T = lambda h, m: int(DAY.replace(hour=h, minute=m).timestamp())
HM = lambda ts: datetime.fromtimestamp(ts, IST).strftime("%H:%M")

# From run 9efcbb28's own rows (H<8, 18-Apr-24):
QTY = 650
L2 = ("NIFTY2441822300PE", 86.20, "SELL")    # survivor short
L4 = ("NIFTY2441822050PE", 7.85, "BUY")      # survivor hedge
# Realized at 11:11 (L1 SELL 110.40->149.85, L3 BUY 7.75->10.90), gross:
REALIZED = (110.40 - 149.85) * QTY + (10.90 - 7.75) * QTY   # = -23,595
MTM_SL = 35000.0
IV_EXIT_TS = T(11, 11)
ENTRY_TS = T(9, 16)
EOD_TS = T(15, 26)


def candles(conn, sym):
    rows = conn.execute(
        "SELECT ts, close FROM backtest_candles_1m "
        "WHERE tradingsymbol = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        (sym, T(9, 15), T(15, 30))).fetchall()
    if not rows:   # naming drift: probe by strike/type/underlying instead
        strike = float(sym[-7:-2]); otype = sym[-2:]
        alt = conn.execute(
            "SELECT DISTINCT tradingsymbol FROM backtest_candles_1m "
            "WHERE underlying='NIFTY' AND instrument_type=? AND strike=? "
            "AND ts BETWEEN ? AND ? LIMIT 5",
            (otype, strike, T(9, 15), T(15, 30))).fetchall()
        print(f"  [WARN] no rows for '{sym}'; same-strike symbols: "
              f"{[a[0] for a in alt]}")
        if len(alt) == 1:
            print(f"  [INFO] falling back to {alt[0][0]}")
            rows = conn.execute(
                "SELECT ts, close FROM backtest_candles_1m "
                "WHERE tradingsymbol = ? AND ts BETWEEN ? AND ? ORDER BY ts",
                (alt[0][0], T(9, 15), T(15, 30))).fetchall()
    return rows


def main():
    conn = sqlite3.connect(f"file:{BT_DB}?mode=ro", uri=True)
    print("=" * 66)
    print("1) CANDLE COVERAGE + GAPS (>60s), 09:15\u201315:30 IST, 2024-04-18")
    print("=" * 66)
    series = {}
    for sym, entry, action in (L2, L4):
        cds = candles(conn, sym)
        series[sym] = cds
        print(f"\n  {sym}: {len(cds)} candles")
        gaps = [(a[0], b[0]) for a, b in zip(cds, cds[1:]) if b[0] - a[0] > 60]
        if not gaps:
            print("    no gaps > 60s")
        for a, b in gaps:
            print(f"    GAP {HM(a)} -> {HM(b)}  ({(b - a) // 60} min)")
        tail = [c for c in cds if T(13, 15) <= c[0] <= T(13, 30)]
        print("    closes 13:15\u201313:30:",
              [f"{HM(c[0])}={c[1]}" for c in tail] or "NONE")

    print()
    print("=" * 66)
    print("2) RECONSTRUCTED MINUTE MTM (runner semantics: mark at minute m =")
    print("   close of last candle with ts < m, carry-forward on gaps)")
    print("=" * 66)
    minutes = list(range(ENTRY_TS + 60, EOD_TS + 1, 60))

    def walk(sym, entry_px):
        cds = series[sym]
        idx, last, out, stale = 0, entry_px, {}, {}
        last_ts = None
        for m in minutes:
            while idx < len(cds) and cds[idx][0] < m:
                last, last_ts = cds[idx][1], cds[idx][0]
                idx += 1
            out[m] = last
            stale[m] = (last_ts is None or m - last_ts > 60)
        return out, stale

    m2, s2 = walk(L2[0], L2[1])
    m4, s4 = walk(L4[0], L4[1])

    def pair(m):
        return (L2[1] - m2[m]) * QTY + (m4[m] - L4[1]) * QTY

    daily_fire = pos_fire = None
    prev_mtm = None
    jump = (0.0, None)
    print(f"\n  realized after 11:11 IV exit = {REALIZED:,.0f} gross")
    print(f"  DAILY fires at pair <= {-MTM_SL - REALIZED:,.0f} ; "
          f"POSITION at pair <= {-MTM_SL:,.0f}\n")
    for m in minutes:
        if m <= IV_EXIT_TS:
            continue                      # survivors phase only
        p = pair(m)
        day = REALIZED + p
        if prev_mtm is not None and abs(day - prev_mtm) > abs(jump[0]):
            jump = (day - prev_mtm, m)
        prev_mtm = day
        if daily_fire is None and day <= -MTM_SL:
            daily_fire = m
        if pos_fire is None and p <= -MTM_SL:
            pos_fire = m
        if T(13, 15) <= m <= T(13, 28) or m in (daily_fire, pos_fire):
            print(f"  {HM(m)}  L2={m2[m]:>7.2f}{'*' if s2[m] else ' '} "
                  f"L4={m4[m]:>6.2f}{'*' if s4[m] else ' '} "
                  f"pair={p:>10,.0f}  day={day:>10,.0f}"
                  f"{'  <== DAILY' if m == daily_fire else ''}"
                  f"{'  <== POSITION' if m == pos_fire else ''}")
    stale2 = sum(1 for m in minutes if m > IV_EXIT_TS and s2[m])
    stale4 = sum(1 for m in minutes if m > IV_EXIT_TS and s4[m])
    print(f"\n  stale-mark minutes after 11:11:  L2={stale2}  L4={stale4}")
    print(f"  largest single-step day-MTM jump: {jump[0]:,.0f} at "
          f"{HM(jump[1]) if jump[1] else '-'}")
    print(f"  first DAILY-basis crossing:    {HM(daily_fire) if daily_fire else 'never'}")
    print(f"  first POSITION-basis crossing: {HM(pos_fire) if pos_fire else 'never'}")
    print()
    print("  READ IT LIKE THIS:")
    print("  \u2022 Long L2 gap ending ~13:26 + both crossings at 13:27 + big jump")
    print("    => STALE-MARK ARTIFACT: corpus hole, code is correct. The MTM")
    print("    exits on gap days book worse than -SL because the move was")
    print("    invisible while marks were frozen.")
    print("  \u2022 Full candle coverage but DAILY crossing well before 13:27")
    print("    => the runner evaluated something different from this")
    print("    reconstruction \u2014 send me the full output, that IS a code bug.")


if __name__ == "__main__":
    main()
