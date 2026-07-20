# backend/app/engine/tma/test_tma_live_core.py
#
# ── TMA LIVE CORE PARITY PROOF ──
# step_candle()/hard_close()/mtm_cut_after_data_gap() driven candle-by-candle
# must produce EXACTLY the exit monitor_position_day() produces over the same
# day (ts, price, reason, ambiguity) — for random prices, random SL/TP
# (including disabled), random xover times, gaps through levels, MTM cuts,
# and hard-close days. Run:  python3 -m pytest test_tma_live_core.py -q
# (also runnable standalone: python3 test_tma_live_core.py)

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest" / "tma"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest" / "pst"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tma_v1_engine import monitor_position_day          # noqa: E402
from tma_live_core import (new_position, step_candle,   # noqa: E402
                           mtm_cut_after_data_gap, hard_close, sltp_levels)

DAY = 1_700_000_000 - (1_700_000_000 % 86400)


def _random_day(rng, entry_ts, n=120, base=100.0):
    out, px = [], base
    for i in range(n):
        drift = rng.uniform(-4, 4)
        o = px
        cl = max(0.05, px + drift)
        hi = max(o, cl) + rng.uniform(0, 3)
        lo = max(0.05, min(o, cl) - rng.uniform(0, 3))
        # occasional violent gap (tests gap-through-level fills)
        if rng.random() < 0.05:
            o = max(0.05, o + rng.uniform(-25, 25))
            hi, lo = max(hi, o), min(lo, o)
        out.append({"ts": entry_ts + i * 60, "open": round(o, 2),
                    "high": round(hi, 2), "low": round(lo, 2),
                    "close": round(cl, 2)})
        px = cl
    return out


def _live_exit(pos_kwargs, candles, xover_ts, hard_close_ts,
               hard_reason, mtm_cut_ts):
    pos = new_position(**pos_kwargs, mtm_cut_ts=mtm_cut_ts)
    for c in sorted(candles, key=lambda x: x["ts"]):
        if hard_close_ts is not None and int(c["ts"]) >= hard_close_ts:
            break
        r = step_candle(pos, c, xover_ts)
        if r is not None:
            return r
    if hard_close_ts is None:
        return mtm_cut_after_data_gap(pos)      # may be None (survives)
    return hard_close(pos, hard_reason)


def _engine_exit(pos_kwargs, candles, xover_ts, hard_close_ts,
                 hard_reason, mtm_cut_ts):
    pos = {"side": "PE", "action": "SELL",
           "entry_ts": pos_kwargs["entry_ts"],
           "entry_price": pos_kwargs["entry_price"],
           "sl_price": pos_kwargs["sl_price"],
           "tp_price": pos_kwargs["tp_price"],
           "watch_from": pos_kwargs["entry_ts"] + 60,
           "last_close": pos_kwargs["entry_price"],
           "last_ts": pos_kwargs["entry_ts"]}
    res = monitor_position_day(pos, candles, xover_ts, hard_close_ts,
                               hard_reason, mtm_cut_ts=mtm_cut_ts)
    if res is None:
        return None
    return {"exit_ts": res["exit_ts"], "exit_price": res["exit_price"],
            "exit_reason": res["exit_reason"],
            "ambiguous": res["ambiguous_fill"]}


def test_randomized_parity():
    rng = random.Random(20260719)
    mismatches = 0
    for trial in range(400):
        entry_ts = DAY + (9 * 60 + 20) * 60 + rng.randrange(0, 60) * 60
        entry = round(rng.uniform(20, 150), 2)
        sl = round(entry * rng.uniform(1.05, 1.6), 2) if rng.random() < 0.8 else None
        tp = round(max(0.05, entry * rng.uniform(0.3, 0.95)), 2) if rng.random() < 0.8 else None
        candles = _random_day(rng, entry_ts, n=rng.randrange(30, 150), base=entry)
        xover_ts = entry_ts + rng.randrange(5, 100) * 60 if rng.random() < 0.5 else None
        positional = rng.random() < 0.5
        if positional:
            hard_ts, hard_reason = None, "EOD"
            mtm_cut_ts = (entry_ts + rng.randrange(10, 140) * 60
                          if rng.random() < 0.5 else None)
        else:
            hard_ts = entry_ts + rng.randrange(10, 160) * 60
            hard_reason, mtm_cut_ts = "EOD", None
        kw = dict(entry_ts=entry_ts, entry_price=entry,
                  sl_price=sl, tp_price=tp)
        a = _live_exit(kw, candles, xover_ts, hard_ts, hard_reason, mtm_cut_ts)
        b = _engine_exit(kw, candles, xover_ts, hard_ts, hard_reason, mtm_cut_ts)
        if a != b:
            mismatches += 1
            print(f"TRIAL {trial} MISMATCH:\n  live  ={a}\n  engine={b}\n"
                  f"  kw={kw} xover={xover_ts} hard={hard_ts} mtm={mtm_cut_ts}")
    assert mismatches == 0, f"{mismatches} parity mismatches"


def test_sltp_units_parity_with_runner_math():
    # Byte-identical to the runner's SLTP_UNITS block for every unit combo.
    ep = 80.0
    assert sltp_levels(ep, 30, 50, "PCT", "PCT") == (ep * 1.3, ep * 0.5)
    assert sltp_levels(ep, 25, 60, "PTS", "PTS") == (105.0, 20.0)
    assert sltp_levels(ep, 200, 10, "ABS", "ABS") == (200.0, 10.0)
    assert sltp_levels(ep, 50, 90, "ABS", "ABS") == (None, None)   # wrong side → off
    assert sltp_levels(ep, 0, 0) == (None, None)                   # disabled
    assert sltp_levels(ep, 0, 100, "PCT", "PTS") == (None, 0.05)   # TP floor


if __name__ == "__main__":
    test_randomized_parity()
    test_sltp_units_parity_with_runner_math()
    print("OK — tma_live_core parity proven (400 randomized trials)")
