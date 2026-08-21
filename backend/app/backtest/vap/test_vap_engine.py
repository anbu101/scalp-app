# backend/app/backtest/vap/test_vap_engine.py
#
# VAP_V1 engine tests — pure, no DB, no app imports beyond the engine.
# Run standalone:  python backend/app/backtest/vap/test_vap_engine.py
#
# Covers the things that would silently corrupt a run rather than crash:
#   * VWAP volume weighting and the volume=0 / no-volume-yet cases
#   * Wilder ATR seed index (the TMA_V1 EMA-seed lesson, applied to ATR)
#   * the arm / enter / disarm state machine including the busy rule
#   * the vwap_buffer_pct asymmetry (entry buffered, re-arm not)
#   * SL/TP sizing across PCT/ATR × RR/PCT and the max_sl_pct clamp
#   * SELL-side level inversion through the reused sl_tp_levels

import os
import sys

# Runs BOTH ways, on purpose:
#   cd backend && python3 -m app.backtest.vap.test_vap_engine   (house style)
#   python3 backend/app/backtest/vap/test_vap_engine.py         (direct path)
# The direct-path form is the one that bites: it puts only vap/ on the
# path, so the `app.*` imports fail, the flat fallback fires, and
# tma_v1_engine then fails on ITS OWN flat import of pst_indicators — a
# second-order miss that a scratch directory with everything copied flat
# will happily hide. Putting the backend ROOT on the path first makes the
# `app.*` branch succeed in both cases, which is the real fix; the
# sibling dirs stay as a belt-and-braces fallback.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _p in (_BACKEND, _HERE,
           os.path.join(os.path.dirname(_HERE), "tma"),
           os.path.join(os.path.dirname(_HERE), "pst"),
           os.path.join(os.path.dirname(_HERE), "ic")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from app.backtest.vap.vap_v1_engine import (  # noqa: E402
        atr_wilder, breached_during, bucket_volumes, decide_leg,
        ema_at_bar_ends, leg_bar_facts, size_sl_tp, sl_tp_levels,
        volume_ok, vwap_by_minute,
    )
except ImportError:
    from vap_v1_engine import (  # type: ignore  # noqa: E402
        atr_wilder, breached_during, bucket_volumes, decide_leg,
        ema_at_bar_ends, leg_bar_facts, size_sl_tp, sl_tp_levels,
        volume_ok, vwap_by_minute,
    )

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {extra}")
        FAILS.append(name)


def m(ts, o, h, l, c, v):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def b5(ts, o, h, l, c, complete=True):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c,
            "complete": complete}


# ──────────────────────────────────────────────────────────────────────
print("VWAP")
# typical price = (h+l+c)/3; two bars, volumes 100 and 300
bars = [m(0, 10, 12, 8, 10, 100), m(60, 10, 22, 14, 18, 300)]
vm = vwap_by_minute(bars)
tp1 = (12 + 8 + 10) / 3.0
tp2 = (22 + 14 + 18) / 3.0
check("first bar VWAP = its own typical price", abs(vm[0] - tp1) < 1e-9,
      f"got {vm[0]} want {tp1}")
want = (tp1 * 100 + tp2 * 300) / 400.0
check("second bar is volume weighted, not a mean",
      abs(vm[60] - want) < 1e-9, f"got {vm[60]} want {want}")
check("volume weighting differs from the simple mean",
      abs(want - (tp1 + tp2) / 2) > 1e-6)

# zero-volume bars contribute nothing and carry VWAP forward
bars = [m(0, 10, 12, 8, 10, 100), m(60, 99, 99, 99, 99, 0)]
vm = vwap_by_minute(bars)
check("volume=0 bar does not move VWAP", abs(vm[60] - tp1) < 1e-9,
      f"got {vm[60]}")

# undefined until cumulative volume is positive — None, never 0.0
vm = vwap_by_minute([m(0, 10, 12, 8, 10, 0), m(60, 10, 12, 8, 11, 0)])
check("VWAP is None while cumulative volume is 0",
      vm[0] is None and vm[60] is None, f"got {vm}")
vm = vwap_by_minute([m(0, 10, 12, 8, 10, 0), m(60, 10, 12, 8, 11, 50)])
check("VWAP appears on the first bar with real volume",
      vm[0] is None and vm[60] is not None)

# ──────────────────────────────────────────────────────────────────────
print("ATR (Wilder, SMA-seeded)")
bars = [b5(i * 300, 100, 110, 90, 100) for i in range(10)]
a = atr_wilder(bars, 3)
check("index 0..period-1 unwarmed",
      a[0] is None and a[1] is None and a[2] is None, f"got {a[:3]}")
check("seed lands at index=period", a[3] is not None, f"got {a[3]}")
# every TR is 20 on a flat series of identical bars → ATR is exactly 20
check("flat series → ATR equals the constant TR",
      all(abs(x - 20.0) < 1e-9 for x in a[3:]), f"got {a[3:]}")
