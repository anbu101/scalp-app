# backend/app/backtest/emc/test_emc_runner_sim.py
#
# ── EMC_V1_20261011 ── behavioural simulation suite (+ EMC_V1_FIX1_20261011: gap-cross
# toggle, arm tag, independent crossover proof, replay arming marker). Standalone:
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/emc/test_emc_runner_sim.py
# Part 1 pure engine (EMA = Pine ta.ema, crosses, band pick, fills, trail,
# the option track vs a brute-force causal recompute). Part 2 scripted
# sessions through simulate_day (every rule of the spec, one scenario each).
# Part 3 the whole runner on the replay suite's synthetic Black-76 corpus:
# fill / band / ordering / cap / cutoff invariants on every trade.

from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)

from emc_v1_engine import (                       # noqa: E402
    EmcBar, Ema, EmaPair, ema_series, crossed, aligned, spot_crosses, pick_in_band,
    OptionEmaTrack, stop_fill, target_fill, effective_stop, DayParams, simulate_day,
    resample_session, SESSION_OPEN_MIN)

IST = 5 * 3600 + 30 * 60
FAILS = []
NPASS = [0]


def check(name: str, ok, note: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        NPASS[0] += 1
    else:
        FAILS.append(name)


def ds_for(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST


DS = ds_for(date(2026, 1, 5))
O = SESSION_OPEN_MIN


def b1(minute, o, h, l, c, ds=DS):
    return EmcBar(ds + minute * 60, o, h, l, c)


# ═══════════════════════════════ 1. engine ═══════════════════════════════
print("── engine: EMA (Pine ta.ema, SMA seed) ──")
vals = [10, 11, 12, 13, 14, 15, 16]
e = ema_series(vals, 3)
check("None until period values", e[0] is None and e[1] is None)
check("period-th value is the SMA seed", abs(e[2] - 11.0) < 1e-12)
check("recursive alpha 2/(n+1)", abs(e[3] - (13 * 0.5 + 11 * 0.5)) < 1e-12)
em = Ema(3)
em.update(10); em.update(11)
check("peek before ready returns the seed it would make", abs(em.peek(12) - 11.0) < 1e-12 and em.value is None)
em.update(12)
pk = em.peek(20)
check("peek does not commit", abs(pk - (20 * 0.5 + 11 * 0.5)) < 1e-12 and abs(em.value - 11.0) < 1e-12)
# parity with the fleet's TMA SMA-seeded EMA
try:
    from app.backtest.tma.tma_v1_engine import ema_series as tma_ema
    random.seed(3)
    xs = [100 + random.uniform(-5, 5) for _ in range(300)]
    a, b = ema_series(xs, 21), tma_ema(xs, 21)
    check("bit-identical to TMA_V1's SMA-seeded EMA (300 values)",
          all((p is None and q is None) or (p is not None and q is not None and abs(p - q) < 1e-9)
              for p, q in zip(a, b)))
except Exception as ex:   # pragma: no cover
    check("TMA EMA parity (import)", False, repr(ex))

print("── engine: crosses / alignment ──")
check("crossover", crossed(1, 2, 3, 2) == 1)
check("crossover from equality", crossed(2, 2, 3, 2) == 1)
check("crossunder", crossed(3, 2, 1, 2) == -1)
check("no cross when staying above", crossed(3, 2, 4, 2) == 0)
check("touch without crossing is not a cross", crossed(3, 2, 2, 2) == 0)
check("None → 0", crossed(None, 2, 3, 2) == 0)
check("aligned CE/PE", aligned("CE", 2, 1) and aligned("PE", 1, 2) and not aligned("CE", 1, 1) and not aligned("PE", None, 1))
pair = EmaPair(2, 3)
bars = [EmcBar(DS + (O + 5 * i) * 60, 0, 0, 0, c, O + 5 * i + 4) for i, c in enumerate([10, 10, 10, 12, 14, 9, 7, 7])]
xs = spot_crosses(bars, pair)
check("spot_crosses keyed by bar end minute, both directions",
      list(xs.values()) == [1, -1] and all((k - O + 1) % 5 == 0 for k in xs), str(xs))
p2 = EmaPair(2, 3)
x1 = spot_crosses(bars[:4], p2)
x2 = spot_crosses(bars[4:], p2)
check("continuous across sessions (split == whole)", {**x1, **x2} == xs)

print("── engine: band pick ──")
qs = [{"sym": "A", "strike": 23000, "price": 210}, {"sym": "B", "strike": 23050, "price": 195.5},
      {"sym": "C", "strike": 23100, "price": 168}, {"sym": "D", "strike": 23150, "price": 150},
      {"sym": "E", "strike": 23200, "price": 149.95}]
check("highest premium inside [150,200]", pick_in_band(qs, 150, 200)["sym"] == "B")
check("band is inclusive at both ends", pick_in_band(qs, 150, 195.5)["sym"] == "B" and
      pick_in_band(qs, 150, 150)["sym"] == "D")
check("lowest pick", pick_in_band(qs, 150, 200, pick="lowest")["sym"] == "D")
check("nothing in band → None", pick_in_band(qs, 300, 400) is None)
tie = [{"sym": "X", "strike": 23000, "price": 180}, {"sym": "Y", "strike": 23100, "price": 180}]
check("tie → strike nearer spot", pick_in_band(tie, 150, 200, spot=23090)["sym"] == "Y")

print("── engine: fills / trail ──")
B = NS(open=100, high=110, low=90, close=105)
check("stop touched books AT level", stop_fill(95, B) == 95)
check("stop gapped books the open", stop_fill(101, B) == 100)
check("stop not reached", stop_fill(89, B) is None)
check("target touched books AT level", target_fill(108, B) == 108)
check("target gapped books the open", target_fill(99, B) == 100)
check("trail not armed below trigger", effective_stop(entry=100, peak=115, sl_level=80, trail_on=True,
                                                      trail_trigger=20, trail_gap=10) == (80, False))
check("trail armed at trigger: peak − gap", effective_stop(entry=100, peak=120, sl_level=80, trail_on=True,
                                                           trail_trigger=20, trail_gap=10) == (110, True))
check("trail never below fixed SL", effective_stop(entry=100, peak=100, sl_level=80, trail_on=True,
                                                   trail_trigger=0, trail_gap=30) == (80, False))
check("trail with SL off", effective_stop(entry=100, peak=105, sl_level=None, trail_on=True,
                                          trail_trigger=0, trail_gap=10) == (95, True))

print("── engine: option track vs brute-force causal recompute ──")
random.seed(11)
px = 180.0
warm_1m, today_1m = [], []
DSW = ds_for(date(2026, 1, 2))
for m in range(O, O + 375):
    o = px; px = max(5, px + random.uniform(-2, 2))
    warm_1m.append(EmcBar(DSW + m * 60, o, max(o, px) + 0.5, min(o, px) - 0.5, px))
for m in range(O, O + 375):
    if random.random() < 0.07:                # missing prints
        continue
    o = px; px = max(5, px + random.uniform(-2, 2))
    today_1m.append(EmcBar(DS + m * 60, o, max(o, px) + 0.5, min(o, px) - 0.5, px))
warm = resample_session(warm_1m, day_start_epoch=DSW, tf_minutes=5)
tr = OptionEmaTrack(fast=9, slow=21, tf=5, ds=DS, warm_bars=warm, today_1m=today_1m)
tr2 = OptionEmaTrack(fast=9, slow=21, tf=5, ds=DS, warm_bars=warm, today_1m=today_1m)
ok_i = ok_c = True
for k in range(O, O + 375):
    known = [c for c in today_1m if (c.ts - DS) // 60 <= k]
    tb = resample_session(known, day_start_epoch=DS, tf_minutes=5)   # last bar = forming (partial) when k isn't an end
    closes_i = [b.close for b in warm] + [b.close for b in tb]
    closes_c = [b.close for b in warm] + [b.close for b in tb if b.end_min <= k]
    fi, si, ni = tr.at(k, True)
    fc, sc, nc = tr2.at(k, False)
    rf, rs = ema_series(closes_i, 9)[-1], ema_series(closes_i, 21)[-1]
    cf, cs = ema_series(closes_c, 9)[-1], ema_series(closes_c, 21)[-1]
    if abs(fi - rf) > 1e-9 or abs(si - rs) > 1e-9 or ni != len(closes_i):
        ok_i = False
    if abs(fc - cf) > 1e-9 or abs(sc - cs) > 1e-9 or nc != len(closes_c):
        ok_c = False
check("intrabar: every minute equals EMA over closed bars + the forming bar's latest print", ok_i)
check("bar_close: every minute equals EMA over closed bars only", ok_c)
cold = OptionEmaTrack(fast=9, slow=21, tf=5, ds=DS, warm_bars=[], today_1m=today_1m)
f, s, n = cold.at(O + 30, True)
check("cold contract: slow EMA not ready after 7 bars", s is None and n <= 7)
try:
    cold.at(O + 10)
    back = False
except ValueError:
    back = True
check("track refuses to go backwards in time (no accidental lookahead)", back)

# ═══════════════════════════ 2. scripted sessions ═══════════════════════════
print("── simulate_day: scripted sessions ──")


class FakeTrack:
    """Scripted option EMA: aligned (for its side) from minute `from_k` on."""
    def __init__(self, side, from_k, ready_k=None):
        self.side, self.from_k, self.ready_k = side, from_k, ready_k

    def at(self, k, intrabar=True):
        if self.ready_k is not None and k < self.ready_k:
            return None, None, 5
        up = k >= self.from_k
        if self.side == "CE":
            return (2.0, 1.0, 30) if up else (1.0, 2.0, 30)
        return (1.0, 2.0, 30) if up else (2.0, 1.0, 30)


def flat_bars(price_by_min, hl=1.0):
    """{min: bar} from {min: close}; open = previous close."""
    out, prev = {}, None
    for m in sorted(price_by_min):
        v = price_by_min[m]
        if isinstance(v, tuple):                 # explicit (o, h, l, c)
            out[m] = NS(open=v[0], high=v[1], low=v[2], close=v[3])
            prev = v[3]
            continue
        c = v
        o = prev if prev is not None else c
        out[m] = NS(open=o, high=max(o, c) + hl, low=min(o, c) - hl, close=c)
        prev = c
    return out


def P(**kw):
    base = dict(sel_lo=150, sel_hi=200, sel_pick="highest", direction="BOTH", intrabar=True, opt_min_bars=21,
                fill="close", sl_pts=20, tp_pts=40, trail_on=False, trail_trigger=20, trail_gap=15,
                xover_exit=True, block_min=14 * 60 + 30, eod_min=15 * 60 + 15, max_trades=0, max_losses=0)
    base.update(kw)
    return DayParams(**base)


def run(p, cross_at, chain, tracks, net=None):
    """chain: {sym: (side, strike, {min: close})}; tracks: {sym: FakeTrack}."""
    bars = {s: flat_bars(v[2]) for s, v in chain.items()}

    def quotes_at(k, side):
        return [{"sym": s, "strike": v[1], "price": bars[s][k].close} for s, v in chain.items()
                if v[0] == side and k in bars[s]]

    dg = {}
    tr = simulate_day(p=p, cross_at=cross_at, quotes_at=quotes_at, bars_of=lambda s: bars.get(s, {}),
                      track_of=lambda c: tracks[c["sym"]],
                      net_of=net or (lambda a, b: (b - a) * 65 - 40), spot_at=lambda k: 23000.0, diag=dg)
    return tr, dg


M = lambda hh, mm: hh * 60 + mm            # noqa: E731
ALL = range(O, M(15, 30))


def const(v, **over):
    d = {m: v for m in ALL}
    d.update(over)
    return d


# S1: up cross 10:04 → highest CE in band; option already aligned → entry at once (close fill)
chain = {"CE1": ("CE", 23000, const(230)), "CE2": ("CE", 23050, const(190)), "CE3": ("CE", 23100, const(160)),
         "PE1": ("PE", 23000, const(175))}
tr, dg = run(P(), {M(10, 4): 1}, chain, {"CE2": FakeTrack("CE", 0)})
t = tr[0] if tr else None
check("S1 picks the highest CE premium in [150,200]", t and t.sym == "CE2" and t.side == "CE", tr)
check("S1 already aligned → entry at the cross minute's close", t and t.entry_min == M(10, 4) and t.aligned_at_setup and t.entry_px == 190)
check("S1 flat premium → EOD exit at the open of 15:15", t and t.reason == "EOD" and t.exit_min == M(15, 15))

# S2: not aligned at setup → waits; option EMA aligns at 10:21 → entry 10:21 (close) / 10:22 open (next_open)
ce = const(180)
ce.update({M(10, 21): 184, M(10, 22): 186})
chain = {"CE2": ("CE", 23050, ce)}
tr, dg = run(P(), {M(10, 4): 1}, chain, {"CE2": FakeTrack("CE", M(10, 21))})
check("S2 waits for the option's own EMA alignment, then buys (close fill)",
      tr and tr[0].entry_min == M(10, 21) and tr[0].entry_px == 184 and tr[0].waited == 17 and not tr[0].aligned_at_setup, tr)
tr, dg = run(P(fill="next_open"), {M(10, 4): 1}, chain, {"CE2": FakeTrack("CE", M(10, 21))})
check("S2 next_open fill = the next minute's open", tr and tr[0].entry_min == M(10, 22) and tr[0].entry_px == 184, tr)

# S3: SL / TP on premium points, pessimistic order
ce = const(180)
ce.update({M(10, 30): 158, M(10, 31): 150})           # bar 10:30 low = 157 → SL 160 touched
chain = {"CE2": ("CE", 23050, ce)}
tr, _ = run(P(), {M(10, 4): 1}, chain, {"CE2": FakeTrack("CE", 0)})
check("S3 SL = entry − sl_pts, books AT the level", tr and tr[0].reason == "SL" and tr[0].exit_px == 160 and tr[0].exit_min == M(10, 30), tr)
ce = const(180)
ce.update({M(11, 0): 221})
tr, _ = run(P(), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce)}, {"CE2": FakeTrack("CE", 0)})
check("S3 TP = entry + tp_pts, books AT the level", tr and tr[0].reason == "TP" and tr[0].exit_px == 220, tr)
ce = const(180)
ce[M(11, 0)] = (180, 190, 170, 182)                     # touches 176 AND 184
tr, _ = run(P(sl_pts=4, tp_pts=4), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce)}, {"CE2": FakeTrack("CE", 0)})
check("S3 bar touching both → stop first (pessimistic)", tr and tr[0].reason == "SL" and tr[0].exit_min == M(11, 0)
      and tr[0].exit_px == 176, tr)
