# backend/app/engine/orb/test_orb_live_core.py
#
# ── ORB_V1 LIVE CORE TESTS ── Fence: ORB_LIVE_20260903
# VET donor pattern: section 2 drives 1m candles ONE AT A TIME and asserts
# the incrementally-observed stream equals the whole-day backtest
# computation. Run standalone: python3 test_orb_live_core.py

from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orb_live_core import OrbLiveDay, PrefixGuard          # noqa: E402
try:
    from app.backtest.orb.orb_v1_engine import OrbBar, orb_signals, SESSION_OPEN_MIN
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "backtest", "orb"))
    from orb_v1_engine import OrbBar, orb_signals, SESSION_OPEN_MIN  # type: ignore

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)

DS = 1_768_000_000 - (1_768_000_000 % 86400)   # any midnight-aligned epoch
def m1(minute, o, h, l, c):
    return OrbBar(DS + (SESSION_OPEN_MIN + minute) * 60, o, h, l, c)

SEALED = {"orb_minutes": 15, "timeframe_minutes": 5, "trigger_source": "high",
          "breakout_buffer_pts": 0, "direction": "BOTH",
          "both_side_policy": "pessimistic", "spot_sl_mode": "points",
          "sl_dist_mode": "pct", "sl_points": 9.174311926605505,  # 10 pts @109
          "spot_sl_trigger": "close", "target_mode": "pct", "target_value": 50,
          "entry_block_time": "12:00", "eod_square_off": "13:00",
          "max_trades_per_day": 2, "max_trades_per_side": 1}

def scripted_day():
    bars = [m1(k, 105, 110, 100, 105) for k in range(15)]          # ORB 100..110
    bars += [m1(k, 104, 106, 103, 105) for k in range(15, 20)]
    bars.append(m1(20, 106, 110.5, 105, 109))                      # touch -> UP
    bars += [m1(k, 109, 111, 108, 110) for k in range(21, 40)]
    bars.append(m1(40, 108, 109, 97.0, 98.0))                      # CLOSES thru 99
    bars += [m1(k, 109, 110, 108, 109) for k in range(41, 230)]    # to 13:05
    return bars

print("── section 1: day lifecycle on the scripted day (interleaved fills) ──")
day = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
stream = []
pos = None
for b in scripted_day():
    for a in day.process(b):
        stream.append((day._m(b.ts) if a[0] != "LEVELS" else -1, a))
        if a[0] == "SIGNAL":                     # manager fills at next 1m open
            pos = day.on_entry_fill(side=a[1], symbol="TESTCE", entry_px=172.0,
                                    entry_spot=109.0,
                                    entry_ts=a[2] + 60)
        elif a[0] in ("STOP_CLOSE_BREACH", "EOD_SQUARE_OFF"):
            day.on_position_closed()
kinds = [a[0] for _, a in stream]
check("levels lock at the first post-window bar",
      kinds[0] == "LEVELS" and stream[0][1][1:] == (110.0, 100.0))
