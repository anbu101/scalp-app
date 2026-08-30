# backend/app/engine/vet/test_vet_live_signal_engine.py
#
# ── VET_V1 LIVE SIGNAL ENGINE PARITY TESTS ──
# ============================================================================
# The claim being tested is the one the whole live integration rests on:
# feeding the live engine 1m spot candles ONE AT A TIME produces exactly the
# conditions the backtest computes when handed the entire day at once.
#
# The test therefore builds a randomized multi-session 1m spot series, then:
#   A. runs the BACKTEST path — resample the whole warmup+day, vet_states once
#   B. runs the LIVE path — on_minute() per candle, collecting emitted signals
# and asserts every emitted (bar_ts, condition) matches the backtest's value
# for that bar. A mismatch here is a live/backtest divergence, which is the
# failure mode that makes a backtest worthless.
#
# Also asserted:
#   * signals fire ONLY at completed 5m boundaries, never mid-bucket
#   * short warmup BLOCKS trading rather than emitting divergent signals
#   * out-of-order / duplicate minutes are ignored, not folded
#   * the session anchor is 09:15 IST (a wrong anchor shifts every bar)
#
# Runs standalone:  python3 test_vet_live_signal_engine.py
# ============================================================================

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')))

try:
    from app.backtest.gc.gc_v1_engine import resample_spot
    from app.backtest.vet.vet_v1_engine import vet_states
    from app.engine.vet.vet_live_core import ENTER, FLIP, HOLD
    from app.engine.vet.vet_live_signal_engine import (
        IST_OFFSET, VetLiveSignalEngine, session_start_epoch)
except ImportError:                                        # pragma: no cover
    from gc_v1_engine import resample_spot                  # type: ignore
    from vet_v1_engine import vet_states                    # type: ignore
    from vet_live_core import ENTER, FLIP, HOLD             # type: ignore
    from vet_live_signal_engine import (                    # type: ignore
        IST_OFFSET, VetLiveSignalEngine, session_start_epoch)

F = 0


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    F += 0 if cond else 1


def make_session(day_index: int, px: float, minutes: int = 375, seed: int = 0):
    """One session of 1m spot candles on the 09:15 IST grid."""
    rnd = random.Random(seed)
    base = 1780000000 // 86400 * 86400 - IST_OFFSET + day_index * 86400
    start = base + (9 * 60 + 15) * 60
    rows = []
    for i in range(minutes):
        o = px
        c = px + rnd.gauss(0, 9)
        rows.append({"ts": start + i * 60, "open": o,
                     "high": max(o, c) + abs(rnd.gauss(0, 3)),
                     "low": min(o, c) - abs(rnd.gauss(0, 3)), "close": c})
        px = c
    return rows, px


# ── build 12 sessions: 11 warmup + 1 traded ───────────────────────────────
sessions, px = [], 24000.0
for d in range(12):
    rows, px = make_session(d, px, seed=1000 + d)
    sessions.append(rows)
warmup = [r for s in sessions[:11] for r in s]
today = sessions[11]

CFG = {"trend_len": 40, "range_len": 0.618, "signal_tf": 5,
       "warmup_sessions": 10, "leg_action": "BUY"}

print("── 1. session anchoring ──")
anchor = session_start_epoch(today[100]["ts"])
mins = ((anchor + IST_OFFSET) % 86400) // 60
chk("session anchor is 09:15 IST", mins == 9 * 60 + 15,
    f"got {mins // 60:02d}:{mins % 60:02d}")
chk("anchor equals the day's first candle ts", anchor == today[0]["ts"])

print("\n── 2. LIVE (minute by minute) vs BACKTEST (whole day at once) ──")
# BACKTEST path
bt_bars = []
for s in sessions:
    bt_bars += resample_spot(s, 5, session_start_epoch(s[0]["ts"]))
bt_states = vet_states(bt_bars, trend_len=40, range_len=0.618)
bt_cond = {b.ts: st.condition for b, st in zip(bt_bars, bt_states)}