ce = const(180)
ce[M(11, 0)] = (150, 152, 149, 151)                     # gaps through the stop at the open
tr, _ = run(P(), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce)}, {"CE2": FakeTrack("CE", 0)})
check("S3 gap through the stop books the open (worse than the level)", tr and tr[0].exit_px == 150, tr)

# S4: opposite spot cross exits (XOVER) and arms the other side in the same minute
ce, pe = const(180), const(170)
ce[M(11, 9)] = 190
chain = {"CE2": ("CE", 23050, ce), "PE2": ("PE", 23000, pe)}
tr, dg = run(P(tp_pts=0, sl_pts=0), {M(10, 4): 1, M(11, 9): -1}, chain,
             {"CE2": FakeTrack("CE", 0), "PE2": FakeTrack("PE", 0)})
check("S4 down cross exits the CE at that close (XOVER)", len(tr) >= 1 and tr[0].reason == "XOVER" and tr[0].exit_min == M(11, 9) and tr[0].exit_px == 190, tr)
check("S4 …and buys the PE (already aligned) in the same minute", len(tr) == 2 and tr[1].side == "PE" and tr[1].entry_min == M(11, 9))
tr, dg = run(P(tp_pts=0, sl_pts=0, xover_exit=False), {M(10, 4): 1, M(11, 9): -1}, chain,
             {"CE2": FakeTrack("CE", 0), "PE2": FakeTrack("PE", 0)})