sig = [a for _, a in stream if a[0] == "SIGNAL"]
check("exactly one SIGNAL, UP side, at the m20 touch bar",
      len(sig) == 1 and sig[0][1] == "CE"
      and (sig[0][2] - DS) // 60 == SESSION_OPEN_MIN + 20)
check("stop level = entry_spot − 10.0 (pct arithmetic to the paisa)",
      pos is not None and abs(pos.sl_spot - 99.0) < 1e-9, str(pos and pos.sl_spot))
check("TP limit level = entry × 1.5", pos is not None and abs(pos.tp_prem - 258.0) < 1e-9)
breach = [(m, a) for m, a in stream if a[0] == "STOP_CLOSE_BREACH"]
check("wick minutes ignored; breach fires ONLY on the m40 closing bar",
      len(breach) == 1 and breach[0][0] == SESSION_OPEN_MIN + 40, str(breach))
check("the simultaneous PE break at m40 is dropped while the position is open",
      day.dropped_open >= 1)
check("no spurious EOD after the stop closed the position",
      "EOD_SQUARE_OFF" not in kinds)

print("── section 2: incremental == whole-day (parity by construction) ──")
full = orb_signals(scripted_day(), day_start_epoch=DS, orb_high=110, orb_low=100,
                   orb_minutes=15, tf_minutes=5)
check("guard-observed stream equals the whole-day engine run",
      day.guard.sig_seen == [(s.ts, s.side, s.ambiguous, s.rearm_entry)
                             for s in full])
day2 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
half = scripted_day()[:60]
for b in half:
    day2.process(b)
check("prefix stream is a prefix of the whole-day stream (append-only)",
      day2.guard.sig_seen == [(s.ts, s.side, s.ambiguous, s.rearm_entry)
                              for s in orb_signals(half, day_start_epoch=DS,
                                                   orb_high=110, orb_low=100,
                                                   orb_minutes=15, tf_minutes=5)])

print("── section 3: guards fail closed ──")
d3 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
try:
    d3.process(OrbBar(DS + (SESSION_OPEN_MIN * 60) + 1, 1, 1, 1, 1))
    check("unaligned ts raises (LD1 / VET gross-0 scar)", False)
except ValueError:
    check("unaligned ts raises (LD1 / VET gross-0 scar)", True)
d3 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
d3.process(m1(0, 105, 110, 100, 105))
acts = d3.process(m1(0, 105, 110, 100, 106))               # restated OHLC
check("restated bar freezes the day",
      acts and acts[0][0] == "FROZEN" and d3.guard.frozen)
check("frozen day emits nothing further",
      d3.process(m1(1, 105, 110, 100, 105)) == [])
d3 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
d3.process(m1(0, 105, 110, 100, 105))
check("identical redelivery is idempotent, not a freeze",
      d3.process(m1(0, 105, 110, 100, 105)) == [] and not d3.guard.frozen)
d4 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
# whole 5m bucket (m5..m9) missing — per-BUCKET fail-closed, exactly the
# backtest's rule (a 4/5-minute bucket still counts; a missing bucket kills)
gap = [m1(k, 105, 110, 100, 105) for k in range(5)] + \
      [m1(k, 105, 110, 100, 105) for k in range(10, 15)] + \
      [m1(16, 104, 106, 103, 105), m1(17, 104, 106, 103, 105)]
ref = []
for b in gap:
    ref += d4.process(b)
check("missing window bucket refuses the day (fail-closed)",
      any(a[0] == "DAY_REFUSED" for a in ref) and d4.refused)

print("── section 4: budgets and pending slots (LD6) ──")
d5 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED, max_trades_per_side=2))
seq = [m1(k, 105, 110, 100, 105) for k in range(15)]
seq += [m1(k, 104, 106, 103, 105) for k in range(15, 20)]
seq.append(m1(20, 106, 110.5, 105, 108))                   # touch 1, close inside
seq += [m1(k, 107, 109, 106, 108) for k in range(21, 25)]  # 5m closes back in -> re-arm
seq.append(m1(26, 108, 110.6, 107, 110))                   # touch 2 after re-arm
outs = []
for b in seq:
    outs += d5.process(b)
sigs5 = [a for a in outs if a[0] == "SIGNAL"]
check("second signal while the first is PENDING is dropped (one at a time)",
      len(sigs5) == 1 and d5.dropped_open == 1, str((len(sigs5), d5.dropped_open)))
d5.on_entry_abandoned()
check("abandoned entry releases the slot without consuming budget",
      d5.pending_side is None and d5.day_trades == 0)
d6 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))
for b in seq:
    d6.process(b)
d6.on_entry_fill(side="CE", symbol="X", entry_px=172.0, entry_spot=109.0,
                 entry_ts=DS + (SESSION_OPEN_MIN + 21) * 60)
d6.on_position_closed()
outs6 = []
for b in [m1(27, 107, 108, 106, 107.5)] + [m1(k, 107, 109, 106, 108) for k in range(28, 32)] \
         + [m1(33, 108, 110.7, 107, 110)]:
    outs6 += d6.process(b)
check("per-side budget 1: a fresh CE signal after the CE trade is dropped",
      not any(a[0] == "SIGNAL" for a in outs6) and d6.dropped_budget >= 1)

print("── section 5: block time (LD2 entry minute rule) ──")
d7 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED, entry_block_time="09:36"))
outs7 = []
for b in scripted_day()[:30]:
    outs7 += d7.process(b)
check("signal whose NEXT-minute entry lands at/after the block is dropped",
      not any(a[0] == "SIGNAL" for a in outs7) and d7.dropped_block == 1)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("ALL CHECKS PASSED")
