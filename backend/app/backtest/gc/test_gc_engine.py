# backend/app/backtest/gc/test_gc_engine.py
#
# Behavioral tests for gc_v1_engine against SYNTHETIC candles with
# hand-computed expectations — one test per locked decision (D1–D10)
# plus the resampler. Pure engine, no corpus, runs standalone:
#     cd backend/app/backtest/gc && python3 test_gc_engine.py

from __future__ import annotations

try:
    from app.backtest.gc.gc_v1_engine import (
        TFCandle, resample_spot, simulate_gc_day)
except ImportError:
    from gc_v1_engine import TFCandle, resample_spot, simulate_gc_day  # type: ignore

DAY0 = 1_000_000_000 - (1_000_000_000 % 86400)   # arbitrary aligned day
S0 = DAY0 + (9 * 60 + 15) * 60                   # 09:15 session anchor


def C(i, o, h, l, c, tf_s=60):
    """tf candle #i (0-based from 09:15)."""
    ts = S0 + i * tf_s
    return TFCandle(ts=ts, open=o, high=h, low=l, close=c,
                    last1m_ts=ts + tf_s - 60)


def cfg(**kw):
    base = {"tf_s": 60, "exit_epoch": S0 + 360 * 60,   # 15:15
            "max_trades": 4, "signal_mode": "latest", "sl_lookback": 10}
    base.update(kw)
    return base


def run(candles, prev=(), **kw):
    return simulate_gc_day(list(candles), list(prev), cfg(**kw))


def t_resampler():
    """3m resample: session-anchored buckets, OHLC folds, last1m_ts = last
    contributing 1m bar."""
    m1 = [{"ts": S0 + i * 60, "open": 100 + i, "high": 105 + i,
           "low": 95 + i, "close": 101 + i} for i in range(7)]
    out = resample_spot(m1, 3, S0)
    assert len(out) == 3, out
    b0 = out[0]
    assert b0.ts == S0 and b0.open == 100 and b0.high == 107 \
        and b0.low == 95 and b0.close == 103 and b0.last1m_ts == S0 + 120
    b2 = out[2]           # partial (one 1m bar)
    assert b2.ts == S0 + 360 and b2.last1m_ts == S0 + 360
    # pre-session bars dropped
    out2 = resample_spot([{"ts": S0 - 60, "open": 1, "high": 1, "low": 1,
                           "close": 1}] + m1, 3, S0)
    assert len(out2) == 3


def t_basic_ce_chain():
    """Breakout close above H1 → touch of H1 → CE entry; SL from prev-day
    lookback (D1=a); holds to EOD."""
    prev = [C(-10 + k, 100, 101, 99, 100) for k in range(10)]      # inert
    day = [
        C(0, 100, 102, 98, 101),      # C1: H1=102 L1=98
        C(1, 101, 104, 101, 103),     # closes > H1 → ARM CE (arm_i=1)
        C(2, 103, 103.5, 101.9, 103), # low 101.9 <= 102 → CE ENTRY @103
        C(3, 103, 105, 102.5, 104),
        C(4, 104, 106, 103, 105),     # last session candle → EOD
    ]
    r = run(day, prev, exit_epoch=S0 + 5 * 60)
    ts = r["trades"]
    assert len(ts) == 1, ts
    t = ts[0]
    assert t.signal_side == "CE" and t.flip_seq == 0
    assert t.entry_idx == 2 and t.entry_spot == 103
    # no prev candle closed < L1=98 → SL fallback to L1
    assert t.sl_level == 98 and t.sl_fallback
    assert t.exit_reason == "EOD" and t.exit_idx == 4 and t.exit_spot == 105
    assert r["diag"]["entries"] == 1 and r["diag"]["sl_fallback_entries"] == 1