check("S4 xover_exit off: CE held to EOD, PE signal dropped (one position at a time)",
      len(tr) == 1 and tr[0].reason == "EOD" and dg.get("sig_dropped_open") == 1, (tr, dg))

# S5: a pending setup is REPLACED by the opposite cross; expires at the cutoff
chain = {"CE2": ("CE", 23050, const(180)), "PE2": ("PE", 23000, const(170))}
tr, dg = run(P(), {M(10, 4): 1, M(10, 29): -1}, chain,
             {"CE2": FakeTrack("CE", 10 ** 9), "PE2": FakeTrack("PE", M(10, 40))})
check("S5 opposite cross replaces the unfilled CE setup; PE fills later",
      len(tr) == 1 and tr[0].side == "PE" and tr[0].entry_min == M(10, 40) and dg.get("setups_replaced") == 1, (tr, dg))
tr, dg = run(P(), {M(14, 9): 1}, {"CE2": ("CE", 23050, const(180))}, {"CE2": FakeTrack("CE", M(14, 30))})
check("S5 option aligns at 14:30 → no entry (cutoff), setup expired", tr == [] and dg.get("setups_expired_cutoff") == 1, dg)
tr, dg = run(P(), {M(14, 9): 1}, {"CE2": ("CE", 23050, const(180))}, {"CE2": FakeTrack("CE", M(14, 29))})
check("S5 alignment on the 14:29 close is still in time", len(tr) == 1 and tr[0].entry_min == M(14, 29))
tr, dg = run(P(), {M(14, 34): 1}, {"CE2": ("CE", 23050, const(180))}, {"CE2": FakeTrack("CE", 0)})
check("S5 spot cross after the cutoff arms nothing", tr == [] and dg.get("sig_dropped_cutoff") == 1, dg)

