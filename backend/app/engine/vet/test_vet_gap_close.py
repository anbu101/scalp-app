# backend/app/engine/vet/test_vet_gap_close.py
#
# ── VET_GAP_CLOSE_20261102 ── a 5m bucket whose LAST 1m spot candle never
# arrives is decided when the next bucket's first minute arrives (1 minute
# late), not skipped until the next bucket ends (5 minutes late — 2026-11-02
# Velvet: backtest flip 14:39, app flip 14:44).
#   cd backend && PYTHONPATH=$PWD python3 app/engine/vet/test_vet_gap_close.py
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from app.backtest.gc.gc_v1_engine import resample_spot                  # noqa: E402
from app.backtest.vet.vet_v1_engine import vet_states                   # noqa: E402
from app.engine.vet.vet_live_signal_engine import (                     # noqa: E402
    IST_OFFSET, VetLiveSignalEngine, session_start_epoch)

F = 0


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  {detail}"))
    F += 0 if cond else 1


def make_session(day_index, px, seed):
    rnd = random.Random(seed)
    base = 1780000000 // 86400 * 86400 - IST_OFFSET + day_index * 86400
    start = base + (9 * 60 + 15) * 60
    rows = []
    for i in range(375):
        o, c = px, px + rnd.gauss(0, 9)
        rows.append({"ts": start + i * 60, "open": o, "high": max(o, c) + abs(rnd.gauss(0, 3)),
                     "low": min(o, c) - abs(rnd.gauss(0, 3)), "close": c})
        px = c
    return rows, px


sessions, px = [], 24000.0
for d in range(12):
    rows, px = make_session(d, px, 1000 + d)
    sessions.append(rows)
WARM = [r for s in sessions[:11] for r in s]
TODAY = sessions[11]
CFG = {"trend_len": 40, "range_len": 0.618, "signal_tf": 5, "warmup_sessions": 10, "leg_action": "SELL"}
A = session_start_epoch(TODAY[0]["ts"])


def feed(rows):
    eng = VetLiveSignalEngine(CFG, warmup_1m=WARM)
    out = {}
    for r in rows:
        s = eng.on_minute(r)
        if s:
            out[s["bar_ts"]] = ((r["ts"] - A) // 60, s["condition"], s["last1m_ts"])
    return eng, out


def bt_conditions(rows):
    """The backtest on the SAME 1m data (whole day at once)."""
    bars = []
    for s in sessions[:11]:
        bars += resample_spot(s, 5, session_start_epoch(s[0]["ts"]))
    bars += resample_spot(rows, 5, A)
    return {b.ts: st.condition for b, st in zip(bars, vet_states(bars, trend_len=40, range_len=0.618))}


print("── 1. complete data: unchanged")
eng, full = feed(TODAY)
chk("one decision per bucket, each at its own last minute",
    all(m == (bts - A) // 60 + 4 for bts, (m, _, _) in full.items()) and len(full) == 75, len(full))
chk("no gap closes", eng.status()["gap_closes"] == 0)
bt = bt_conditions(TODAY)
chk("every condition equals the backtest's", all(bt[b] == c for b, (_, c, _) in full.items()))

print("── 2. one bucket's last minute missing (the 14:39 case)")
k = 64                                               # 14:35–14:40
gap = [r for i, r in enumerate(TODAY) if i != k * 5 + 4]
eng, got = feed(gap)
bk, bk1 = A + k * 300, A + (k + 1) * 300
chk("that bar is decided at the next bucket's FIRST minute (1 min late, was never)",
    got.get(bk, (None,))[0] == k * 5 + 5, got.get(bk))
chk("…on the minutes it has (last 1m = the bucket's 4th minute)", got[bk][2] == A + (k * 5 + 3) * 60)
chk("the next bar is still decided at its own last minute", got.get(bk1, (None,))[0] == (k + 1) * 5 + 4)
bt_gap = bt_conditions(gap)
chk("conditions equal the backtest run on the same (gapped) data",
    all(bt_gap[b] == c for b, (_, c, _) in got.items()))
chk("gap_closes counted", eng.status()["gap_closes"] == 1)
chk("the prefix guard never froze", not eng.guard.frozen)

print("── 3. two consecutive buckets each missing their last minute")
gap2 = [r for i, r in enumerate(TODAY) if i not in (k * 5 + 4, (k + 1) * 5 + 4)]
eng, got2 = feed(gap2)
chk("both decided one minute late", got2[bk][0] == k * 5 + 5 and got2[bk1][0] == (k + 1) * 5 + 5)
chk("…and nothing skipped (75 bars, 2 gap closes)", len(got2) == 75 and eng.status()["gap_closes"] == 2)

print("── 4. late / duplicate minutes still ignored")
eng = VetLiveSignalEngine(CFG, warmup_1m=WARM)
for r in gap[:k * 5 + 6]:
    eng.on_minute(r)
n = eng.status()["bars_today"]
chk("the missing minute arriving late is ignored (no re-decision)",
    eng.on_minute(TODAY[k * 5 + 4]) is None and eng.status()["bars_today"] == n)

print("\n" + ("ALL VET GAP-CLOSE CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