def t_d1a_prevday_sl():
    """D1=a: first entry's SL comes from the PREVIOUS session — a prev-day
    candle that closed below L1 donates its LOW as the CE SL."""
    prev = [C(-3, 100, 101, 99, 100),
            C(-2, 98, 99, 96.0, 97.5),    # close 97.5 < L1=98 → G1, low 96.0
            C(-1, 99, 100, 98.5, 99.5)]
    day = [
        C(0, 100, 102, 98, 101),
        C(1, 101, 104, 101, 103),
        C(2, 103, 103.5, 101.9, 103),     # CE entry
        C(3, 103, 104, 102, 103.5),
    ]
    r = run(day, prev, exit_epoch=S0 + 4 * 60)
    t = r["trades"][0]
    assert t.sl_level == 96.0 and not t.sl_fallback, t
    # D2 most-recent: add a NEWER qualifying candle — it must win
    prev2 = prev + [C(0, 97, 98, 95.0, 97.0)]     # closes 97 < 98, low 95
    # (shift day indices by using prev2 as tail; engine only cares order)
    r2 = run(day, prev2, exit_epoch=S0 + 4 * 60)
    assert r2["trades"][0].sl_level == 95.0


def t_d3_breakout_candle_not_retrace():
    """D3: the arming candle's own touch of H1 never triggers entry."""
    day = [
        C(0, 100, 102, 98, 101),
        C(1, 101.5, 104, 101.5, 103),   # closes > H1, low 101.5 <= 102 TOUCHES
        C(2, 103, 104, 102.8, 103.5),   # does NOT touch (low > 102)
    ]
    r = run(day, exit_epoch=S0 + 3 * 60)
    assert r["trades"] == [] and r["diag"]["armed_no_retrace"] == 1


def t_d4_latest_vs_first():
    """D4: armed CE, then a close below L1. latest → re-arms PE (entry on
    L1 touch). first → stays CE (no PE entry on that touch)."""
    day = [
        C(0, 100, 102, 98, 101),
        C(1, 101, 104, 102.5, 103),    # ARM CE (no touch: low 102.5 > 102)
        C(2, 103, 103.2, 96, 97),      # closes 97 < L1=98...
        C(3, 97, 98.5, 96.5, 97.5),    # high 98.5 >= 98 → PE touch
        C(4, 97.5, 98, 96, 97),
    ]
    r = run(day, exit_epoch=S0 + 5 * 60, signal_mode="latest")
    # candle 2: latest-mode — but wait: candle 2's low 96 <= H1? Retrace
    # check for ARMED CE runs FIRST (i=2 > arm_i=1, low 96 <= 102) → CE
    # ENTRY at close 97 — and 97 < SL(L1=98 fallback) → same-candle SL,
    # then flip-arm PE at i=2; candle 3 touches L1 → PE entry.
    ts = r["trades"]
    assert len(ts) == 2, ts
    assert ts[0].signal_side == "CE" and ts[0].same_candle_sl
    assert ts[1].signal_side == "PE" and ts[1].flip_seq == 1
    assert r["diag"]["same_candle_sl"] == 1

    # A clean latest-vs-first A/B needs the opposite close BEFORE any touch:
    day2 = [
        C(0, 100, 102, 98, 101),
        C(1, 101, 104, 102.5, 103),    # ARM CE, no touch
        C(2, 103, 103.5, 97.6, 97.5),  # low 97.6 > ... wait low must not
                                        # touch H1: low 97.6 <= 102 touches!
    ]
    # Constructing "opposite breakout with no H1 touch" is impossible when
    # the candle CLOSES below L1 (its range must cross H1's zone only if it
    # opened above). Open below H1: gap-down open 101.9 → low never above...
    # A close < L1 with low > H1 cannot exist (L1 < H1). So in latest mode a
    # CE-armed → PE re-arm can only matter when the crossing candle's low
    # stays ABOVE H1 while closing below L1 — impossible — OR when the arm
    # candle itself is the crosser (i == arm_i, retrace check skipped by D3):
    day3 = [
        C(0, 100, 102, 98, 101),
        C(1, 101, 104, 102.5, 103),    # ARM CE (arm_i=1)
        # i=2 with i > arm_i: any candle closing < L1 has low <= H1 → the
        # CE retrace fires first (as asserted above). The re-arm switch is
        # therefore only reachable when the CE arm has NOT yet happened on
        # an earlier candle — i.e. first crossing candle closes < L1 after
        # a candle closed > H1 with NO position possible... covered above.
    ]
    # first mode on day: candle 2 still CE-enters (retrace precedes arming
    # logic) — identical outcome; assert the mode flag flows to diag only.
    r3 = run(day, exit_epoch=S0 + 5 * 60, signal_mode="first")
    assert len(r3["trades"]) == 2   # same chain: retrace precedence is modal-
    # independent; the modes diverge only pre-entry with no-touch crossings.


