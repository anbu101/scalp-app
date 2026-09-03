# backend/app/backtest/orv/test_orv_runner_sim.py
#
# ── ORV_V1 SIM SUITE ── behavioural checks for the ORB-Reversal engine and
# runner helpers. Fence: ORV_V1_20260903.
#
# Run standalone:  python3 test_orv_runner_sim.py
# Every check prints PASS/FAIL; a non-zero exit means at least one failed.
# The suite is PURE except the final integration test, which builds a tiny
# synthetic backtest.db in a temp dir and runs the full runner through it
# (catches SQL/wiring/dispatch regressions the pure tests cannot see).

from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orv_v1_engine import (                     # noqa: E402
    OrvBar, resample_1m, compute_orb, true_ranges, atr_series,
    is_hammer, is_shooting_star, is_bull_engulf, is_bear_engulf,
    orv_signals, target_level, resolve_spot_exit, SESSION_OPEN_MIN)
from backtest_orv_runner import (               # noqa: E402
    _merge_cfg, _hhmm, pick_candidate, DEFAULTS)

IST = 5 * 3600 + 30 * 60
FAILS = []


def check(name: str, ok: bool, note: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


def ds_for(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


DS = ds_for(date(2026, 1, 5))          # a Monday


def bar(minute: int, o, h, l, c, ds: int = DS) -> OrvBar:
    return OrvBar(ds + minute * 60, o, h, l, c)


def bar5(idx: int, o, h, l, c, ds: int = DS) -> OrvBar:
    """5m bar by post-09:15 bucket index (0 = 09:15)."""
    return OrvBar(ds + (SESSION_OPEN_MIN + idx * 5) * 60, o, h, l, c)


# ─────────────────────────────────────────────────────────────────────────
print("── resample_1m ──")
ones = [bar(SESSION_OPEN_MIN + k, 100 + k, 101 + k, 99 + k, 100.5 + k)
        for k in range(10)]
r5 = resample_1m(ones, day_start_epoch=DS, tf_minutes=5)
check("two 5m buckets from ten 1m bars", len(r5) == 2)
check("bucket open = first 1m open", r5[0].open == 100)
check("bucket close = last 1m close", r5[0].close == 100.5 + 4)
check("bucket high = max of highs", r5[0].high == 101 + 4)
check("bucket low = min of lows", r5[0].low == 99)
check("bucket ts on the 09:15 grid",
      (r5[1].ts - DS) // 60 == SESSION_OPEN_MIN + 5)
pre = resample_1m([bar(SESSION_OPEN_MIN - 3, 1, 2, 0, 1)] + ones,
                  day_start_epoch=DS, tf_minutes=5)
check("pre-09:15 prints ignored", len(pre) == 2 and pre[0].open == 100)

# ─────────────────────────────────────────────────────────────────────────
print("── compute_orb ──")
full = [bar5(i, 100, 110 + i, 90 - i, 105) for i in range(18)]
orb = compute_orb(full, day_start_epoch=DS, orb_minutes=90, tf_minutes=5)
check("ORB uses exactly the first 18 buckets",
      orb == (110 + 17, 90 - 17))
missing = [b for b in full if (b.ts - DS) // 60 != SESSION_OPEN_MIN + 35]
check("incomplete ORB (one bucket absent) -> None",
      compute_orb(missing, day_start_epoch=DS, orb_minutes=90,
                  tf_minutes=5) is None)
short = compute_orb(full[:3], day_start_epoch=DS, orb_minutes=15,
                    tf_minutes=5)
check("orb_minutes=15 needs 3 buckets", short == (112, 88))

# ─────────────────────────────────────────────────────────────────────────
print("── ATR ──")
dl = [(110, 90, 100), (120, 100, 115), (118, 108, 110), (140, 100, 120)]
trs = true_ranges(dl)
check("TR[0] = H-L", trs[0] == 20)
check("TR uses prev close gap", trs[1] == max(20, 20, 0) and trs[3] == 40)
sma = atr_series(dl, period=3, method="sma")
check("SMA ATR warm at index period-1",
      sma[0] is None and sma[1] is None and abs(sma[2] - (20 + 20 + 10) / 3) < 1e-9)
check("SMA ATR rolls", abs(sma[3] - (20 + 10 + 40) / 3) < 1e-9)
wil = atr_series(dl, period=3, method="wilder")
seed = (20 + 20 + 10) / 3
check("Wilder seed = SMA", abs(wil[2] - seed) < 1e-9)
check("Wilder smoothing", abs(wil[3] - (seed * 2 + 40) / 3) < 1e-9)
check("insufficient history -> all None",
      atr_series(dl[:2], period=3) == [None, None])

# ─────────────────────────────────────────────────────────────────────────
print("── patterns ──")
h = OrvBar(0, 100.0, 100.6, 97.0, 100.5)      # body .5, lower 3, upper .1
check("hammer: long lower wick", is_hammer(h))
check("hammer is not a star", not is_shooting_star(h))
s = OrvBar(0, 100.5, 104.0, 100.4, 100.0)     # body .5, upper 3.5, lower .4
check("star: long upper wick", is_shooting_star(s))
check("star is not a hammer", not is_hammer(s))
big_up = OrvBar(0, 100.0, 100.6, 98.5, 100.5)  # lower 1.5 < 2*.5=1.0? no: 1.5>=1.0 ok, upper .1<=.25 ok
check("ratio boundary: lower exactly 2x body passes",
      is_hammer(OrvBar(0, 100.0, 100.5, 99.0, 100.5)))
check("fat opposite wick fails hammer",
      not is_hammer(OrvBar(0, 100.0, 101.0, 97.0, 100.5)))
dragonfly = OrvBar(0, 100.0, 100.0, 97.0, 100.0)
check("dragonfly doji qualifies as hammer", is_hammer(dragonfly))
gravestone = OrvBar(0, 100.0, 103.0, 100.0, 100.0)
check("gravestone doji is NOT a hammer", not is_hammer(gravestone))
check("gravestone doji IS a star", is_shooting_star(gravestone))
check("flat bar (zero range) never a pattern",
      not is_hammer(OrvBar(0, 100, 100, 100, 100))
      and not is_shooting_star(OrvBar(0, 100, 100, 100, 100)))
prev_r = OrvBar(0, 101.0, 101.5, 99.5, 100.0)  # red body 101->100
eng_g = OrvBar(0, 99.8, 101.9, 99.6, 101.5)    # green body 99.8->101.5
check("bullish engulfing (red -> green, body engulfed)",
      is_bull_engulf(prev_r, eng_g))
check("green prev blocks classic bull engulf",
      not is_bull_engulf(OrvBar(0, 100, 101.5, 99.5, 101.0), eng_g))
check("need_opposite_prev=False relaxes it",
      is_bull_engulf(OrvBar(0, 100, 101.5, 99.5, 101.0), eng_g,
                     need_opposite_prev=False))
check("partial engulf fails",
      not is_bull_engulf(prev_r, OrvBar(0, 100.2, 101.9, 100.0, 101.5)))
prev_g = OrvBar(0, 100.0, 101.5, 99.8, 101.0)
eng_r = OrvBar(0, 101.2, 101.4, 99.5, 99.8)
check("bearish engulfing (green -> red)", is_bear_engulf(prev_g, eng_r))
check("red bar never bull-engulfs",
      not is_bull_engulf(prev_r, OrvBar(0, 101.5, 102, 99, 99.5)))
check("equal-body edge counts (>= convention)",
      is_bull_engulf(OrvBar(0, 101.0, 101.2, 99.9, 100.0),
                     OrvBar(0, 100.0, 101.2, 99.9, 101.0)))

# ─────────────────────────────────────────────────────────────────────────
print("── state machine ──")
# ORB 09:15-09:30 (3 buckets, orb_minutes=15), range 100..110.
ORB_BARS = [bar5(0, 105, 110, 100, 106), bar5(1, 106, 109, 101, 104),
            bar5(2, 104, 108, 100.5, 103)]
OH, OL = 110.0, 100.0


def run_sm(extra, **kw):
    d = {}
    sig = orv_signals(ORB_BARS + extra, day_start_epoch=DS, orb_high=OH,
                      orb_low=OL, orb_minutes=15, diag=d, **kw)
    return sig, d


# breakout below, then hammer -> CE
hammer5 = bar5(4, 98.6, 98.65, 95.5, 98.4)     # body .2, lower 2.9, upper .05
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0), hammer5])
check("close below ORB low arms bull", d["bull_arms"] >= 1)
check("hammer while bull-armed -> CE signal",
      len(sig) == 1 and sig[0].side == "CE" and sig[0].pattern == "HAMMER")
check("signal ts = pattern bar ts", sig[0].ts == hammer5.ts)

# the arming bar itself is never its own pattern
arm_hammer = bar5(3, 99.2, 99.25, 96.0, 99.1)  # closes below OL AND is a hammer
sig, d = run_sm([arm_hammer])
check("arming bar cannot be its own pattern", len(sig) == 0 and d["bull_arms"] == 1)
sig, d = run_sm([arm_hammer, bar5(4, 99.0, 99.05, 96.0, 98.9)])
check("NEXT hammer after the arming hammer fires", len(sig) == 1)

# disarm on close back inside
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0),      # arm bull
                 bar5(4, 99.5, 103.0, 99.3, 102.0),  # back inside, no pattern
                 hammer5])
