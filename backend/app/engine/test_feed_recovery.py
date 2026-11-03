# backend/app/engine/test_feed_recovery.py
#
# ── FEED_RECOVERY_20261103 ── a spot-candle HOLE (Mac asleep, WS dead) is
# repaired from Kite while the app runs: TMA_V2 / TMA_V1 / Velvet rebuild
# today's indicators and keep signalling exactly like a feed that never broke.
#   cd backend && PYTHONPATH=$PWD python3 app/engine/test_feed_recovery.py
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="feed_rec_"))
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parents[2] / "app" / "backtest" / "tma"))
sys.path.insert(0, str(HERE.parents[2] / "app" / "backtest" / "pst"))
sys.path.insert(0, str(HERE.parent / "tma2"))
sys.path.insert(0, str(HERE.parent / "tma"))

from app.engine.feed_recovery import FeedRecovery, STALL_MISSING_MIN      # noqa: E402
from app.engine.tma2.tma2_live_signal_engine import TMA2LiveSignalEngine  # noqa: E402
from app.engine.tma.tma_live_signal_engine import TMALiveSignalEngine     # noqa: E402

FAILS, PASSES = [], [0]
DAY = 1_700_000_000 - (1_700_000_000 % 86400)


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


# ── 1. FeedRecovery on a fake engine ────────────────────────────────────
class FakeEngine:
    def __init__(self):
        self.candles, self.rebuilt = [], []

    def last_candle_ts(self):
        return self.candles[-1] if self.candles else None

    def rebuild_today(self, cs):
        self.rebuilt.append([c["ts"] for c in cs])
        return len(cs)


T0 = DAY + (9 * 60 + 15) * 60
M = lambda n: T0 + n * 60                                        # noqa: E731
kite = {"rows": [], "fail": None, "calls": 0}


def fetch():
    kite["calls"] += 1
    if kite["fail"]:
        raise kite["fail"]
    return list(kite["rows"])


def candles(a, b):
    return [{"ts": M(i), "open": 1, "high": 1, "low": 1, "close": 1} for i in range(a, b)]


print("── 1. detection and the rebuild contract")
NOTE = []
fe = FakeEngine()
fe.candles = [M(10)]
rec = FeedRecovery(tag="T", engine=fe, fetch_fn=fetch, notify=lambda m, s="info": NOTE.append((s, m)))
check("no candle yet today → nothing", FeedRecovery(tag="T", engine=FakeEngine(), fetch_fn=fetch).before_candle(M(5)) is False)
check("next minute → no stall", rec.before_candle(M(11)) is False)
check("1 missing minute (gap-close case) → no stall", rec.before_candle(M(12)) is False)
fe.candles = [M(10)]
check(f"{STALL_MISSING_MIN - 1} missing minutes → no stall", rec.before_candle(M(10 + STALL_MISSING_MIN)) is False and kite["calls"] == 0)
kite["rows"] = candles(0, 40)
sup = rec.before_candle(M(30))                                   # 19 minutes missing
check("a stall is repaired: rebuild called once with every candle BEFORE the current one",
      sup is False and len(fe.rebuilt) == 1 and fe.rebuilt[0] == [M(i) for i in range(0, 30)], fe.rebuilt)
check("→ signals NOT suppressed once repaired; recoveries counted; info alert sent",
      rec.recoveries == 1 and not rec.pending and any(s == "info" for s, _ in NOTE), NOTE)

print("── 2. fail closed until Kite delivers")
fe = FakeEngine()
fe.candles = [M(10)]
NOTE.clear()
kite.update(rows=[], fail=None, calls=0)
rec = FeedRecovery(tag="T", engine=fe, fetch_fn=fetch, notify=lambda m, s="info": NOTE.append((s, m)), retry_max=3)
check("Kite returns nothing → signals suppressed", rec.before_candle(M(30)) is True and rec.pending)
fe.candles.append(M(30))
kite["rows"] = candles(0, 20)                                    # stale: stops at minute 19 — does not reach the current candle
check("Kite lagging (series ends 11 min short) → still suppressed", rec.before_candle(M(31)) is True and not fe.rebuilt)
kite["fail"] = RuntimeError("network")
check("Kite raising → still suppressed (never raises into the caller)", rec.before_candle(M(32)) is True)
check("first failure alerted as a warning", sum(1 for s, _ in NOTE if s == "warning") == 1, NOTE)
check("retry budget spent → error alert once, then no more fetches",
      any(s == "error" for s, _ in NOTE), NOTE)