# LIVE path
eng = VetLiveSignalEngine(CFG, warmup_1m=warmup)
emitted = []
for r in today:
    sig = eng.on_minute(r)
    if sig:
        emitted.append(sig)
chk("live emitted signals for the day", len(emitted) > 0, len(emitted))
bad = [s for s in emitted if bt_cond.get(s["bar_ts"]) != s["condition"]]
chk(f"every emitted bar's condition matches the backtest "
    f"({len(emitted)} bars)", not bad,
    [(s["bar_ts"], s["condition"], bt_cond.get(s["bar_ts"]))
     for s in bad[:3]])
exp_bars = [b.ts for b in resample_spot(today, 5, anchor)]
chk("one signal per completed 5m bucket, in order",
    [s["bar_ts"] for s in emitted] == exp_bars[:len(emitted)],
    f"{[s['bar_ts'] for s in emitted][:3]} vs {exp_bars[:3]}")

print("\n── 3. signals fire only on COMPLETED buckets ──")
eng2 = VetLiveSignalEngine(CFG, warmup_1m=warmup)
fired_at = []
for i, r in enumerate(today[:40]):
    if eng2.on_minute(r):
        fired_at.append(i)
chk("no signal before the 5th minute of the session",
    all(i >= 4 for i in fired_at), fired_at[:5])
chk("signals land on every 5th minute",
    all((i + 1) % 5 == 0 for i in fired_at), fired_at[:8])

print("\n── 4. refusals are refusals, not guesses ──")
short = VetLiveSignalEngine(CFG, warmup_1m=[r for s in sessions[:3]
                                            for r in s])
for r in today[:60]:
    short.on_minute(r)
d = short.decide(None)
chk("short warmup BLOCKS trading", d["action"] == HOLD and d["blocked"],
    d)
chk("the block names the shortfall", "warmup" in (d["blocked"] or ""),
    d["blocked"])
chk("full warmup does not block",
    eng.decide(None)["blocked"] is None, eng.decide(None))

eng3 = VetLiveSignalEngine(CFG, warmup_1m=warmup)
for r in today[:30]:
    eng3.on_minute(r)
before = eng3.status()["bars_today"]
eng3.on_minute(today[10])                    # replayed old minute
eng3.on_minute(today[29])                    # duplicate of the last
chk("out-of-order and duplicate minutes are ignored",
    eng3.status()["bars_today"] == before, eng3.status()["bars_today"])

eng3.guard.frozen = True
eng3.guard.reason = "test"
chk("a frozen guard refuses to act",
    eng3.decide("CE")["action"] == HOLD
    and eng3.decide("CE")["blocked"] == "frozen")

print("\n── 5. the decision rule rides on top, unchanged ──")
eng4 = VetLiveSignalEngine(dict(CFG, leg_action="SELL"), warmup_1m=warmup)
for r in today:
    eng4.on_minute(r)
sig = eng4.latest_signal()
dec_buy = eng.decide(None)
dec_sell = eng4.decide(None)
chk("BUY and SELL read the SAME condition", sig["condition"]
    == eng.latest_signal()["condition"])
if dec_buy["action"] == ENTER and dec_sell["action"] == ENTER:
    chk("...but take opposite contracts",
        dec_buy["side"] != dec_sell["side"],
        (dec_buy["side"], dec_sell["side"]))
else:
    chk("both legs reach the same action on the same bar",
        dec_buy["action"] == dec_sell["action"],
        (dec_buy["action"], dec_sell["action"]))
chk("holding the signalled side is HOLD, not a re-entry",
    eng.decide(dec_buy["side"])["action"] == HOLD
    if dec_buy["side"] else True)

print("\n" + ("ALL SIGNAL-ENGINE PARITY CHECKS PASSED" if F == 0
              else f"{F} FAILURES"))
sys.exit(1 if F else 0)