check("too few bars → all None", atr_wilder(bars[:3], 3) == [None] * 3)
check("period 6 is warm on the 7th bar (≈09:50 off a 09:15 anchor)",
      atr_wilder([b5(i * 300, 100, 110, 90, 100) for i in range(7)], 6)[6]
      is not None)

# ──────────────────────────────────────────────────────────────────────
print("leg_bar_facts")
bars = [b5(0, 10, 12, 8, 11), b5(300, 11, 12, 8, 9)]
# VWAP at each bucket's FINAL minute (ts + tf - 60)
vm = {240: 10.0, 540: 10.0}
f = leg_bar_facts(bars, vm, tf_s=300)
check("ts_end is the bar completion time",
      [x["ts_end"] for x in f] == [300, 600])
check("close 11 vs vwap 10 → above", f[0]["above"] is True and f[0]["below"] is False)
check("close 9 vs vwap 10 → below", f[1]["above"] is False and f[1]["below"] is True)

f = leg_bar_facts(bars, {240: None, 540: 10.0}, tf_s=300)
check("undefined VWAP → above/below both None",
      f[0]["above"] is None and f[0]["below"] is None)

f = leg_bar_facts([b5(0, 10, 12, 8, 11, complete=False)], {240: 10.0}, tf_s=300)
check("incomplete bucket is dropped (stale close)", f == [])

# buffer applies to the entry test only, never to re-arming
f = leg_bar_facts([b5(0, 10, 12, 8, 10.4)], {240: 10.0}, tf_s=300,
                  buffer_pct=5.0)
check("buffer suppresses a marginal break", f[0]["above"] is False)
check("buffer does NOT make the leg re-arm", f[0]["below"] is False)

# ──────────────────────────────────────────────────────────────────────
print("decide_leg state machine")
d = dict(armed=True, busy=False, entries=0, max_entries=0)
check("armed + above → ENTER",
      decide_leg(above=True, below=False, **d)[0] == "ENTER")
check("armed + below → ARM (stays eligible)",
      decide_leg(above=False, below=True, **d) == ("ARM", True))
check("disarmed + above → NONE",
      decide_leg(above=True, below=False, armed=False, busy=False,
                 entries=0, max_entries=0)[0] == "NONE")
check("disarmed + below → ARM",
      decide_leg(above=False, below=True, armed=False, busy=False,
                 entries=0, max_entries=0) == ("ARM", True))
check("undefined VWAP → WARMUP and does NOT arm",
      decide_leg(above=None, below=None, armed=False, busy=False,
                 entries=0, max_entries=0) == ("WARMUP", False))
check("busy → BUSY even on a below-close (no arming mid-trade)",
      decide_leg(above=False, below=True, armed=False, busy=True,
                 entries=0, max_entries=0) == ("BUSY", False))
check("cap spent → CAP not ENTER",
      decide_leg(above=True, below=False, armed=True, busy=False,
                 entries=3, max_entries=3)[0] == "CAP")
check("cap 0 = unlimited",
      decide_leg(above=True, below=False, armed=True, busy=False,
                 entries=99, max_entries=0)[0] == "ENTER")
check("inside the buffer band (neither above nor below) → NONE",
      decide_leg(above=False, below=False, armed=True, busy=False,
                 entries=0, max_entries=0)[0] == "NONE")

# full re-entry cycle: enter, exit, must see a below-close before re-entry
armed = True
act, armed = decide_leg(above=True, below=False, armed=armed, busy=False,
                        entries=0, max_entries=0)
check("cycle 1 enters", act == "ENTER")
armed = False                      # runner disarms on entry
act, armed = decide_leg(above=True, below=False, armed=armed, busy=False,
                        entries=1, max_entries=0)
check("cycle 2 above-close after exit does NOT re-enter", act == "NONE")
act, armed = decide_leg(above=False, below=True, armed=armed, busy=False,
                        entries=1, max_entries=0)
check("cycle 3 below-close re-arms", act == "ARM" and armed is True)
act, armed = decide_leg(above=True, below=False, armed=armed, busy=False,
                        entries=1, max_entries=0)
check("cycle 4 next above-close re-enters", act == "ENTER")

# ──────────────────────────────────────────────────────────────────────
print("size_sl_tp")
s, why = size_sl_tp(entry_price=100, sl_mode="PCT", sl_pct=25,
                    atr_value=None, atr_mult=0, max_sl_pct=0,
                    tp_mode="RR", rr=1.5, tp_pct=0)
check("PCT SL 25% of 100 → 25 points", why is None and abs(s["sl_pts"] - 25) < 1e-9)
check("RR target measures the SL DISTANCE, not the premium",
      abs(s["tp_val"] - 37.5) < 1e-9 and s["tp_unit"] == "PTS")

