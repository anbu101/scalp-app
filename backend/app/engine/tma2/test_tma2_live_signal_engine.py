# backend/app/engine/tma2/test_tma2_live_signal_engine.py
#
# ── TMA_V2 LIVE SIGNAL PARITY PROOF ──
# The engine emits signals INCREMENTALLY (one completed 1m spot candle at a
# time). This asserts the incrementally emitted stream equals what a
# BACKTEST of the same day produces one-shot (reference_full_day) — the
# replay-diff contract. Also proves:
#   * the frozen study parameters actually reach build_signals_v2 (an
#     extension cap of 0 vs 0.8 must change the emitted set on a shaped day),
#   * the prefix-stability guard freezes rather than trading unstable signals,
#   * xover_ts_for uses the configured exit reference (EMA55 by default) and
#     returns the SAME answer as the backtest's own xover_exit_ts_v2,
#   * malformed / out-of-order / duplicate candles are rejected, not traded.
# Run standalone:  python3 test_tma2_live_signal_engine.py
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest" / "tma"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest" / "pst"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tma_v2_engine import (compute_state_v2, warmup_bars,        # noqa: E402
                           xover_exit_ts_v2)
from pst_indicators import aggregate                             # noqa: E402
from tma2_live_signal_engine import TMA2LiveSignalEngine         # noqa: E402

IST = 5 * 3600 + 30 * 60
DAY = 1_764_000_000 - (1_764_000_000 % 86400)      # some UTC midnight
FAILS = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def session_candles(day_start, closes):
    """1m spot candles from 09:15 IST for the given close path."""
    t0 = day_start + (9 * 60 + 15) * 60
    return [{"ts": t0 + i * 60, "open": c, "high": c, "low": c, "close": c}
            for i, c in enumerate(closes)]


def flat_then_trend(n_flat, n_trend, base=25000.0, step=-2.0):
    return ([base] * n_flat
            + [base + step * i for i in range(1, n_trend + 1)])


def warmup_pack(days=5, base=25000.0):
    """FIVE prior sessions of DEAD-FLAT spot — enough bars for the EMA144
    seed (exactly why WARMUP_DAYS is 5 for V2), and flat so all four EMAs
    are EQUAL at the open. That matters: the stack test is STRICT, so
    equal EMAs mean neither stack is standing, and today's move produces a
    genuine False→True TRANSITION. (Warmup at differing levels would leave
    a stack already standing at the open, which correctly emits nothing —
    the rule, not a bug.)"""
    out = []
    for d in range(days, 0, -1):
        ds = DAY - d * 86400
        out.append((session_candles(ds, [base] * 375), ds))
    return out


def feed(engine, candles):
    got = []
    for c in candles:
        got.extend(engine.on_spot_candle(c))
    return got


def build(**kw):
    e = TMA2LiveSignalEngine(**kw)
    assert e.seed_warmup(warmup_pack())
    e.start_day(DAY, sess_start_min=9 * 60 + 15, sess_end_min=15 * 60)
    return e


# ── 1. incremental stream ≡ one-shot backtest ────────────────────────
def test_replay_parity():
    e = build(max_extension_pct=0.0, slope_gate=False)
    candles = session_candles(DAY, flat_then_trend(60, 200))
    emitted = feed(e, candles)
    ref = e.reference_full_day()
    key = lambda s: (int(s["ts"]), s["side"], s["cond"])
    check("incremental stream == one-shot backtest",
          sorted(map(key, emitted)) == sorted(map(key, ref)),
          f"live={sorted(map(key, emitted))[:3]} ref={sorted(map(key, ref))[:3]}")
    check("engine not frozen on a normal day", not e.frozen)
    check("signals were actually produced (fixture is meaningful)",
          len(emitted) > 0, f"n={len(emitted)}")
    check("every signal carries E1/E2 + side",
          all(s["cond"] in ("E1", "E2") and s["side"] in ("CE", "PE")
              for s in emitted))
    check("no signal marked stale on a from-open feed",
          all(not s["stale"] for s in emitted))


