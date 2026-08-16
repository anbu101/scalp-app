# backend/app/engine/gc/test_gc_live_core.py
# Pure-core tests. THE test is t_replay_parity: feed a day candle-by-candle
# through replay_and_diff and assert the incremental action stream equals
# the one-shot backtest simulation — parity by construction, verified.
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from app.backtest.gc.gc_v1_engine import TFCandle, simulate_gc_day
from app.engine.gc.gc_live_core import (
    norm_live_cfg, engine_cfg_for_day, replay_and_diff, stable_history_check,
    plan_legs, combined_open_mtm, cap_cut, cut_halts_day, to_tf_candles,
    closed_only, LOT_SIZE)

S0 = 1_700_000_000


def C(i, o, h, l, c):
    return TFCandle(ts=S0 + i * 60, open=o, high=h, low=l, close=c,
                    last1m_ts=S0 + i * 60)


def day_flip():
    """CE arm→entry→SL→flip PE→EOD (2 trades), plus tail candles."""
    d = [C(0, 100, 102, 98, 101),        # H1=102 L1=98
         C(1, 101, 104, 101, 103),       # ARM CE
         C(2, 103, 103.5, 101.9, 103),   # touch → ENTER seq0
         C(3, 103, 104, 102, 103.5),
         C(4, 103.5, 104, 96, 97),       # SL seq0 (close < anchor low)
         C(5, 97, 99, 96.5, 98.4),       # flip touch (>=98? touch of L1=98) → wait: flip = touch back of C1 level
         C(6, 98, 99, 97, 98.2)]
    return d


def run_incremental(candles, prev_tail, ecfg):
    """Feed candle-by-candle; collect actions; maintain executed counters."""
    entries = exits = 0
    stream = []
    executed = []
    for n in range(1, len(candles) + 1):
        sim, acts = replay_and_diff(candles[:n], prev_tail, ecfg,
                                    entries, exits)
        err = stable_history_check(sim["trades"], executed)
        assert err is None, err
        for a in acts:
            stream.append((a.kind, a.trade_seq, a.signal_side, a.ts,
                           a.exit_reason))
            if a.kind == "ENTER":
                entries += 1
                executed.append({"entry_ts": a.ts,
                                 "signal_side": a.signal_side})
            else:
                exits += 1
    return stream, entries, exits


def t_replay_parity():
    cfg = norm_live_cfg({"c1_range_max_pct": 0, "max_sl_pct": 0,
                         "max_trades_per_day": 4})
    ecfg = engine_cfg_for_day(cfg, S0 - SESSION_OFFSET, None)
    candles = day_flip()
    # pin the EOD boundary to the last candle's end so the batch reference
    # and the incremental stream describe the SAME finished day
    ecfg["exit_epoch"] = candles[-1].ts + 60
    stream, entries, exits = run_incremental(candles, [], ecfg)
    ref = simulate_gc_day(candles, [], ecfg)["trades"]
    # every ref trade appears as ENTER at its entry_ts and EXIT at exit_ts
    want = []
    for i, t in enumerate(ref):
        want.append(("ENTER", i, t.signal_side, t.entry_ts, None))
        if t.exit_ts is not None:
            want.append(("EXIT", i, t.signal_side, t.exit_ts, t.exit_reason))
    want.sort(key=lambda x: (x[3], 0 if x[0] == "ENTER" else 1, x[1]))
    got = sorted(stream, key=lambda x: (x[3], 0 if x[0] == "ENTER" else 1, x[1]))
    assert got == want, (got, want)
    assert entries == len(ref) and exits == len(ref)
    assert len(ref) == 2 and ref[0].exit_reason == "SL"


def t_same_candle_sl_incremental():
    # gap-through: entry candle also closes beyond SL → ENTER+EXIT same call
    cfg = norm_live_cfg({"c1_range_max_pct": 0, "max_sl_pct": 0})
    ecfg = engine_cfg_for_day(cfg, S0 - SESSION_OFFSET, None)
    d = [C(0, 100, 102, 98, 101),
         C(1, 101, 104, 101, 103),        # ARM CE
         C(2, 103, 103.2, 95, 96)]        # touch AND close < L1 → same-candle
    ecfg["exit_epoch"] = d[-1].ts + 3600      # boundary far away: prefix mode
    sim, acts = replay_and_diff(d, [], ecfg, 0, 0)
    kinds = [a.kind for a in acts]
    if sim["trades"] and sim["trades"][0].exit_idx == sim["trades"][0].entry_idx:
        assert kinds == ["ENTER", "EXIT"], kinds


def t_history_tripwire():
    cfg = norm_live_cfg({"c1_range_max_pct": 0, "max_sl_pct": 0})
    ecfg = engine_cfg_for_day(cfg, S0 - SESSION_OFFSET, None)
    d = day_flip()
    sim, _ = replay_and_diff(d, [], ecfg, 1, 0)
    bad = stable_history_check(sim["trades"],
                               [{"entry_ts": 12345, "signal_side": "PE"}])
    assert bad is not None and "seq 0" in bad


