# backend/app/engine/tma/test_tma_signal_engine.py
#
# ── TMA LIVE SIGNAL ENGINE — incremental == full-day proof ──
# Synthetic 1m spot across 3 warmup sessions + 1 live day; the engine fed
# candle-by-candle must emit EXACTLY the signals build_signals produces over
# the complete day in one shot (reference_full_day), never freeze, and flag
# backfilled signals stale. Run: python3 test_tma_signal_engine.py

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest" / "tma"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest" / "pst"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tma_live_signal_engine import TMALiveSignalEngine   # noqa: E402

DAY = 1_700_000_000 - (1_700_000_000 % 86400)
IST = 5 * 3600 + 30 * 60


def _session(day_start, rng, base):
    """One trading session of 1m candles 09:15–15:29, trending noise so
    EMA crosses actually happen."""
    out, px = [], base
    t0 = day_start + (9 * 60 + 15) * 60
    trend = rng.choice([-1, 1]) * rng.uniform(0.05, 0.4)
    for i in range(375):
        if rng.random() < 0.01:
            trend = rng.choice([-1, 1]) * rng.uniform(0.05, 0.5)
        drift = trend + rng.uniform(-2.5, 2.5) + 3 * math.sin(i / 40)
        o = px
        cl = px + drift
        out.append({"ts": t0 + i * 60, "open": round(o, 2),
                    "high": round(max(o, cl) + rng.uniform(0, 1), 2),
                    "low": round(min(o, cl) - rng.uniform(0, 1), 2),
                    "close": round(cl, 2)})
        px = cl
    return out


def test_incremental_equals_full_day():
    rng = random.Random(7)
    total_sigs = 0
    for trial in range(20):
        base = rng.uniform(24000, 26000)
        warm = []
        for d in range(3):
            ds = DAY + d * 86400
            warm.append((_session(ds, rng, base), ds))
            base = warm[-1][0][-1]["close"]
        live_ds = DAY + 3 * 86400
        live = _session(live_ds, rng, base)

        eng = TMALiveSignalEngine()
        assert eng.seed_warmup(warm)
        eng.start_day(live_ds, sess_start_min=9 * 60 + 15,
                      sess_end_min=15 * 60)
        emitted = []
        for c in live:
            emitted.extend(eng.on_spot_candle(c))
        assert not eng.frozen, f"trial {trial}: engine froze " \
                               f"({eng.diag['freeze_reason']})"
        ref = eng.reference_full_day()
        got = sorted((int(s["ts"]), s["side"], s["cond"]) for s in emitted)
        want = sorted((int(s["ts"]), s["side"], s["cond"]) for s in ref)
        assert got == want, f"trial {trial}: incremental != full-day\n" \
                            f"  got ={got}\n  want={want}"
        assert all(not s["stale"] for s in emitted), \
            f"trial {trial}: live-fed signals must never be stale"
        total_sigs += len(emitted)
    assert total_sigs > 0, "no signals across 20 trials — data gen too flat"
    print(f"OK — incremental == full-day across 20 trials "
          f"({total_sigs} signals emitted)")


def test_backfill_marks_stale():
    rng = random.Random(11)
    base = 25000.0
    warm = []
    for d in range(3):
        ds = DAY + d * 86400
        warm.append((_session(ds, rng, base), ds))
        base = warm[-1][0][-1]["close"]
    live_ds = DAY + 3 * 86400
    live = _session(live_ds, rng, base)

    eng = TMALiveSignalEngine()
    eng.seed_warmup(warm)
    eng.start_day(live_ds, sess_start_min=9 * 60 + 15, sess_end_min=15 * 60)
    # simulate a mid-session restart backfill: feed the first 240 minutes
    # then check that any signal older than the final fed candle is stale.
    backfill = live[:240]
    emitted = []
    for c in backfill:
        emitted.extend(eng.on_spot_candle(c))
    old = [s for s in emitted if int(s["ts"]) < int(backfill[-1]["ts"])]
    fresh = [s for s in emitted if int(s["ts"]) >= int(backfill[-1]["ts"])]
    assert all(s["stale"] for s in old), "backfilled old signals must be stale"
    # signals stamped at the last fed boundary are fresh by definition
    assert all(not s["stale"] for s in fresh
               if int(s["ts"]) == int(backfill[-1]["ts"]) + 60)
    print(f"OK — backfill staleness ({len(old)} stale, {len(fresh)} boundary)")


if __name__ == "__main__":
    test_incremental_equals_full_day()
    test_backfill_marks_stale()