# S6: band / direction / warm-up
tr, dg = run(P(), {M(10, 4): 1}, {"CE1": ("CE", 23000, const(240))}, {})
check("S6 nothing in band → no setup (counted)", tr == [] and dg.get("setup_no_band") == 1, dg)
tr, dg = run(P(direction="PE"), {M(10, 4): 1}, {"CE2": ("CE", 23050, const(180))}, {"CE2": FakeTrack("CE", 0)})
check("S6 direction PE ignores up crosses", tr == [] and dg.get("sig_dropped_dir") == 1)
tr, dg = run(P(), {M(9, 19): 1}, {"CE2": ("CE", 23050, const(180))}, {"CE2": FakeTrack("CE", 0, ready_k=M(10, 30))})
check("S6 option EMA not ready → waits; enters once ready", len(tr) == 1 and tr[0].entry_min == M(10, 30)
      and dg.get("entries_after_warmup_wait") == 1, (tr, dg))

# S7: daily caps — max trades, max losing trades
crosses = {M(9, 29): 1, M(9, 49): -1, M(10, 9): 1, M(10, 29): -1, M(10, 49): 1}
chain = {"CE2": ("CE", 23050, const(180)), "PE2": ("PE", 23000, const(170))}
tks = {"CE2": FakeTrack("CE", 0), "PE2": FakeTrack("PE", 0)}
tr, dg = run(P(max_trades=2), crosses, chain, tks)
check("S7 max trades/day = 2", len(tr) == 2 and dg.get("sig_dropped_trades", 0) >= 1, (len(tr), dg))
tr, dg = run(P(max_losses=2), crosses, chain, tks, net=lambda a, b: -100.0)
check("S7 max losing trades/day = 2 halts new entries", len(tr) == 2 and dg.get("days_halted_losses") == 1
      and dg.get("sig_dropped_losses", 0) >= 1, (len(tr), dg))