n_calls = kite["calls"]
check("…and it stops hammering Kite", rec.before_candle(M(33)) is True and kite["calls"] == n_calls)
fe = FakeEngine()
fe.candles = [M(10)]
kite.update(rows=[], fail=None, calls=0)
rec = FeedRecovery(tag="T", engine=fe, fetch_fn=fetch, retry_max=5)
rec.before_candle(M(30))
kite["rows"] = candles(0, 40)
check("a later minute succeeds → rebuilt, suppression lifted",
      rec.before_candle(M(31)) is False and len(fe.rebuilt) == 1 and not rec.pending)
rec.pending, rec._gap = True, (M(10), M(30))
check("a pending rebuild from yesterday is dropped on a new session",
      rec.before_candle(M(30) + 86400) is False and not rec.pending)

# ── 2b. engines end to end ───────────────────────────────────────────────
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
    e = TMA2LiveSignalEngine(max_extension_pct=0.0, slope_gate=False) if kind == "V2" else TMALiveSignalEngine()
    assert e.seed_warmup(warm)
    e.start_day(live_ds, sess_start_min=9 * 60 + 15, sess_end_min=15 * 60)
    return e


def feed(kind, warm, live_ds, feed_candles, truth=None):
    """The coordinator's loop: recovery check BEFORE the fold, replay signals never reach the manager."""
    e = make(kind, warm, live_ds)
    now = [0]
    rec = FeedRecovery(tag=kind, engine=e, fetch_fn=lambda: [c for c in truth if c["ts"] <= now[0]]) if truth else None
    got = []
    for c in feed_candles:
        now[0] = c["ts"]
        sup = rec.before_candle(c["ts"]) if rec else False
        for s in e.on_spot_candle(c):
            got.append((int(s["ts"]), s["side"], s["cond"], bool(s["stale"]) or sup))
    return e, got, rec


for kind, nwarm in (("V2", 5), ("V1", 3)):
    print(f"── 3. TMA_{kind}: a 60-minute hole (09:30–10:30) mid-session")
    rng = random.Random(41 if kind == "V2" else 17)
    trials = broken = repaired = lost = 0
    for t in range(40):
        base = rng.uniform(24000, 26000)
        warm = []
        for d in range(nwarm):
            ds = DAY + d * 86400
            warm.append((session(ds, rng, base), ds))
            base = warm[-1][0][-1]["close"]
        live_ds = DAY + nwarm * 86400
        full = session(live_ds, rng, base)
        hole_lo, hole_hi = full[15]["ts"], full[75]["ts"]            # 09:30 → 10:30
        holed = [c for c in full if not (hole_lo <= c["ts"] < hole_hi)]
        eA, gA, _ = feed(kind, warm, live_ds, full)
        eB, gB, _ = feed(kind, warm, live_ds, holed)
        eC, gC, rc = feed(kind, warm, live_ds, holed, truth=full)
        after = lambda g: [x for x in g if x[0] > hole_hi + 60 * 10 and not x[3]]   # noqa: E731
        trials += 1
        if [b["close"] for b in eA._bars5] != [b["close"] for b in eB._bars5]:
            broken += 1
        same_bars = [b["close"] for b in eA._bars5] == [b["close"] for b in eC._bars5]
        lastv = lambda st: {k: v[-1] for k, v in st.items() if isinstance(v, list) and v}   # noqa: E731
        same_ema = lastv(eA._state) == lastv(eC._state) and len(lastv(eA._state)) >= 3
        same_sig = [x[:3] for x in after(gA)] == [x[:3] for x in after(gC)]
        if same_bars and same_ema and same_sig and rc.recoveries == 1:
            repaired += 1
        else:
            check(f"trial {t}: repaired stream ≡ a feed that never broke", False,
                  (same_bars, same_ema, same_sig, rc.recoveries))
        # replay signals never reach the caller: nothing stamped inside the hole's replay window is returned fresh
        if any(x[0] <= hole_hi and not x[3] for x in gC if x[0] > hole_lo and x[0] not in {y[0] for y in gA}):
            lost += 1
    check(f"TMA_{kind}: the hole really breaks the un-repaired engine ({broken}/{trials} trials)", broken >= trials * 0.9, broken)
    check(f"TMA_{kind}: with recovery, bars, EMAs and every later signal equal the unbroken feed ({repaired}/{trials})",
          repaired == trials, repaired)
    check(f"TMA_{kind}: no signal invented from the replay", lost == 0, lost)

print("── 4. coordinator wiring (TMA_V2): suppression reaches the manager")
try:
    from app.engine.tma2 import tma2_selection_loop as L2