s, why = size_sl_tp(entry_price=100, sl_mode="ATR", sl_pct=0,
                    atr_value=8.0, atr_mult=1.5, max_sl_pct=0,
                    tp_mode="RR", rr=2.0, tp_pct=0)
check("ATR SL = atr × mult", abs(s["sl_pts"] - 12.0) < 1e-9)
check("ATR feeds sl_tp_levels as PTS (locked level math reused)",
      s["sl_unit"] == "PTS")

s, why = size_sl_tp(entry_price=100, sl_mode="ATR", sl_pct=0,
                    atr_value=None, atr_mult=1.5, max_sl_pct=0,
                    tp_mode="RR", rr=2.0, tp_pct=0)
check("unwarmed ATR refuses to size (no silent %-fallback)",
      s is None and why == "atr_warmup")

s, why = size_sl_tp(entry_price=100, sl_mode="ATR", sl_pct=0,
                    atr_value=60.0, atr_mult=1.5, max_sl_pct=35,
                    tp_mode="PCT", rr=0, tp_pct=40)
check("max_sl_pct clamps an ATR spike",
      abs(s["sl_pts"] - 35.0) < 1e-9 and s["clamped"] is True)
check("clamped RR/PCT target stays in PCT when asked",
      s["tp_unit"] == "PCT" and abs(s["tp_val"] - 40) < 1e-9)

s, why = size_sl_tp(entry_price=100, sl_mode="PCT", sl_pct=0,
                    atr_value=None, atr_mult=0, max_sl_pct=0,
                    tp_mode="RR", rr=1.5, tp_pct=0)
check("RR with no SL is refused, not silently zeroed",
      s is None and why == "rr_without_sl")

s, why = size_sl_tp(entry_price=100, sl_mode="PCT", sl_pct=0,
                    atr_value=None, atr_mult=0, max_sl_pct=0,
                    tp_mode="PCT", rr=0, tp_pct=40)
check("SL disabled + PCT target is legal", why is None and s["sl_pts"] == 0)

# ──────────────────────────────────────────────────────────────────────
print("sl_tp_levels side inversion (reused from tma_v2_engine)")
sl, tp = sl_tp_levels(100, "BUY", 25, 37.5, "PTS", "PTS")
check("BUY: SL below entry, TP above", abs(sl - 75) < 1e-9 and abs(tp - 137.5) < 1e-9)
sl, tp = sl_tp_levels(100, "SELL", 25, 37.5, "PTS", "PTS")
check("SELL: SL above entry, TP below", abs(sl - 125) < 1e-9 and abs(tp - 62.5) < 1e-9)
sl, tp = sl_tp_levels(20, "SELL", 25, 37.5, "PTS", "PTS")
check("SELL TP floored at 0.05, never negative", tp >= 0.05)

# ──────────────────────────────────────────────────────────────────────
print("breached_during (SL-grace diagnostics)")
# SHORT is stopped when the premium RISES to the level
sh = [b5(0, 100, 130, 95, 110)]
check("short: high reaches the level -> breach",
      breached_during(sh, 125.0, True) is True)
check("short: high short of the level -> no breach",
      breached_during(sh, 140.0, True) is False)
check("short: a LOW excursion is not a breach",
      breached_during([b5(0, 100, 105, 10, 100)], 125.0, True) is False)
# long is stopped when the premium FALLS to the level
check("long: low reaches the level -> breach",
      breached_during([b5(0, 100, 110, 70, 90)], 75.0, False) is True)
check("long: low short of the level -> no breach",
      breached_during([b5(0, 100, 110, 70, 90)], 60.0, False) is False)
check("long: a HIGH excursion is not a breach",
      breached_during([b5(0, 100, 200, 95, 150)], 75.0, False) is False)
check("no level (SL disabled) -> never a breach",
      breached_during(sh, None, True) is False)
check("empty window -> no breach", breached_during([], 125.0, True) is False)
# the trigger side must match monitor_position_day exactly
sl_short, _ = sl_tp_levels(100, "SELL", 25, 37.5, "PTS", "PTS")
check("short breach agrees with the SELL level from sl_tp_levels",
      breached_during([b5(0, 100, 126, 99, 120)], sl_short, True) is True)
sl_long, _ = sl_tp_levels(100, "BUY", 25, 37.5, "PTS", "PTS")
check("long breach agrees with the BUY level from sl_tp_levels",
      breached_during([b5(0, 100, 101, 74, 80)], sl_long, False) is True)

# ──────────────────────────────────────────────────────────────────────
print("entry filters: EMA on the option's own premium")
b1 = [m(i * 60, 100, 100, 100, 100.0 + i, 1000) for i in range(20)]
b5b = [b5(0, 100, 120, 100, 119), b5(300, 100, 120, 100, 119)]
e = ema_at_bar_ends(b1, b5b, tf_s=300, period=5, basis_minutes=1)
check("1m basis: EMA sampled at each bucket's final minute",
      e[300] is not None and e[600] is not None)