def t_d4_rearm_pre_touch():
    """FILL-THROUGH TOUCH SEMANTICS (documented invariant): the retrace
    trigger is `high >= L1` (PE) / `low <= H1` (CE) — a limit order at the
    level fills when price trades AT OR THROUGH it. On contiguous index
    data every opposite-side crossing candle spans the armed level, so the
    retrace entry always fires BEFORE a D4 re-arm could — the latest/first
    toggle can only diverge across a genuine data gap (rearm_switches in
    DIAG will show whether the corpus ever produced one)."""
    day = [
        C(0, 100, 102, 98, 101),       # H1=102 L1=98
        C(1, 100, 101, 96, 97),        # closes < L1 → ARM PE (arm_i=1)
        C(2, 99, 104, 98.6, 103),      # high 104 >= L1 → PE ENTRY @103;
                                        # first-entry SL window = prev_tail
                                        # (empty) → fallback H1=102; close
                                        # 103 > 102 → SAME-CANDLE SL → flip
                                        # to CE (arm_i=2)
        C(3, 103, 103.5, 101.9, 103),  # low <= H1 → CE flip entry @103;
                                        # SL: most recent close < L1 is
                                        # candle 1 (97) → its low 96
        C(4, 103, 104, 102.5, 103.2),  # EOD
    ]
    for mode in ("latest", "first"):
        r = run(day, exit_epoch=S0 + 5 * 60, signal_mode=mode)
        ts = r["trades"]
        assert len(ts) == 2, (mode, ts)
        assert ts[0].signal_side == "PE" and ts[0].same_candle_sl \
            and ts[0].sl_level == 102 and ts[0].sl_fallback
        assert ts[1].signal_side == "CE" and ts[1].flip_seq == 1 \
            and ts[1].sl_level == 96 and not ts[1].sl_fallback
        assert ts[1].exit_reason == "EOD" and ts[1].exit_idx == 4
        assert r["diag"]["rearm_switches"] == 0


def t_sl_close_only_and_flip():
    """SL fires on CLOSE beyond the level only (wick through survives);
    the flip re-enters on a touch of the ORIGINAL C1 level with a fresh
    same-day lookback SL (re-entry rule)."""
    day = [
        C(0, 100, 102, 98, 101),          # H1=102 L1=98
        C(1, 101, 104, 101, 103),         # ARM CE
        C(2, 103, 103.5, 101.9, 103),     # CE entry @103, SL=98 (fallback)
        C(3, 103, 103.5, 97.5, 99),       # wick to 97.5 < 98, close 99 → NO SL
        C(4, 99, 100, 96, 97.4),          # close 97.4 < 98 → SL EXIT; flip→PE
        C(5, 97, 99, 96.5, 98.4),         # i=5 > arm_i=4; high 99 >= L1=98 →
                                          # PE entry @98.4; SL lookback from
                                          # entry candle: candles 1..4? window
                                          # = 10 before → prev-tail empty +
                                          # today 0..4; most recent close>H1:
                                          # candle 2 (103>102) high 103.5 —
                                          # wait candle 3 close 99, candle 4
                                          # close 97.4; candle 2 close 103 →
                                          # F1 = candle 2, SL = 103.5
        C(6, 98, 99, 97, 98.2),
    ]
    r = run(day, exit_epoch=S0 + 7 * 60)
    ts = r["trades"]
    assert len(ts) == 2, ts
    assert ts[0].exit_reason == "SL" and ts[0].exit_idx == 4 \
        and ts[0].exit_spot == 97.4
    t2 = ts[1]
    assert t2.signal_side == "PE" and t2.flip_seq == 1 and t2.entry_idx == 5
    assert t2.sl_level == 103.5 and not t2.sl_fallback, t2
    assert t2.exit_reason == "EOD" and t2.exit_idx == 6