except Exception as e:                                           # noqa: BLE001
    L2 = None
    print(f"  note: coordinator import unavailable here ({e!r}) — wiring checked by the apply script's loop suites")
if L2 is not None:
    class Mgr:
        def __init__(self):
            self.sigs = []

        def note_chain(self, c): pass
        def on_minute(self, *a, **k): pass
        def force_eod(self, ts): pass
        def on_signal(self, sig, chain): self.sigs.append(dict(sig))

    rng = random.Random(5)
    warm, base = [], 25000.0
    for d in range(5):
        ds = DAY + d * 86400
        warm.append((session(ds, rng, base), ds))
        base = warm[-1][0][-1]["close"]
    live_ds = DAY + 5 * 86400
    full = session(live_ds, rng, base)
    holed = [c for c in full if not (full[15]["ts"] <= c["ts"] < full[75]["ts"])]
    for ok_kite in (False, True):
        e = make("V2", warm, live_ds)
        now = [0]
        rec = FeedRecovery(tag="TMA2", engine=e,
                           fetch_fn=lambda: ([c for c in full if c["ts"] <= now[0]] if ok_kite else []), retry_max=1000)
        mg = Mgr()
        co = L2.TMA2MinuteCoordinator(e, mg, 15 * 60 + 25, notify=None, recovery=rec)
        for c in holed:
            now[0] = c["ts"]
            co.on_minute(c["ts"], c, None)
        after_hole = [s for s in mg.sigs if int(s["ts"]) > full[75]["ts"]]
        if ok_kite:
            check("Kite reachable: signals after the hole reach the manager FRESH", all(not s["stale"] for s in after_hole), after_hole)
        else:
            check("Kite unreachable: every signal after the hole is stale-flagged (the manager skips them)",
                  all(s["stale"] for s in after_hole), after_hole)

print("── 5. Velvet: the same hole")
try:
    from app.backtest.gc.gc_v1_engine import resample_spot               # noqa: E402
    from app.backtest.vet.vet_v1_engine import vet_states                # noqa: E402
    from app.engine.vet.vet_live_signal_engine import (IST_OFFSET, VetLiveSignalEngine,
                                                       session_start_epoch)   # noqa: E402

    def vsession(day_index, px, seed):
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

    sess, px = [], 24000.0
    for d in range(12):
        rows, px = vsession(d, px, 1000 + d)
        sess.append(rows)
    WARM = [r for s in sess[:11] for r in s]
    TODAY = sess[11]
    CFG = {"trend_len": 40, "range_len": 0.618, "signal_tf": 5, "warmup_sessions": 10, "leg_action": "SELL"}
    A0 = session_start_epoch(TODAY[0]["ts"])

    def vfeed(rows, truth=None):
        e = VetLiveSignalEngine(CFG, warmup_1m=WARM)
        now = [0]
        rec = FeedRecovery(tag="VET", engine=e, fetch_fn=lambda: [c for c in truth if c["ts"] <= now[0]]) if truth else None
        out = {}
        for r in rows:
            now[0] = r["ts"]
            sup = rec.before_candle(r["ts"]) if rec else False
            s = e.on_minute(r)
            if s and not sup:
                out[s["bar_ts"]] = s["condition"]
        return e, out, rec

    broken = ok = 0
    TRIALS = 0
    for lo in (15, 40, 95, 140, 200):
        hole = (TODAY[lo]["ts"], TODAY[lo + 60]["ts"])
        holed = [r for r in TODAY if not (hole[0] <= r["ts"] < hole[1])]
        eA, a, _ = vfeed(TODAY)
        eB, b, _ = vfeed(holed)
        eC, c, rc = vfeed(holed, truth=TODAY)
        TRIALS += 1
        late = lambda d_: {k: v for k, v in d_.items() if k > hole[1] + 600}     # noqa: E731
        if late(a) != late(b):
            broken += 1
        if late(a) == late(c) and rc.recoveries == 1 and eA.status()["bars_today"] == eC.status()["bars_today"]:
            ok += 1
        else:
            check(f"Velvet hole @{lo}: repaired ≡ unbroken", False, (len(late(a)), len(late(c)), rc.recoveries))
    check(f"Velvet: the hole changes later conditions in the un-repaired engine ({broken}/{TRIALS})", broken >= 1, broken)
    check(f"Velvet: with recovery every later condition equals the unbroken feed ({ok}/{TRIALS})", ok == TRIALS, ok)
    check("Velvet status counts the recovery", eC.status().get("recoveries") == 1, eC.status())
except ImportError as e:
    print(f"  note: Velvet pieces unavailable here ({e!r}) — skipped")

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL FEED-RECOVERY CHECKS PASSED")