check("1m basis EMA5 is warm inside the first bucket", e[300] is not None)
e = ema_at_bar_ends(b1, b5b, tf_s=300, period=20, basis_minutes=5)
check("5m basis with only 2 bars: EMA20 unwarmed",
      e[300] is None and e[600] is None)
check("period 0 -> empty map (filter off)",
      ema_at_bar_ends(b1, b5b, tf_s=300, period=0, basis_minutes=1) == {})

# the trap: EMA20 on 5m off a 09:15 anchor is not warm until 10:55
warm_5m = 9 * 60 + 15 + 20 * 5
check("EMA20 @5m warms at 10:55 (the guard's reason to exist)",
      warm_5m == 10 * 60 + 55, f"got {warm_5m//60}:{warm_5m%60}")
warm_1m = 9 * 60 + 15 + (20 - 1)
check("EMA20 @1m warms at 09:34 — usable inside the window",
      warm_1m == 9 * 60 + 34)

print("entry filters: volume")
check("volume at the multiple passes", volume_ok([100, 100, 100], 300, 3.0) is True)
check("volume under the multiple fails", volume_ok([100, 100, 100], 299, 3.0) is False)
check("too few prior bars -> undecidable (None)",
      volume_ok([100, 100], 900, 3.0) is None)
check("mult 0 = filter off, always True", volume_ok([], 0, 0) is True)
check("zero-volume prior bars are ignored, not counted as samples",
      volume_ok([0, 0, 0, 100, 100, 100], 300, 3.0) is True)
check("all-zero history -> undecidable", volume_ok([0, 0, 0], 500, 2.0) is None)

bv = bucket_volumes([m(i * 60, 1, 1, 1, 1, 10) for i in range(10)],
                    [b5(0, 1, 1, 1, 1), b5(300, 1, 1, 1, 1)], tf_s=300)
check("bucket volume sums the 1m rows", bv[300] == 50 and bv[600] == 50)

print("entry filters: gating is ENTRY-ONLY")
d = dict(armed=True, busy=False, entries=0, max_entries=0)
check("EMA rejects -> EMA_BLOCK, leg stays ARMED",
      decide_leg(above=True, below=False, ema_ok=False, **d) == ("EMA_BLOCK", True))
check("EMA unwarmed -> EMA_WARMUP, leg stays ARMED",
      decide_leg(above=True, below=False, ema_ok=None, **d) == ("EMA_WARMUP", True))
check("volume rejects -> VOL_BLOCK",
      decide_leg(above=True, below=False, vol_ok=False, **d)[0] == "VOL_BLOCK")
check("volume undecidable -> VOL_WARMUP",
      decide_leg(above=True, below=False, vol_ok=None, **d)[0] == "VOL_WARMUP")
check("both filters pass -> ENTER",
      decide_leg(above=True, below=False, ema_ok=True, vol_ok=True, **d)[0] == "ENTER")
# THE critical invariant: a filter must never suppress re-arming
check("a below-close still ARMS with the EMA unwarmed",
      decide_leg(above=False, below=True, armed=False, busy=False, entries=0,
                 max_entries=0, ema_ok=None) == ("ARM", True))
check("a below-close still ARMS with volume rejecting",
      decide_leg(above=False, below=True, armed=False, busy=False, entries=0,
                 max_entries=0, vol_ok=False) == ("ARM", True))
check("filters do not override BUSY",
      decide_leg(above=True, below=False, armed=True, busy=True, entries=0,
                 max_entries=0, ema_ok=False)[0] == "BUSY")
check("cap is checked before the filters",
      decide_leg(above=True, below=False, armed=True, busy=False, entries=3,
                 max_entries=3, ema_ok=False)[0] == "CAP")

print("entry filters: leg_bar_facts wiring")
f = leg_bar_facts([b5(0, 10, 12, 8, 11)], {240: 10.0}, tf_s=300,
                  ema_at={300: 12.0})
check("close 11 under EMA 12 -> ema_ok False", f[0]["ema_ok"] is False)
f = leg_bar_facts([b5(0, 10, 12, 8, 11)], {240: 10.0}, tf_s=300,
                  ema_at={300: 9.0})
check("close 11 over EMA 9 -> ema_ok True", f[0]["ema_ok"] is True)
f = leg_bar_facts([b5(0, 10, 12, 8, 11)], {240: 10.0}, tf_s=300)
check("no ema_at -> filter off, ema_ok True", f[0]["ema_ok"] is True)

# ──────────────────────────────────────────────────────────────────────
print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("all VAP_V1 engine tests passed")