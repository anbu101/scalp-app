# backend/app/engine/tma2/test_tma_gap_close.py
#
# ── TMA_GAP_CLOSE_20261102 ── TMA_V1 / TMA_V2 live signal engines: a 5m bucket
# whose LAST 1m spot candle never arrives is closed by the next minute — its
# signal is emitted one minute late (fresh, not stale) and the EMA stream keeps
# every bar. Before: the bucket was dropped for the rest of the day (signal
# lost, every later EMA one bar short).
# Run standalone:  python3 test_tma_gap_close.py
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "backtest" / "tma"))
sys.path.insert(0, str(HERE.parents[2] / "backtest" / "pst"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "tma"))

from tma2_live_signal_engine import TMA2LiveSignalEngine         # noqa: E402
from tma_live_signal_engine import TMALiveSignalEngine           # noqa: E402

FAILS = []
DAY = 1_700_000_000 - (1_700_000_000 % 86400)


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def session(day_start, rng, base):
    out, px = [], base
    t0 = day_start + (9 * 60 + 15) * 60
    trend = rng.choice([-1, 1]) * rng.uniform(0.05, 0.4)
    for i in range(375):
        if rng.random() < 0.01:
            trend = rng.choice([-1, 1]) * rng.uniform(0.05, 0.5)
        drift = trend + rng.uniform(-2.5, 2.5) + 3 * math.sin(i / 40)
        o, cl = px, px + drift
        out.append({"ts": t0 + i * 60, "open": round(o, 2), "high": round(max(o, cl) + rng.uniform(0, 1), 2),
                    "low": round(min(o, cl) - rng.uniform(0, 1), 2), "close": round(cl, 2)})
        px = cl
    return out


def make(kind, warm, live_ds):
    if kind == "V2":
        e = TMA2LiveSignalEngine(max_extension_pct=0.0, slope_gate=False)
    else:
        e = TMALiveSignalEngine()
    assert e.seed_warmup(warm)
    e.start_day(live_ds, sess_start_min=9 * 60 + 15, sess_end_min=15 * 60)
    return e


def run(kind, warm, live_ds, candles):
    e = make(kind, warm, live_ds)
    got = []
    for c in candles:
        for s in e.on_spot_candle(c):
            got.append((int(s["ts"]), s["side"], s["cond"], bool(s["stale"]), bool(s.get("gap_close")),
                        int(c["ts"])))
    return e, got


for kind, nwarm in (("V2", 5), ("V1", 3)):
    print(f"── TMA_{kind}")
    rng = random.Random(11 if kind == "V2" else 7)
    tested = gapped_sig = 0
    moved = [0]
    for trial in range(40):
        base = rng.uniform(24000, 26000)
        warm = []
        for d in range(nwarm):
            ds = DAY + d * 86400
            warm.append((session(ds, rng, base), ds))
            base = warm[-1][0][-1]["close"]
        live_ds = DAY + nwarm * 86400
        live = session(live_ds, rng, base)
        e, full = run(kind, warm, live_ds, live)
        fresh = [g for g in full if not g[3]]
        if not fresh:
            continue
        sig_ts = fresh[0][0]                         # bar completion = bar start + 300
        drop = sig_ts - 60                           # that bar's LAST minute
        gapped = [c for c in live if c["ts"] != drop]
        e2, got = run(kind, warm, live_ds, gapped)
        tested += 1
        if len(e2._bars5) != len(e._bars5):
            check(f"trial {trial}: no 5m bar dropped from the EMA stream", False, (len(e2._bars5), len(e._bars5)))
        ref_keys = {(int(s["ts"]), s["side"], s["cond"]) for s in e2.reference_full_day()}
        first = next((g for g in got if g[0] == sig_ts), None)
        if first is None:
            # the bar's close moved (its last minute is missing) and the knife-edge stack
            # transition moved with it — allowed ONLY if the one-shot computation agrees
            if any(k[0] == sig_ts for k in ref_keys):
                check(f"trial {trial}: the gapped bar's signal is not lost", False, got[:3])
            moved[0] += 1
            continue
        gapped_sig += 1
        ok = (first[5] == sig_ts and not first[3] and first[4]
              and (first[1], first[2]) == (fresh[0][1], fresh[0][2]))
        if not ok:
            check(f"trial {trial}: emitted at the next minute, fresh, same side/cond", False, (first, fresh[0]))
        if e2.frozen or e2.diag.get("gap_closes") != 1:
            check(f"trial {trial}: not frozen, one gap close", False, (e2.frozen, e2.diag.get("gap_closes")))
        if not all(not g[3] for g in got):
            check(f"trial {trial}: no stale signals", False, got)
        ref = e2.reference_full_day()
        if sorted((int(s["ts"]), s["side"], s["cond"]) for s in ref) != sorted(g[:3] for g in got):
            check(f"trial {trial}: incremental == one-shot on the same gapped feed", False)
    check(f"TMA_{kind}: gapped-bar signal emitted one minute late ({gapped_sig}/{tested}; "
          f"{moved[0]} moved by the missing minute's price, as the one-shot run agrees — "
          f"the old engine lost all of them)",
          tested >= 5 and gapped_sig + moved[0] == tested and gapped_sig >= 0.85 * tested, (gapped_sig, tested))

    # complete data → no gap closes, nothing changes
    rng = random.Random(3)
    warm, base = [], 25000.0
    for d in range(nwarm):
        ds = DAY + d * 86400
        warm.append((session(ds, rng, base), ds))
        base = warm[-1][0][-1]["close"]
    live_ds = DAY + nwarm * 86400
    e, full = run(kind, warm, live_ds, session(live_ds, rng, base))
    check(f"TMA_{kind}: complete feed → no gap closes, no gap-flagged signal",
          not e.diag.get("gap_closes") and not any(g[4] for g in full))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL TMA GAP-CLOSE CHECKS PASSED")