check("close back inside disarms bull (no signal)",
      len(sig) == 0 and d["bull_disarms"] == 1)
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0),
                 bar5(4, 99.5, 103.0, 99.3, 102.0),
                 bar5(5, 102, 102.5, 97.5, 98.0),   # fresh close outside
                 hammer5])
check("re-arm after disarm works", len(sig) == 1 and d["bull_arms"] >= 2)
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0),
                 bar5(4, 99.5, 103.0, 99.3, 102.0),
                 hammer5], disarm_on_reentry=False)
check("disarm_on_reentry=False keeps the arm", len(sig) == 1)

# pattern beats disarm on the same bar (ORDERING RULE)
inside_hammer = bar5(4, 99.5, 100.8, 96.5, 100.7)  # hammer, closes INSIDE
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0), inside_hammer])
check("pattern evaluated before disarm on the same bar",
      len(sig) == 1 and sig[0].pattern == "HAMMER")

# bear side mirror
star5 = bar5(4, 111.4, 114.5, 111.38, 111.5)
sig, d = run_sm([bar5(3, 108, 112, 107, 111.0), star5])
check("close above ORB high arms bear", d["bear_arms"] >= 1)
check("star while bear-armed -> PE signal",
      len(sig) == 1 and sig[0].side == "PE" and sig[0].pattern == "STAR")