# ── 2. the frozen study parameters actually reach the pipeline ───────
def test_params_reach_pipeline():
    candles = session_candles(DAY, flat_then_trend(60, 200))
    loose = feed(build(max_extension_pct=0.0, slope_gate=False), candles)
    tight = build(max_extension_pct=0.0001, slope_gate=False)
    tight_sigs = feed(tight, candles)
    check("max_extension_pct reaches build_signals_v2",
          len(tight_sigs) < len(loose) and tight.diag["blocked_extension"] > 0,
          f"loose={len(loose)} tight={len(tight_sigs)} "
          f"blk={tight.diag['blocked_extension']}")
    sg = build(max_extension_pct=0.0, slope_gate=True)
    feed(sg, candles)
    check("slope gate reaches build_signals_v2 (funnel present)",
          "blocked_slope" in sg.diag and sg.diag["slope_gate"] == "ON")
    check("diag echoes the frozen params",
          sg.diag["xover_ref"] == 55 and sg.diag["max_extension_pct"] == 0.0)


# ── 3. xover service uses the configured reference ──────────────────
def test_xover_reference():
    candles = session_candles(DAY, flat_then_trend(30, 120) + [
        24760 + 4 * i for i in range(1, 110)])          # down then reversal
    e = build(max_extension_pct=0.0, slope_gate=False)
    sigs = feed(e, candles)
    check("fixture produced a PE (E1) signal",
          any(s["side"] == "PE" for s in sigs))
    if any(s["side"] == "PE" for s in sigs):
        pe = [s for s in sigs if s["side"] == "PE"][0]
        live = e.xover_ts_for("PE", int(pe["ts"]))
        # independent recomputation through the backtest's own function
        warm5 = warmup_bars(e._warmup_sessions, 5)
        today5 = [b for b in aggregate(e._candles, 5, DAY) if b["complete"]]
        bars5 = warm5 + today5
        st = compute_state_v2(bars5)
        ref55 = xover_exit_ts_v2(bars5, st, "PE", int(pe["ts"]), tf_s=300,
                                 exit_ref=55)
        ref89 = xover_exit_ts_v2(bars5, st, "PE", int(pe["ts"]), tf_s=300,
                                 exit_ref=89)
        check("xover_ts_for == backtest xover_exit_ts_v2 @ref55",
              live == ref55, f"live={live} ref55={ref55}")
        if ref55 is not None and ref89 is not None:
            check("ref55 is not later than ref89 (sanity)", ref55 <= ref89)
        e89 = build(xover_exit_ref=89, max_extension_pct=0.0, slope_gate=False)
        feed(e89, candles)
        check("configured ref=89 is honoured",
              e89.xover_ts_for("PE", int(pe["ts"])) == ref89)


# ── 4. fail-closed behaviours ───────────────────────────────────────
def test_fail_closed():
    e = TMA2LiveSignalEngine()
    check("no warmup → not ready, emits nothing",
          not e.ready and e.on_spot_candle(
              {"ts": DAY + 34500, "open": 1, "high": 1, "low": 1,
               "close": 1}) == [])
    e = build()
    base = session_candles(DAY, [25000.0] * 10)
    feed(e, base)
    n_rej = e.diag["rejected_candles"]
    e.on_spot_candle({"ts": base[-1]["ts"], "open": 1, "high": 1,
                      "low": 1, "close": 1})               # duplicate
    e.on_spot_candle({"ts": base[0]["ts"], "open": 1, "high": 1,
                      "low": 1, "close": 1})               # out of order
    e.on_spot_candle({"ts": DAY + 34530, "open": 1, "high": 1,
                      "low": 1, "close": 1})               # unaligned
    e.on_spot_candle({"ts": "junk"})                        # malformed
    check("duplicate/out-of-order/unaligned/malformed all rejected",
          e.diag["rejected_candles"] == n_rej + 4,
          f"{e.diag['rejected_candles']} vs {n_rej + 4}")

    # prefix-stability guard: corrupt a frozen signal, next candle must FREEZE
    e2 = build(max_extension_pct=0.0, slope_gate=False)
    cs = session_candles(DAY, flat_then_trend(60, 200))
    fed = 0
    for c in cs:
        got = e2.on_spot_candle(c)
        fed += 1
        if got:
            break
    check("guard fixture emitted a signal to corrupt", bool(e2._emitted))
    if e2._emitted:
        k = next(iter(e2._emitted))
        e2._emitted[k] = dict(e2._emitted[k], spot=float(e2._emitted[k]["spot"]) + 99)
        e2.on_spot_candle(cs[fed])
        check("prefix instability FREEZES the engine (fail closed)",
              e2.frozen and e2.diag["freeze_reason"])
        check("frozen engine emits nothing thereafter",
              e2.on_spot_candle(cs[fed + 1]) == [])


if __name__ == "__main__":
    test_replay_parity()
    test_params_reach_pipeline()
    test_xover_reference()
    test_fail_closed()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print("\nALL TMA_V2 LIVE SIGNAL ENGINE TESTS PASSED")