tr, dg = run(P(max_losses=2), crosses, chain, tks, net=lambda a, b: 100.0)
check("S7 winners do not count toward the losing cap", len(tr) == 5)

# S8: trailing SL
ce = const(180)
ce.update({M(10, 10): 195, M(10, 11): 205, M(10, 12): 212, M(10, 13): 199, M(10, 14): 196})
tr, _ = run(P(tp_pts=0, trail_on=True, trail_trigger=20, trail_gap=15), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce)},
            {"CE2": FakeTrack("CE", 0)})
# peak through 10:12 = 213 (high = close+1) → stop 198; 10:13 bar low 198 → TSL @ 198
check("S8 trailing stop arms at +trigger and books peak − gap", tr and tr[0].reason == "TSL" and tr[0].exit_px == 198
      and tr[0].exit_min == M(10, 13), tr)
tr, _ = run(P(tp_pts=0, trail_on=False), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce)}, {"CE2": FakeTrack("CE", 0)})
check("S8 same path without trail → held (EOD)", tr and tr[0].reason == "EOD")
ce2 = const(180)
ce2.update({M(10, 10): 230})                            # one bar: high 231 → the same bar's low must NOT hit a trail set by its own high
tr, _ = run(P(tp_pts=0, trail_on=True, trail_trigger=20, trail_gap=15), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce2)},
            {"CE2": FakeTrack("CE", 0)})
check("S8 trail uses the PREVIOUS bars' peak (no same-bar high→low)", tr and tr[0].exit_min == M(10, 11) and tr[0].reason == "TSL", tr)

# S9: EOD with a missing 15:15 bar → last mark; positions never cross the day
ce = {m: 180 for m in range(O, M(15, 12))}
tr, dg = run(P(), {M(10, 4): 1}, {"CE2": ("CE", 23050, ce)}, {"CE2": FakeTrack("CE", 0)})
check("S9 EOD at last mark when the square-off minute has no print", tr and tr[0].reason == "EOD" and tr[0].exit_px == 180
      and dg.get("stale_marks", 0) >= 1, (tr, dg))

# ═══════════════════════════ 3. runner on a corpus ═══════════════════════════
print("── runner: synthetic Black-76 corpus (replay suite generator) ──")
try:
    from app.backtest.replay import replay_synth
    from app.backtest.emc.backtest_emc_runner import run_emc_backtest, _merge_cfg, validate, DEFAULTS
    APP = True
except Exception as ex:   # pragma: no cover
    APP = False
    print(f"  SKIP  runner e2e (app tree unavailable: {ex!r})")