# engulfing path + prev-bar continuity
prev_green = bar5(4, 111.2, 112.8, 111.0, 112.5)
bear_eng = bar5(5, 112.7, 112.9, 110.9, 111.0)
sig, d = run_sm([bar5(3, 108, 112, 107, 111.0), prev_green, bear_eng])
check("bearish engulfing fires on the bear side",
      any(x.pattern == "BEAR_ENG" and x.side == "PE" for x in sig))
sig, d = run_sm([bar5(3, 108, 112, 107, 111.0), prev_green, bear_eng],
                engulf_on=False)
check("engulf_on=False silences it", len(sig) == 0)
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0), hammer5], hammer_on=False)
check("hammer_on=False silences the hammer", len(sig) == 0)

# both sides independent in one day
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0), hammer5,
                 bar5(6, 108, 112, 107, 111.0),
                 OrvBar(DS + (SESSION_OPEN_MIN + 35) * 60, 111.4, 114.5, 111.38, 111.5)])
check("bull then bear signals in one day",
      [x.side for x in sig] == ["CE", "PE"])

# max_wait_bars (inert default; active when > 0)
sig, d = run_sm([bar5(3, 102, 103, 98, 99.0),
                 bar5(4, 99, 99.5, 98.5, 99.0),
                 bar5(5, 99, 99.5, 98.5, 99.0),
                 OrvBar(DS + (SESSION_OPEN_MIN + 30) * 60, 98.6, 98.65, 95.5, 98.4)],
                max_wait_bars=2)