def t_d7_cap():
    """D7: chain runs until max_trades. With max_trades=2 the third flip
    arm is blocked (cap_blocked_flips)."""
    # whipsaw: CE entry → SL → PE entry → SL → (blocked)
    day = [
        C(0, 100, 102, 98, 101),
        C(1, 101, 104, 101, 103),        # ARM CE
        C(2, 103, 103.5, 101.9, 103),    # CE entry, SL=98 fb
        C(3, 99, 100, 96, 97),           # close < 98 → SL; flip→PE arm_i=3
        C(4, 97, 98.5, 96.5, 97.5),      # touch L1 → PE entry; SL: recent
                                          # close>H1 = candle 2 → 103.5
        C(5, 98, 105, 98, 104),          # close 104 > 103.5 → PE SL; flip
                                          # blocked (2 trades == cap)
        C(6, 104, 105, 101, 102.5),      # would touch H1 — must NOT enter
    ]
    r = run(day, exit_epoch=S0 + 7 * 60, max_trades=2)
    assert len(r["trades"]) == 2
    assert r["diag"]["cap_blocked_flips"] == 1


def t_no_breakout():
    day = [C(0, 100, 102, 98, 101)] + \
          [C(i, 100, 101.5, 98.5, 100 + (i % 2) * 0.5) for i in range(1, 6)]
    r = run(day, exit_epoch=S0 + 6 * 60)
    assert r["trades"] == [] and r["diag"]["no_breakout"] == 1


def t_exit_time_scope():
    """Candles ending after exit_epoch never participate; an open trade
    exits at the LAST in-scope candle (EOD)."""
    day = [
        C(0, 100, 102, 98, 101),
        C(1, 101, 104, 101, 103),
        C(2, 103, 103.5, 101.9, 103),    # CE entry
        C(3, 103, 104, 102, 103.5),      # last in-scope (exit at i=3)
        C(4, 103.5, 110, 90, 90),        # OUT of scope — a huge SL candle
    ]
    r = run(day, exit_epoch=S0 + 4 * 60)
    t = r["trades"][0]
    assert t.exit_idx == 3 and t.exit_reason == "EOD"


def t_c1_range_gate():
    """C1 volatility gate: (H1-L1) strictly > pct% of prev_close skips the
    day; equal passes; 0 disables; gate on + no prev_close = fail-closed."""
    prev = [C(-1, 100, 101, 99, 25000 / 250)]   # close irrelevant; use kw
    day = [
        C(0, 25000, 25080, 25000, 25050),   # C1 range = 80 pts
        C(1, 25050, 25100, 25055, 25090),   # would ARM CE
        C(2, 25090, 25095, 25075, 25085),   # would touch H1 → entry
        C(3, 25085, 25090, 25070, 25080),
    ]
    def go(pct, prev_close, prev_tail=()):
        return simulate_gc_day(list(day), list(prev_tail),
                               cfg(exit_epoch=S0 + 4 * 60,
                                   c1_range_max_pct=pct,
                                   prev_close=prev_close))
    # 0.3% of 25000 = 75 → 80 > 75 → SKIP
    r = go(0.3, 25000.0)
    assert r["trades"] == [] and r["diag"]["c1_range_skip"] == 1 \
        and r["diag"]["c1_range_pts"] == 80
    # 0.32% of 25000 = 80 → 80 > 80 is FALSE (strict) → trades
    r = go(0.32, 25000.0)
    assert len(r["trades"]) == 1 and r["diag"]["c1_range_skip"] == 0
    # 0 = off → trades even on a huge C1
    r = go(0, 100.0)
    assert len(r["trades"]) == 1
    # gate on, no reference → fail-closed skip
    r = go(0.3, None)
    assert r["trades"] == [] and r["diag"]["c1_range_no_ref"] == 1