if APP:
    tmp = tempfile.mkdtemp(prefix="emc_sim_")
    db = os.path.join(tmp, "backtest.db")
    days = replay_synth.build(db, d0=date(2025, 1, 1), d1=date(2025, 2, 28), strikes_each_side=8, with_fut=False)
    import sqlite3
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    def opt_bar(sym, ts):
        r = con.execute("SELECT open, high, low, close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",
                        (sym, ts)).fetchone()
        return r

    def chain_close(day, side, ts):
        exp = expected_expiry_for_day(day).isoformat()
        return [(r["tradingsymbol"], r["close"]) for r in con.execute(
            "SELECT tradingsymbol, close FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type=? "
            "AND expiry=? AND ts=?", (side, exp, ts))]

    def IST_DATE(ts):
        return (datetime(1970, 1, 1) + timedelta(seconds=ts + IST)).date()

    base = {"sel_prem_min": 60, "sel_prem_max": 140}   # synthetic chain is cheaper than live NIFTY
    progress = []
    res = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                           date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close"),
                           progress_cb=progress.append)
    T = res["trades"]
    dg = res["summary"].get("diag_emc", {})
    check("runner produced trades on the synthetic corpus", len(T) >= 10, f"{len(T)} trades · {dg}")
    check("progress callback per session", len(progress) == dg.get("days_total"))
    ok_band = ok_hi = ok_ord = ok_time = ok_fill = ok_lvl = True
    bad = []
    for t in T:
        d = IST_DATE(t.entry_ts)
        sel = float(t.condition.split("sel₹")[1].split("·")[0])
        if not (60 <= sel <= 140):
            ok_band = False
        # selection minute = entry minute − wait (close fill)
        wait = 0 if "·aligned·" in t.condition else int(t.condition.split("·wait")[1].split("m")[0])
        sel_ts = t.entry_ts - wait * 60
        others = [c for s, c in chain_close(d, t.instrument_type, sel_ts) if 60 <= c <= 140]
        if others and abs(max(others) - sel) > 1e-6:
            ok_hi = False
            bad.append((t.tradingsymbol, sel, max(others)))
        if not (t.entry_ts < t.exit_ts or (t.exit_reason in ("SL", "TP", "TSL") and t.exit_ts > t.entry_ts)):
            ok_ord = False
        em = (t.entry_ts - ds_for(d)) // 60
        xm = (t.exit_ts - ds_for(d)) // 60
        if em >= 14 * 60 + 30 or xm > 15 * 60 + 15 or IST_DATE(t.exit_ts) != d:
            ok_time = False
        eb = opt_bar(t.tradingsymbol, t.entry_ts)
        if eb is None or abs(eb["close"] - t.entry_price) > 0.011:
            ok_fill = False
        if t.exit_reason == "SL" and t.exit_price > t.entry_price - 20 + 0.011:
            ok_lvl = False
        if t.exit_reason == "TP" and t.exit_price < t.entry_price + 40 - 0.011:
            ok_lvl = False
        if t.exit_reason == "EOD":
            xb = opt_bar(t.tradingsymbol, t.exit_ts)
            if xb is not None and abs(xb["open"] - t.exit_price) > 0.011:
                ok_lvl = False
        if t.exit_reason == "XOVER":
            xb = opt_bar(t.tradingsymbol, t.exit_ts)
            if xb is not None and abs(xb["close"] - t.exit_price) > 0.011:
                ok_lvl = False
    check("every selection premium inside the band", ok_band)
    check("every selection is the HIGHEST in-band premium of its side at that minute", ok_hi, bad[:3])
    check("exit strictly after entry", ok_ord)
    check("no entry at/after 14:30, no exit after 15:15, no overnight", ok_time)
    check("close-fill entry price = the option's 1m close", ok_fill)
    check("SL/TP/EOD/XOVER exit prices obey their conventions", ok_lvl)
    srt = sorted(T, key=lambda x: x.entry_ts)
    check("one position at a time", all(a.exit_ts <= b.entry_ts for a, b in zip(srt, srt[1:])))
    reasons = {t.exit_reason for t in T}
    check("several exit kinds exercised", len(reasons) >= 3, reasons)
    check("expiry is the expected weekly", all(t.expiry == expected_expiry_for_day(IST_DATE(t.entry_ts)).isoformat() for t in T))
    check("net = gross − charges on every trade", all(abs(t.net_pnl - (t.pnl - t.charges)) < 0.02 for t in T))
    check("both option-trigger paths used (aligned at setup / after wait)",
          dg.get("entries_aligned_at_setup", 0) > 0 and dg.get("entries_after_wait", 0) > 0, dg)

    # next_open fill
    r2 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base))
    ok = True
    for t in r2["trades"]:
        b = opt_bar(t.tradingsymbol, t.entry_ts)
        if b is None or abs(b["open"] - t.entry_price) > 0.011:
            ok = False
    check("default next_open fill = the following minute's option open", ok and r2["trades"])

    # caps
    r3 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, max_trades_per_day=1))
    per = {}
    for t in r3["trades"]:
        per[IST_DATE(t.entry_ts)] = per.get(IST_DATE(t.entry_ts), 0) + 1
    check("max_trades_per_day = 1 honoured", per and max(per.values()) == 1)
    r4 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, max_losses_per_day=1))
    ok = True
    by = {}
    for t in sorted(r4["trades"], key=lambda x: x.entry_ts):
        by.setdefault(IST_DATE(t.entry_ts), []).append(t)
    for d, ts in by.items():
        losers = [i for i, t in enumerate(ts) if t.net_pnl < 0]
        if losers and losers[0] != len(ts) - 1:
            ok = False
    check("max_losses_per_day = 1: nothing is entered after the day's first loser", ok and by)
    # cutoff / eod knobs
    r5 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, entry_cutoff="12:00", eod_square_off="14:00"))
    check("entry cutoff 12:00 / EOD 14:00 honoured",
          r5["trades"] and all((t.entry_ts - ds_for(IST_DATE(t.entry_ts))) // 60 < 720 and
                               (t.exit_ts - ds_for(IST_DATE(t.exit_ts))) // 60 <= 840 for t in r5["trades"]))
    # trailing
    r6 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28),
                          config_override=dict(base, tp_pts=0, trail_on=True, trail_trigger_pts=8, trail_gap_pts=6))
    check("trailing SL produces TSL exits", any(t.exit_reason == "TSL" for t in r6["trades"]))
    # skip-expiry must not perturb the spot EMA on other days (R1)
    r7 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close", skip_expiry_day=True))
    exp_days = {d for d in days if expected_expiry_for_day(d) == d}
    keep = [(t.tradingsymbol, t.entry_ts, t.exit_ts) for t in T if IST_DATE(t.entry_ts) not in exp_days]
    got = [(t.tradingsymbol, t.entry_ts, t.exit_ts) for t in r7["trades"]]
    check("skip-expiry = same trades minus expiry days (spot EMA keeps advancing)", got == keep and exp_days,
          (len(got), len(keep)))
    # option bar_close mode never enters mid-bar (close fill, opt_tf 5)
    r8 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close", opt_check="bar_close"))
    check("bar_close mode: entries only on 5m bar closes",
          r8["trades"] and all(((t.entry_ts - ds_for(IST_DATE(t.entry_ts))) // 60 - O + 1) % 5 == 0 for t in r8["trades"]))
    # lots / compounding hooks
    r9 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close", lots=3))
    check("lots scale qty (3 × 65)", r9["trades"] and all(t.qty == 195 for t in r9["trades"]))
    r10 = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                           date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close", dte_lot_mult="0:0"))
    check("DTE × lots 0:0 skips expiry days", r10["summary"]["diag_emc"].get("dte_skipped_days", 0) >= 1
          and not any(IST_DATE(t.entry_ts) in exp_days for t in r10["trades"]))

    print("── FIX1: every entry is armed by a TRUE spot crossover (independent recompute) ──")
    from app.backtest.emc.emc_v1_engine import ema_series as _es
    from app.backtest.emc.backtest_emc_runner import _day_start_epoch as _dse
    allspot = sorted({IST_DATE(r["ts"]) for r in con.execute(
        "SELECT ts FROM backtest_candles_1m WHERE underlying='NIFTY' AND instrument_type='SPOT'")})
    def xmap(from_day, warm=5):
        prior = [d for d in allspot if d < from_day][-warm:]
        seq = []
        for d in prior + [d for d in allspot if d >= from_day]:
            ds_ = _dse(d)
            rows_ = [NS(ts=r["ts"], open=r["open"], high=r["high"], low=r["low"], close=r["close"]) for r in con.execute(
                "SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE underlying='NIFTY' "
                "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts", (ds_, ds_ + 86400))]
            for i_, b in enumerate(resample_session(rows_, day_start_epoch=ds_, tf_minutes=5)):
                seq.append((d, b.end_min, b.close, i_ == 0))
        f_, s_ = _es([x[2] for x in seq], 9), _es([x[2] for x in seq], 21)
        out_ = {}
        for i_ in range(1, len(seq)):
            if None in (f_[i_ - 1], s_[i_ - 1], f_[i_], s_[i_]):
                continue
            if f_[i_ - 1] <= s_[i_ - 1] and f_[i_] > s_[i_]:
                out_[(seq[i_][0], seq[i_][1])] = ("CE", seq[i_][3])
            elif f_[i_ - 1] >= s_[i_ - 1] and f_[i_] < s_[i_]:
                out_[(seq[i_][0], seq[i_][1])] = ("PE", seq[i_][3])
        return out_
    XM = xmap(date(2025, 1, 15))
    def arm_of(t):
        hh_, mm_ = t.condition.split("·arm")[1][:5].split(":")
        return int(hh_) * 60 + int(mm_)
    bad = [(t.tradingsymbol, t.condition) for t in T
           if XM.get((IST_DATE(t.entry_ts), arm_of(t)), (None,))[0] != t.instrument_type
           or arm_of(t) > (t.entry_ts - ds_for(IST_DATE(t.entry_ts))) // 60]
    check(f"every trade's arm minute is a genuine EMA9/21 crossover of its side, at/before entry ({len(T)} trades)",
          not bad and T, bad[:3])
    # no other cross of either side between the arm and the entry (else the setup would have been replaced)
    bad2 = []
    for t in T:
        d_, a_, e_ = IST_DATE(t.entry_ts), arm_of(t), (t.entry_ts - ds_for(IST_DATE(t.entry_ts))) // 60
        if any(k[0] == d_ and a_ < k[1] < e_ for k in XM):
            bad2.append(t.condition)
    check("no spot cross between a trade's arming cross and its entry", not bad2, bad2[:3])
    gap_trades = [t for t in T if XM.get((IST_DATE(t.entry_ts), arm_of(t)), (None, False))[1]]
    rg = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close", first_candle_cross=False))
    dgg = rg["summary"]["diag_emc"]
    check(f"FIX1: gap crosses exist on the corpus and are counted ({dg.get('gap_crosses')}; {len(gap_trades)} trades armed by one)",
          dg.get("gap_crosses", 0) > 0 and gap_trades)
    check("FIX1: first_candle_cross=False ignores every gap cross and arms nothing from them",
          dgg.get("gap_crosses_ignored") == dgg.get("gap_crosses") > 0
          and not [t for t in rg["trades"] if arm_of(t) == O + 4])
    check("FIX1: trades not armed by a gap cross are unaffected on days without one",
          [x.condition for x in rg["trades"] if all(not (k[0] == IST_DATE(x.entry_ts) and v[1]) for k, v in XM.items())]
          == [x.condition for x in T if all(not (k[0] == IST_DATE(x.entry_ts) and v[1]) for k, v in XM.items())])
    check("FIX1: default still counts gap crosses (existing runs reproduce)", DEFAULTS["first_candle_cross"] is True)

    print("── runner: config guards (abort, never run) ──")
    for name, over in (("fast >= slow", {"ema_fast": 21, "ema_slow": 9}),
                       ("band inverted", {"sel_prem_min": 200, "sel_prem_max": 150}),
                       ("cutoff after eod", {"entry_cutoff": "15:20", "eod_square_off": "15:15"}),
                       ("eod after 15:29", {"eod_square_off": "15:40"}),
                       ("bad time text", {"entry_cutoff": "2pm"}),
                       ("sl looks like a %", {"sl_pts": 250}),
                       ("trail without gap", {"trail_on": True, "trail_gap_pts": 0}),
                       ("tf 90", {"spot_tf": 90})):
        r = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 2, 3),
                             date_to=date(2025, 2, 4), config_override=over)
        check(f"abort: {name}", r.get("aborted") and not r["trades"], r.get("reason"))
    r = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 2, 3),
                         date_to=date(2025, 2, 28), config_override=base, cancel_cb=lambda: True)
    check("cancel_cb stops before trading", not r["trades"])
    cfg = _merge_cfg({"trail_on": "false", "direction": "x", "opt_check": "BAR_CLOSE"})
    check("merge: string booleans / unknown enums normalised", cfg["trail_on"] is False and cfg["direction"] == "BOTH"
          and cfg["opt_check"] == "bar_close")
    check("defaults are the spec (5m, EMA 9/21, ₹150–200 highest, cutoff 14:30, EOD 15:15)",
          DEFAULTS["spot_tf"] == 5 and DEFAULTS["opt_tf"] == 5 and DEFAULTS["ema_fast"] == 9 and DEFAULTS["ema_slow"] == 21
          and DEFAULTS["sel_prem_min"] == 150 and DEFAULTS["sel_prem_max"] == 200 and DEFAULTS["sel_pick"] == "highest"
          and DEFAULTS["entry_cutoff"] == "14:30" and DEFAULTS["eod_square_off"] == "15:15")
    print("── replay: persisted run → Trade Replay (EMC overlay) ──")
    from pathlib import Path
    import app.backtest.repo.backtest_repo as BR
    import app.backtest.replay.replay_core as RC
    import app.backtest.replay.overlays as OV
    BR._db_path = lambda: Path(db)
    RC.runs_db_path = lambda: db
    check("EMC_V1 registered in the replay overlay registry", "EMC_V1" in OV.REGISTRY)
    rr = run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=date(2025, 1, 15),
                          date_to=date(2025, 2, 28), config_override=dict(base, fill_mode="close"))
    rr["meta"] = {"strategy_id": "EMC_V1", "underlying": "NIFTY", "date_from": "2025-01-15", "date_to": "2025-02-28"}
    rid = BR.persist_run(rr)
    rows = [dict(r) for r in con.execute("SELECT * FROM backtest_trades WHERE run_id=? ORDER BY entry_ts, id", (rid,))]
    check("persist_run stores every EMC trade with its condition", len(rows) == len(rr["trades"]) and
          all(r.get("condition", "").startswith("EMC·") for r in rows))
    ok_tier = ok_x = ok_opt = ok_arm = ok_win = ok_frame = True
    n = 0
    for r in rows[:12]:
        out = RC.build_replay(rid, int(r["id"]))
        if not out.get("ok", True) or "spot" not in out:
            ok_tier = False
            continue
        n += 1
        par = (out.get("parity") or {}).get("tier") or (out.get("meta") or {}).get("parity", {}).get("tier")
        sp = {o.get("id"): o for o in out["spot"]["overlays"]}
        if not (sp.get("ema_fast", {}).get("points") and sp.get("ema_slow", {}).get("points")):
            ok_tier = False
        # the arming cross: selection minute = entry − wait (close fill); its spot bar starts 4 min earlier
        cond = r["condition"]
        hh, mm = cond.split("·arm")[1][:5].split(":")
        d_ = IST_DATE(int(r["entry_ts"]))
        bar_ts = ds_for(d_) + (int(hh) * 60 + int(mm) - 4) * 60
        dots = [p for p in sp.get("crosses", {}).get("points", []) if p[0] == bar_ts]
        if not dots or (("▲" in dots[0][2]) != (r["instrument_type"] == "CE")):
            ok_x = False
        armed = sp.get("armed", {}).get("points", [])
        if not armed or armed[0][0] != bar_ts or f"ARMED {r['instrument_type']}" not in armed[0][2]:
            ok_arm = False
        if len(out["window"]["days"]) != 2 or out["window"]["days"][1] != d_.isoformat():
            ok_win = False
        if out["window"].get("frame_from_ts") != bar_ts:
            ok_frame = False
        po = out["premium"]["overlays"].get(r["tradingsymbol"], [])
        if not any(o.get("id") == "opt_ema_slow" and o.get("points") for o in po):
            ok_opt = False
    check(f"replay builds with spot EMA lines ({n} trades)", ok_tier and n)
    check("every replayed entry's arming spot cross is drawn at its bar, same direction", ok_x)
    check("the traded option's own EMA pair is drawn on the premium pane", ok_opt)
    check("FIX1: the ARMED marker sits on the arming spot candle", ok_arm)
    check("FIX1: the previous session is drawn before the entry day", ok_win)
    check("FIX1: 'Trade' framing starts at the arming candle", ok_frame)
    con.close()

if FAILS:
    print(f"\n{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print(f"\nALL {NPASS[0]} EMC CHECKS PASSED")