check("max_wait_bars disarms a stale arm",
      len(sig) == 0 and d["bull_wait_disarms"] == 1)

# ─────────────────────────────────────────────────────────────────────────
print("── targets & exits ──")
check("CE T1 = ORB low", target_level(side="CE", mode="T1", orb_high=110,
                                      orb_low=100, entry_spot=97,
                                      custom_pts=0) == 100)
check("CE T2 = ORB high", target_level(side="CE", mode="T2", orb_high=110,
                                       orb_low=100, entry_spot=97,
                                       custom_pts=0) == 110)
check("PE T1 = ORB high", target_level(side="PE", mode="T1", orb_high=110,
                                       orb_low=100, entry_spot=113,
                                       custom_pts=0) == 110)
check("PE T2 = ORB low", target_level(side="PE", mode="T2", orb_high=110,
                                      orb_low=100, entry_spot=113,
                                      custom_pts=0) == 100)
check("CE custom = entry + pts", target_level(side="CE", mode="custom",
                                              orb_high=110, orb_low=100,
                                              entry_spot=97,
                                              custom_pts=25) == 122)
check("PE custom = entry - pts", target_level(side="PE", mode="custom",
                                              orb_high=110, orb_low=100,
                                              entry_spot=113,
                                              custom_pts=25) == 88)
check("D1.4: CE entry already >= T1 -> None",
      target_level(side="CE", mode="T1", orb_high=110, orb_low=100,
                   entry_spot=100.5, custom_pts=0) is None)
check("D1.4: PE entry already <= T1 -> None",
      target_level(side="PE", mode="T1", orb_high=110, orb_low=100,
                   entry_spot=109.0, custom_pts=0) is None)

ce_sl, ce_tp = 95.0, 100.0
check("CE SL on spot low touch",
      resolve_spot_exit(side="CE", sl_level=ce_sl, tp_level=ce_tp,
                        spot_bar=OrvBar(0, 96, 97, 94.9, 96.5)) == "SL")
check("CE TP on spot high touch",
      resolve_spot_exit(side="CE", sl_level=ce_sl, tp_level=ce_tp,
                        spot_bar=OrvBar(0, 99, 100.2, 98.5, 99.9)) == "TP")
check("both in one bar -> SL wins",
      resolve_spot_exit(side="CE", sl_level=ce_sl, tp_level=ce_tp,
                        spot_bar=OrvBar(0, 97, 100.5, 94.5, 98)) == "SL")
check("no touch -> None",
      resolve_spot_exit(side="CE", sl_level=ce_sl, tp_level=ce_tp,
                        spot_bar=OrvBar(0, 97, 99, 96, 98)) is None)
check("PE SL on spot high, TP on spot low",
      resolve_spot_exit(side="PE", sl_level=115, tp_level=108,
                        spot_bar=OrvBar(0, 112, 115.2, 111, 113)) == "SL"
      and resolve_spot_exit(side="PE", sl_level=115, tp_level=108,
                            spot_bar=OrvBar(0, 110, 111, 107.8, 109)) == "TP")

# ─────────────────────────────────────────────────────────────────────────
print("── cfg & helpers ──")
c = _merge_cfg({"target_mode": "banana", "atr_method": "x",
                "sl_points": -20, "max_trades_per_day": 0,
                "orb_minutes": "junk", "atr_pct": "-25"})
check("bad target_mode -> T1", c["target_mode"] == "T1")
check("bad atr_method -> wilder", c["atr_method"] == "wilder")
check("negative sl_points -> abs", c["sl_points"] == 20)
check("zero max_trades_per_day -> 1", c["max_trades_per_day"] == 1)
check("junk orb_minutes -> default", c["orb_minutes"] == DEFAULTS["orb_minutes"])
check("negative atr_pct -> abs", c["atr_pct"] == 25.0)
check("defaults untouched by empty override",
      _merge_cfg(None)["max_trades_per_day"] == 2)