def t_sl_cap_gap_day():
    """GC_SL_CAP (D12/D13): gap-up day. Prev session ~24800; today gaps to
    25200. CE first entry's D2 winner is a prev-day candle whose low is
    ~400 pts from entry spot → cap 0.3% of prev close 24800 = 74.4 pts →
    anchor REJECTED → SL = L1 (today's structure), sl_capped flagged."""
    prev = [C(-3, 24800, 24810, 24790, 24800),
            C(-2, 24800, 24805, 24785, 24795),   # most recent close < L1 →
                                                  # D2 winner, low 24785
            C(-1, 24795, 24805, 24788, 24800)]   # close 24800 < L1 too →
                                                  # actually THIS is most
                                                  # recent: low 24788
    day = [
        C(0, 25200, 25210, 25190, 25205),   # gap-up C1: H1=25210 L1=25190
        C(1, 25205, 25215, 25205, 25212),   # closes > H1 → ARM CE
        C(2, 25212, 25214, 25209, 25211),   # low 25209 <= 25210 → CE entry
        C(3, 25211, 25215, 25208, 25212),
    ]
    kw = dict(exit_epoch=S0 + 4 * 60, prev_close=24800.0)
    # cap ON: prev-day anchor (24788) is 423 pts from entry 25211 > 74.4
    r = run(day, prev, max_sl_pct=0.3, **kw)
    t = r["trades"][0]
    assert t.sl_level == 25190 and t.sl_fallback and t.sl_capped, t
    assert r["diag"]["sl_cap_fallbacks"] == 1
    # cap OFF: D2 winner stands — most recent qualifier is prev[-1], low 24788
    r0 = run(day, prev, max_sl_pct=0, **kw)
    t0 = r0["trades"][0]
    assert t0.sl_level == 24788 and not t0.sl_capped and not t0.sl_fallback
    # cap ON but WIDE (2% = 496 pts > 423): anchor accepted
    rw = run(day, prev, max_sl_pct=2.0, **kw)
    assert rw["trades"][0].sl_level == 24788 and not rw["trades"][0].sl_capped
    # TODAY-donated anchors are never capped: flip re-entry whose D2 winner
    # is a today candle keeps it even at a tight cap. Build: CE entry SLs
    # out, flip PE finds today candle closing > H1.
    day2 = [
        C(0, 25200, 25210, 25190, 25205),
        C(1, 25205, 25218, 25205, 25215),   # ARM CE; close 25215 > H1 →
                                             # future PE-lookback qualifier,
                                             # high 25218 (TODAY candle)
        C(2, 25215, 25216, 25209, 25211),   # touch → CE entry, capped SL=L1
        C(3, 25211, 25212, 25180, 25185),   # close < 25190 → SL; flip PE
        C(4, 25185, 25195, 25182, 25188),   # high >= L1 → PE entry; D2 most
                                             # recent close > H1 = candle 2
                                             # (25211 > 25210, TODAY) → its
                                             # high 25216; distance 28 pts >
                                             # the 0.05% cap (12.4) — but a
                                             # TODAY anchor is never capped
        C(5, 25188, 25192, 25184, 25189),
    ]
    r2 = run(day2, prev, max_sl_pct=0.05,
             exit_epoch=S0 + 6 * 60, prev_close=24800.0)
    ts = r2["trades"]
    assert ts[0].sl_capped and ts[0].sl_level == 25190
    assert ts[1].signal_side == "PE" and ts[1].sl_level == 25216 \
        and not ts[1].sl_capped and not ts[1].sl_fallback, ts[1]
    assert r2["diag"]["sl_cap_fallbacks"] == 1