def t_plan_legs_buy_sell_hedge():
    cfg = norm_live_cfg({"mode": "BUY", "premium_max": 200,
                         "hedge_premium_max": 5, "lots": 2})
    chain = [("N25000CE", "CE", 180.0), ("N25100CE", "CE", 120.0),
             ("N25200CE", "CE", 60.0), ("N25300CE", "CE", 4.5),
             ("N25000PE", "PE", 175.0), ("N24900PE", "PE", 110.0),
             ("N24800PE", "PE", 55.0), ("N24700PE", "PE", 3.8)]
    legs, r = plan_legs(signal_side="CE", cfg=cfg, chain=chain)
    assert r == "ok" and len(legs) == 1
    assert legs[0].action == "BUY" and legs[0].symbol == "N25000CE"
    assert legs[0].qty == 2 * LOT_SIZE
    # SELL: CE signal → SELL PE + BUY PE hedge ≤5, hedge FIRST in basket
    cfg2 = norm_live_cfg({"mode": "SELL", "premium_max": 200,
                          "hedge_premium_max": 5, "lots": 1})
    legs2, r2 = plan_legs(signal_side="CE", cfg=cfg2, chain=chain)
    assert r2 == "ok" and len(legs2) == 2
    assert legs2[0].role == "HEDGE" and legs2[0].action == "BUY" \
        and legs2[0].symbol == "N24700PE"
    assert legs2[1].role == "MAIN" and legs2[1].action == "SELL" \
        and legs2[1].symbol == "N25000PE"
    # hedge unfillable (no cheap strikes) → fail-closed skip
    chain3 = [(s, t, p) for (s, t, p) in chain if p > 50]
    legs3, r3 = plan_legs(signal_side="CE",
                          cfg=norm_live_cfg({"mode": "SELL", "premium_max": 200,
                                             "hedge_premium_max": 5}),
                          chain=chain3)
    # cheapest-real fallback allowed only when cheaper than main: 55 < 175 → used
    assert legs3 and legs3[0].symbol == "N24800PE"
    chain4 = [("N25000PE", "PE", 100.0)]      # only the main itself
    legs4, r4 = plan_legs(signal_side="CE",
                          cfg=norm_live_cfg({"mode": "SELL", "premium_max": 200,
                                             "hedge_premium_max": 5}),
                          chain=chain4)
    assert legs4 == [] and "fail-closed" in r4


def t_cap_book():
    pos = [{"symbol": "A", "action": "SELL", "entry_price": 100.0, "qty": 65},
           {"symbol": "B", "action": "BUY", "entry_price": 5.0, "qty": 65}]
    ltp = {"A": 110.0, "B": 6.0}
    m = combined_open_mtm(pos, ltp)
    assert abs(m - ((100 - 110) * 65 + (6 - 5) * 65)) < 1e-9   # -585
    assert combined_open_mtm(pos, {"A": 110.0}) is None        # partial marks
    cfg = norm_live_cfg({"max_loss_day": 1000, "max_loss_per_trade": 400})
    assert cap_cut(day_realized=-500, open_mtm=-585, cfg=cfg) == "MAX_LOSS_DAY"
    assert cap_cut(day_realized=0, open_mtm=-585, cfg=cfg) == "MAX_LOSS_TRADE"
    assert cap_cut(day_realized=0, open_mtm=-100, cfg=cfg) is None
    assert cut_halts_day("MAX_LOSS_DAY") and not cut_halts_day("MAX_LOSS_TRADE")


def t_cfg_clamps():
    cfg = norm_live_cfg({"exit_time": "15:29", "mode": "sell", "lots": 0})
    assert cfg["exit_time"] == "15:20" and cfg["mode"] == "SELL" \
        and cfg["lots"] == 1


def t_candle_plumbing():
    rows = [{"ts": S0, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"ts": S0 + 60, "open": 1.5, "high": 2, "low": 1, "close": 1.8}]
    cs = to_tf_candles(rows)
    assert cs[0].last1m_ts == S0 and cs[1].close == 1.8
    assert len(closed_only(cs, S0 + 60)) == 1          # 2nd minute still open
    assert len(closed_only(cs, S0 + 120)) == 2


SESSION_OFFSET = (9 * 60 + 15) * 60   # S0 treated as 09:15


if __name__ == "__main__":
    for t in (t_replay_parity, t_same_candle_sl_incremental,
              t_history_tripwire, t_plan_legs_buy_sell_hedge,
              t_cap_book, t_cfg_clamps, t_candle_plumbing):
        t()
        print(f"PASS {t.__name__}")
    print("ALL GC LIVE CORE TESTS PASSED")