check("_hhmm parses", _hhmm("14:30", 0) == 870 and _hhmm("junk", 99) == 99)
check("pick_candidate nearest-below",
      pick_candidate({"A": 150.0, "B": 179.5, "C": 181.0}, below=180.0) == "B")
check("pick_candidate floor",
      pick_candidate({"A": 90.0, "B": 179.5}, below=180.0, floor=100.0) == "B"
      and pick_candidate({"A": 90.0}, below=180.0, floor=100.0) is None)
check("pick_candidate empty -> None", pick_candidate({}, below=180.0) is None)

# ─────────────────────────────────────────────────────────────────────────
print("── integration: synthetic corpus end-to-end ──")
# atr_period=3 to keep the corpus small. Sessions: 5 warmup weekdays, then
# the trade day. Trade day script (5m spot): ORB 09:15-09:30 = 100..110,
# breakout below at bar 3 (close 99), hammer at bar 4, entry at bar-5 open
# 09:40 spot open 98.0, SL 98-10=88, T1=100 hit at 10:00 -> option TP.


def _weekdays_before(d0: date, n: int):
    out, d = [], d0 - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _integration() -> None:
    trade_day = date(2026, 1, 21)              # Wed (holiday-free week)
    warm = _weekdays_before(trade_day, 5)
    tmp = tempfile.mkdtemp(prefix="orv_sim_")
    dbp = os.path.join(tmp, "backtest.db")
    cn = sqlite3.connect(dbp)
    cn.execute("""CREATE TABLE backtest_candles_1m (
        instrument_token INTEGER NOT NULL, ts INTEGER NOT NULL,
        underlying TEXT NOT NULL, tradingsymbol TEXT NOT NULL,
        instrument_type TEXT NOT NULL, strike REAL NOT NULL,
        expiry TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
        low REAL NOT NULL, close REAL NOT NULL,
        volume INTEGER NOT NULL DEFAULT 0, oi INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (instrument_token, ts))""")

    def put(tok, ts, sym, itype, strike, expiry, o, h, l, c):
        cn.execute("INSERT OR REPLACE INTO backtest_candles_1m VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,0,0)",
                   (tok, ts, "NIFTY", sym, itype, strike, expiry, o, h, l, c))

    # warmup sessions: flat spot days, range 20 pts -> ATR(3) = 20.
    for wd in warm:
        wds = ds_for(wd)
        for k in range(0, 375):
            m = SESSION_OPEN_MIN + k
            put(1, wds + m * 60, "NIFTY_SPOT", "SPOT", 0, "1970-01-01",
                100.0, 110.0, 90.0, 100.0)

    # trade day 5m script, written as 1m bars (each 5m bucket = 5 identical
    # 1m bars carrying the bucket's OHLC shape on its first/last minute).
    tds = ds_for(trade_day)
    script = {
        0: (105, 110, 100, 106),   # ORB bars: range exactly 100..110 = 10pts
        1: (106, 109, 101, 104),   # 10 > 25% of ATR 20 = 5 -> filter passes
        2: (104, 108, 100.5, 103),
        3: (102, 103, 98, 99.0),   # breakout close below 100 -> arm bull
        4: (98.9, 98.92, 95.5, 98.8),  # HAMMER (body .1, lower 3.3, upper .02)
        5: (98.0, 98.5, 97.5, 98.2),  # entry bar: spot open 98.0
        6: (98.2, 99.0, 98.0, 98.8),
        7: (98.8, 99.5, 98.5, 99.2),
        8: (99.2, 100.4, 99.0, 100.2),  # high 100.4 >= T1 100 -> TP here
    }
    for idx in range(0, 75):
        o, h, l, c = script.get(idx, (100.2, 100.4, 100.0, 100.2))
        for k in range(5):
            m = SESSION_OPEN_MIN + idx * 5 + k
            # spread the 5m shape: first minute opens at o, last closes at c,
            # every minute carries the bucket's full high/low so the 5m
            # resample reproduces the script exactly.
            put(1, tds + m * 60, "NIFTY_SPOT", "SPOT", 0, "1970-01-01",
                o if k == 0 else c, h, l, c)

    # one CE and one PE, expected weekly expiry for the trade day.
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    exp = expected_expiry_for_day(trade_day).isoformat()
    for idx in range(0, 75):
        for k in range(5):
            m = SESSION_OPEN_MIN + idx * 5 + k
            ts = tds + m * 60
            # CE priced to move up after entry; entry minute (09:40) open 120.
            ce_px = 120.0 + (m - (SESSION_OPEN_MIN + 25)) * 0.5
            put(2, ts, "NIFTYTESTCE", "CE", 24000, exp,
                ce_px, ce_px + 1, ce_px - 1, ce_px + 0.5)
            put(3, ts, "NIFTYTESTPE", "PE", 24000, exp,
                90.0, 91.0, 89.0, 90.5)
    cn.commit()
    cn.close()

    from backtest_orv_runner import run_orv_backtest
    res = run_orv_backtest(
        db_path=dbp, strategy_id="ORV_V1", underlying="NIFTY",
        date_from=trade_day, date_to=trade_day,
        config_override={"orb_minutes": 15, "atr_period": 3,
                         "atr_pct": 25.0, "sl_points": 10.0,
                         "target_mode": "T1", "premium_max": 180.0,
                         "entry_block_time": "14:30",
                         "eod_square_off": "15:15"})
    dg = res["summary"].get("diag_orv", {})
    check("integration: not aborted", not res.get("aborted"),
          str(res.get("reason")))
    check("integration: exactly one trade",
          res["summary"]["total_trades"] == 1, str(res["summary"]))
    if res["trades"]:
        t = res["trades"][0]
        check("integration: CE side, HAMMER pattern",
              t.instrument_type == "CE" and "HAMMER" in t.condition,
              t.condition)
        check("integration: entry at 09:40 option open",
              (t.entry_ts - tds) // 60 == SESSION_OPEN_MIN + 25
              and abs(t.entry_price - 120.0) < 1e-6,
              f"min={(t.entry_ts - tds) // 60} px={t.entry_price}")
        check("integration: TP exit at the T1 touch minute",
              t.exit_reason == "TP"
              and (t.exit_ts - tds) // 60 == SESSION_OPEN_MIN + 40,
              f"{t.exit_reason} @min {(t.exit_ts - tds) // 60}")
        check("integration: spot SL/TP recorded on the trade",
              abs(t.sl - 88.0) < 1e-6 and abs(t.tp - 100.0) < 1e-6,
              f"sl={t.sl} tp={t.tp}")
    check("integration: ATR gate passed exactly once",
          dg.get("days_range_filter_skip") == 0 and dg.get("days_no_atr") == 0,
          str({k: dg.get(k) for k in ("days_no_atr", "days_range_filter_skip")}))
    check("integration: arm/pattern accounting",
          dg.get("bull_arms", 0) >= 1 and dg.get("pattern_hist", {}).get("HAMMER") == 1,
          str(dg.get("pattern_hist")))

    # ATR gate must kill the day when atr_pct is cranked up.
    res2 = run_orv_backtest(
        db_path=dbp, strategy_id="ORV_V1", underlying="NIFTY",
        date_from=trade_day, date_to=trade_day,
        config_override={"orb_minutes": 15, "atr_period": 3,
                         "atr_pct": 60.0, "sl_points": 10.0})
    dg2 = res2["summary"].get("diag_orv", {})
    check("integration: atr_pct=60 filters the day out",
          res2["summary"]["total_trades"] == 0
          and dg2.get("days_range_filter_skip") == 1, str(dg2))


try:
    import app  # noqa: F401  (only runs inside the repo's backend/)
    _HAVE_APP = True
except ImportError:
    _HAVE_APP = False

if _HAVE_APP:
    _integration()
else:
    print("  SKIP  integration (app package not importable — run from backend/)")

# ─────────────────────────────────────────────────────────────────────────
print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