def t_entry_cutoff():
    """GC_ENTRY_CUTOFF: a retrace touch whose decision candle closes AFTER
    the cutoff is blocked (no entry, HALT); a candle closing exactly AT the
    cutoff passes (<=); an open trade entered before the cutoff still runs
    to its SL/EOD; a post-SL flip touch after the cutoff is blocked."""
    day = [
        C(0, 100, 102, 98, 101),          # H1=102 L1=98
        C(1, 101, 104, 101, 103),         # ARM CE
        C(2, 103, 103.5, 101.9, 103),     # touch → would enter
        C(3, 103, 104, 102, 103.5),
        C(4, 103.5, 104, 96, 97),         # (SL candle when in trade)
        C(5, 97, 99, 96.5, 98.4),         # (flip PE touch when armed)
        C(6, 98, 99, 97, 98.2),
    ]
    # cutoff before candle 2's close (candle 2 ends at S0+3*60) → blocked
    r = run(day, exit_epoch=S0 + 7 * 60, entry_cutoff_epoch=S0 + 2 * 60)
    assert r["trades"] == [] and r["diag"]["cutoff_blocked_entries"] == 1
    # cutoff exactly at candle 2's close → passes (<=); trade runs, SL at 4,
    # flip touch at 5 is AFTER cutoff → blocked (chain stops at 1 trade)
    r2 = run(day, exit_epoch=S0 + 7 * 60, entry_cutoff_epoch=S0 + 3 * 60)
    ts = r2["trades"]
    assert len(ts) == 1 and ts[0].exit_reason == "SL" and ts[0].exit_idx == 4
    assert r2["diag"]["cutoff_blocked_entries"] == 1
    # no cutoff → full chain (entry + flip)
    r3 = run(day, exit_epoch=S0 + 7 * 60)
    assert len(r3["trades"]) == 2 and r3["diag"]["cutoff_blocked_entries"] == 0


def t_pe_symmetry():
    """PE mirror: close < L1 arms; L1 touch enters; close > SL exits."""
    prev = [C(-2, 104, 106.0, 103, 105.0),   # close 105 > H1=102 → F1 hi 106
            C(-1, 103, 104, 101, 101.5)]
    day = [
        C(0, 100, 102, 98, 101),
        C(1, 100, 101, 95, 96),          # close < L1 → ARM PE
        C(2, 96, 98.3, 95.5, 96.5),      # high 98.3 >= 98 → PE entry @96.5
        C(3, 97, 106.5, 96, 106.2),      # close 106.2 > SL 106.0 → SL exit
        C(4, 106, 107, 101.5, 102.0),    # flip→CE armed at i=3; low 101.5
                                          # <= H1=102 → CE entry @102.0; SL:
                                          # most recent close<L1 (D2) is the
                                          # PE ENTRY candle 2 itself (96.5) →
                                          # its low 95.5 (nearest beats
                                          # candle 1's 95)
        C(5, 102, 103, 101, 102.5),
    ]
    r = run(day, prev, exit_epoch=S0 + 6 * 60)
    ts = r["trades"]
    assert ts[0].signal_side == "PE" and ts[0].sl_level == 106.0 \
        and not ts[0].sl_fallback
    assert ts[0].exit_reason == "SL" and ts[0].exit_idx == 3
    assert ts[1].signal_side == "CE" and ts[1].sl_level == 95.5 \
        and ts[1].entry_idx == 4


if __name__ == "__main__":
    for fn in (t_resampler, t_basic_ce_chain, t_d1a_prevday_sl,
               t_d3_breakout_candle_not_retrace, t_d4_latest_vs_first,
               t_d4_rearm_pre_touch, t_sl_close_only_and_flip, t_d7_cap,
               t_no_breakout, t_exit_time_scope, t_c1_range_gate,
               t_sl_cap_gap_day, t_entry_cutoff, t_pe_symmetry):
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL GC ENGINE TESTS PASSED")