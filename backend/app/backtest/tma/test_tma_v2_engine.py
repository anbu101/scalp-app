# backend/app/backtest/tma/test_tma_v2_engine.py
#
# ── TMA_V2 ENGINE TESTS ── synthetic 5m bars, hand-reasoned expectations
# (house rule: every engine branch tested standalone before the runner
# touches it). Small test-only EMA periods (2/3/4/5) keep the fixtures
# short; the stack/transition/xover logic is period-agnostic.
#
# Run standalone:  python3 test_tma_v2_engine.py   (from this directory)

from __future__ import annotations

import sys

try:
    from app.backtest.tma.tma_v2_engine import (
        EXIT_REF_MAX, EXIT_REF_MIN, REF_KEYS, build_signals_v2,
        compute_state_v2, ema_series, sl_tp_levels, xover_exit_ts_v2,
    )
except ImportError:
    from tma_v2_engine import (  # type: ignore
        EXIT_REF_MAX, EXIT_REF_MIN, REF_KEYS, build_signals_v2,
        compute_state_v2, ema_series, sl_tp_levels, xover_exit_ts_v2,
    )

TF = 300
PK = dict(p1=2, p2=3, p3=4, p4=5)   # test-only small periods


def bars(closes, t0=0):
    return [{"ts": t0 + i * TF, "open": c, "high": c, "low": c, "close": c}
            for i, c in enumerate(closes)]


FAILS = []


def check(name, cond, detail=""):
    tag = "ok  " if cond else "MISS"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ──────────────────────────────────────────────────────────────────────
# 1. stack detection: monotonic ramp orders the EMAs by period
# ──────────────────────────────────────────────────────────────────────
def test_stack_detection():
    up = bars([100 + 2 * i for i in range(20)])
    st = compute_state_v2(up, **PK)
    # warmup: any index < p4-1 (=4) must be None
    check("stack None while unwarmed", all(st["stack_up"][i] is None
                                           for i in range(4)))
    # in a steady uptrend shorter EMAs sit above longer → stack_up True
    check("uptrend → stack_up True (tail)",
          all(st["stack_up"][i] is True for i in range(10, 20)))
    check("uptrend → stack_dn False (tail)",
          all(st["stack_dn"][i] is False for i in range(10, 20)))

    dn = bars([200 - 2 * i for i in range(20)])
    sd = compute_state_v2(dn, **PK)
    check("downtrend → stack_dn True (tail)",
          all(sd["stack_dn"][i] is True for i in range(10, 20)))
    check("downtrend → stack_up False (tail)",
          all(sd["stack_up"][i] is False for i in range(10, 20)))

    flat = bars([100.0] * 20)
    sf = compute_state_v2(flat, **PK)
    # equal EMAs: STRICT inequalities → neither stack (False, not None)
    check("flat → neither stack (strict)",
          sf["stack_up"][10] is False and sf["stack_dn"][10] is False)


# ──────────────────────────────────────────────────────────────────────
# 2. transitions: flat → ramp emits exactly ONE signal; up-down-up re-enters
# ──────────────────────────────────────────────────────────────────────
def test_transitions():
    closes = [100.0] * 10 + [100 + 3 * i for i in range(1, 15)]
    b = bars(closes)
    res = build_signals_v2(b, 0, session0=0,
                           entry_start_ts=0, entry_end_ts=10 ** 9,
                           tf_s=TF, **PK)
    ce = [s for s in res["signals"] if s["side"] == "CE"]
    check("flat→ramp: exactly one E2/CE signal", len(ce) == 1,
          f"got {len(ce)}")
    check("no E1 on an up move",
          not [s for s in res["signals"] if s["side"] == "PE"])
    if ce:
        st = res["state"]
        i = (ce[0]["ts"] // TF) - 1
        check("signal bar is the first True after a non-True",
              st["stack_up"][i] is True and st["stack_up"][i - 1] is not True)
        check("cond stamped E2", ce[0]["cond"] == "E2")

    # up, hard down, up again → two CE transitions and one PE in between
    closes2 = ([100.0] * 8
               + [100 + 3 * i for i in range(1, 15)]
               + [142 - 4 * i for i in range(1, 15)]
               + [86 + 4 * i for i in range(1, 15)])
    b2 = bars(closes2)
    r2 = build_signals_v2(b2, 0, 0, 0, 10 ** 9, tf_s=TF, **PK)
    ce2 = [s for s in r2["signals"] if s["side"] == "CE"]
    pe2 = [s for s in r2["signals"] if s["side"] == "PE"]
    check("re-entry: two CE transitions across the V", len(ce2) == 2,
          f"got {len(ce2)}")
    check("re-entry: one PE transition in the trough", len(pe2) == 1,
          f"got {len(pe2)}")


# ──────────────────────────────────────────────────────────────────────
# 3. warmup blocking + session gating
# ──────────────────────────────────────────────────────────────────────
def test_warmup_and_session():
    # first decidable bar (index p4-1=4) already True → prev is None →
    # blocked_warmup, no emission
    up = bars([100 + 2 * i for i in range(12)])
    r = build_signals_v2(up, 0, 0, 0, 10 ** 9, tf_s=TF, **PK)
    check("first-True-at-warmup-edge blocked", r["diag"]["blocked_warmup"] >= 1
          and not r["signals"])

    # entry window excludes the transition → blocked_session
    closes = [100.0] * 10 + [100 + 3 * i for i in range(1, 8)]
    b = bars(closes)
    full = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF, **PK)
    sig_ts = full["signals"][0]["ts"]
    gated = build_signals_v2(b, 0, 0, sig_ts + TF, 10 ** 9, tf_s=TF, **PK)
    check("entry window gates emission",
          gated["diag"]["blocked_session"] == 1 and not gated["signals"])

    # warmup_count: signals never emitted on warmup-index bars
    r3 = build_signals_v2(b, len(b) - 1, b[-1]["ts"], 0, 10 ** 9,
                          tf_s=TF, **PK)
    check("no emission from warmup indices", not r3["signals"])


# ──────────────────────────────────────────────────────────────────────
# 4. crossover exit: inclusive 13-vs-89 (test: p1 vs p3), side-mapped
# ──────────────────────────────────────────────────────────────────────
def test_xover_exit():
    closes = ([100.0] * 8                               # decidable False first
              + [100 + 3 * i for i in range(1, 15)]     # up: e_p1 > e_p3
              + [142 - 4 * i for i in range(1, 15)])    # down: crosses under
    b = bars(closes)
    st = compute_state_v2(b, **PK)
    sig = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF, state=st,
                           **PK)["signals"]
    ce = [s for s in sig if s["side"] == "CE"][0]

    xts = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF)
    check("CE xover exit exists on the reversal", xts is not None)
    if xts is not None:
        i = (xts // TF) - 1
        check("exit bar satisfies e13 <= e89 (inclusive)",
              st["e13"][i] <= st["e89"][i])
        check("no earlier decidable bar satisfies it",
              all(not (st["e13"][j] is not None and st["e89"][j] is not None
                       and st["e13"][j] <= st["e89"][j])
                  for j in range((ce["ts"] // TF), i)))
        check("exit strictly after after_ts", xts > ce["ts"])

    # healthy trend, no reversal → None (EOD owns it)
    up = bars([100 + 3 * i for i in range(25)])
    stu = compute_state_v2(up, **PK)
    check("no exit while the trend holds",
          xover_exit_ts_v2(up, stu, "CE", 6 * TF, tf_s=TF) is None)

    # PE side mirrors: exit when e13 >= e89 after a down→up turn
    closes_pe = ([200.0] * 8
                 + [200 - 3 * i for i in range(1, 15)]
                 + [158 + 4 * i for i in range(1, 15)])
    bp = bars(closes_pe)
    stp = compute_state_v2(bp, **PK)
    pe = [s for s in build_signals_v2(bp, 0, 0, 0, 10 ** 9, tf_s=TF,
                                      state=stp, **PK)["signals"]
          if s["side"] == "PE"][0]
    xp = xover_exit_ts_v2(bp, stp, "PE", pe["ts"], tf_s=TF)
    check("PE xover exit exists on the up-turn", xp is not None)
    if xp is not None:
        i = (xp // TF) - 1
        check("PE exit bar satisfies e13 >= e89",
              stp["e13"][i] >= stp["e89"][i])


# ──────────────────────────────────────────────────────────────────────
# 4b. ── 2026-CHOP ── exit_ref=e55 fires EARLIER than e89 on a reversal
# ──────────────────────────────────────────────────────────────────────
def test_xover_exit_ref():
    closes = ([100.0] * 8
              + [100 + 3 * i for i in range(1, 15)]
              + [142 - 4 * i for i in range(1, 15)])
    b = bars(closes)
    st = compute_state_v2(b, **PK)
    ce = [s for s in build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                                      state=st, **PK)["signals"]
          if s["side"] == "CE"][0]
    x89 = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF, exit_ref="e89")
    x55 = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF, exit_ref="e55")
    check("ref55 and ref89 both find exits on the reversal",
          x55 is not None and x89 is not None)
    if x55 is not None and x89 is not None:
        # test periods: e55→p2(3), e89→p3(4); the shorter ref line is
        # closer to e13, so the reversal reaches it FIRST — never later
        check("ref55 exit is earlier (or equal) vs ref89", x55 <= x89,
              f"x55={x55} x89={x89}")
        i = (x55 // TF) - 1
        check("ref55 exit bar satisfies e13 <= e55",
              st["e13"][i] <= st["e55"][i])
    # default + unknown-ref fail-safe both reproduce e89 semantics
    check("default ref is e89",
          xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF) == x89)
    check("unknown ref falls back to e89",
          xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF,
                           exit_ref="e42") == x89)
    # healthy trend: ref55 does NOT exit early while the stack holds
    up = bars([100 + 3 * i for i in range(25)])
    stu = compute_state_v2(up, **PK)
    check("ref55 holds through a healthy trend",
          xover_exit_ts_v2(up, stu, "CE", 6 * TF, tf_s=TF,
                           exit_ref="e55") is None)


# ──────────────────────────────────────────────────────────────────────
# 4b2. ── EXIT_REF_CUSTOM ── arbitrary reference periods
# ──────────────────────────────────────────────────────────────────────
def test_exit_ref_custom():
    closes = ([100.0] * 8
              + [100 + 3 * i for i in range(1, 15)]
              + [142 - 4 * i for i in range(1, 15)])
    b = bars(closes)
    # custom period between the two presets (test scale: p2=3, p3=4 → 3.5
    # is not an int, so use the real-scale analogue on a 4-EMA state with
    # DEFAULT periods; the fixture is long enough for period 3)
    st = compute_state_v2(b, ref_period=3, **PK)
    check("custom ref builds state['eref']",
          "eref" in st and st.get("ref_period") == 3)
    check("eref equals a standalone EMA of that period",
          st["eref"] == ema_series([x["close"] for x in b], 3))
    # a preset period must NOT allocate an extra series
    st89 = compute_state_v2(b, ref_period=89, **PK)
    check("preset ref adds no extra series", "eref" not in st89)

    ce = [s for s in build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                                      state=st, **PK)["signals"]
          if s["side"] == "CE"][0]
    # numeric refs resolve: 2 (=p1 stack line) vs 3 (custom) vs 4 (=p3)
    x_fast = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF, exit_ref=3)
    x_slow = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF, exit_ref=4)
    check("custom-period ref resolves to an exit", x_fast is not None)
    if x_fast and x_slow:
        # test PK maps p3=4 → a *later* line than the custom 3
        check("shorter custom ref exits no later than a longer one",
              x_fast <= x_slow, f"{x_fast} vs {x_slow}")
        i = (x_fast // TF) - 1
        check("custom exit bar satisfies e13 <= eref",
              st["e13"][i] <= st["eref"][i])

    # resolution rules
    legacy = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF,
                              exit_ref="e89")
    numeric89 = xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF,
                                 exit_ref=89)
    check("numeric 89 ≡ legacy 'e89'", legacy == numeric89)
    check("default (no exit_ref) ≡ 89",
          xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF) == numeric89)
    check("garbage ref falls back to 89",
          xover_exit_ts_v2(b, st, "CE", ce["ts"], tf_s=TF,
                           exit_ref="nonsense") == numeric89)
    # custom period requested but state built WITHOUT it → fail-safe to 89
    check("custom ref with no eref in state falls back to 89",
          xover_exit_ts_v2(b, st89, "CE", ce["ts"], tf_s=TF,
                           exit_ref=70) == numeric89)
    check("bounds sane", EXIT_REF_MIN > 13 and EXIT_REF_MAX >= 144
          and 55 in REF_KEYS and 89 in REF_KEYS)


# ──────────────────────────────────────────────────────────────────────
# 4c. ── 2026-CHOP ── extension gate + EMA144 slope gate
# ──────────────────────────────────────────────────────────────────────
def test_entry_gates():
    closes = [100.0] * 10 + [100 + 3 * i for i in range(1, 15)]
    b = bars(closes)
    base = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF, **PK)
    check("gates-off baseline emits", len(base["signals"]) == 1)

    # extension gate: a permissive cap keeps the signal; a tiny cap
    # blocks it and counts blocked_extension
    loose = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                             max_extension_pct=50.0, **PK)
    check("loose extension cap keeps the signal",
          len(loose["signals"]) == 1
          and loose["diag"]["blocked_extension"] == 0)
    tight = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                             max_extension_pct=0.0001, **PK)
    check("tight extension cap blocks + counts",
          not tight["signals"]
          and tight["diag"]["blocked_extension"] == 1)

    # ── EXT_BAND ── floor: a permissive floor keeps the signal, a huge
    # floor blocks it into its OWN funnel (never blocked_extension)
    lo = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                          min_extension_pct=0.0001, **PK)
    check("tiny floor keeps the signal",
          len(lo["signals"]) == 1
          and lo["diag"]["blocked_extension_min"] == 0)
    hi = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                          min_extension_pct=99.0, **PK)
    check("huge floor blocks into blocked_extension_min",
          not hi["signals"] and hi["diag"]["blocked_extension_min"] == 1
          and hi["diag"]["blocked_extension"] == 0)
    # band: floor and ceiling are disjoint funnels — a blocked signal is
    # counted exactly once
    both = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                            min_extension_pct=99.0,
                            max_extension_pct=0.0001, **PK)
    check("floor wins when both would block (counted once)",
          both["diag"]["blocked_extension_min"]
          + both["diag"]["blocked_extension"] == 1
          and both["diag"]["blocked_extension_min"] == 1)
    # a satisfiable band admits the signal
    band = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                            min_extension_pct=0.0001,
                            max_extension_pct=99.0, **PK)
    check("satisfiable band admits", len(band["signals"]) == 1)
    check("floor off by default ≡ no floor blocks",
          base["diag"]["blocked_extension_min"] == 0)

    # slope gate: uptrend transition has RISING e144 → passes; forcing
    # the mirror side logic — a downtrend PE transition with rising
    # long-run average must block
    sg = build_signals_v2(b, 0, 0, 0, 10 ** 9, tf_s=TF,
                          slope_gate=True, **PK)
    check("slope gate passes a with-trend transition",
          len(sg["signals"]) == 1 and sg["diag"]["blocked_slope"] == 0)
    # long up-ramp then a sharp collapse: the PE transition fires while
    # e144 (test p4=5) is... short periods track fast, so build a shape
    # where the slope lookback (SLOPE_BARS=6) straddles the peak: e144
    # is still >= its 6-bars-ago value right at the PE transition
    closes2 = [100.0] * 6 + [100 + 6 * i for i in range(1, 13)]         + [172 - 20 * i for i in range(1, 5)]
    b2 = bars(closes2)
    all2 = build_signals_v2(b2, 0, 0, 0, 10 ** 9, tf_s=TF, **PK)
    pe_all = [s for s in all2["signals"] if s["side"] == "PE"]
    if pe_all:   # fixture yields a PE transition right after the peak
        g2 = build_signals_v2(b2, 0, 0, 0, 10 ** 9, tf_s=TF,
                              slope_gate=True, **PK)
        pe_g = [s for s in g2["signals"] if s["side"] == "PE"]
        check("slope gate blocks the against-slope PE (counts)",
              len(pe_g) < len(pe_all)
              and g2["diag"]["blocked_slope"] >= 1,
              f"all={len(pe_all)} gated={len(pe_g)} "
              f"blk={g2['diag']['blocked_slope']}")
    else:
        check("slope-gate fixture produced a PE transition", False,
              "no PE in ungated run — fixture needs reshaping")


# ──────────────────────────────────────────────────────────────────────
# 5. sl_tp_levels: SELL byte-matches V1 math; BUY is the honest mirror
# ──────────────────────────────────────────────────────────────────────
def test_sl_tp_levels():
    # SELL PCT — V1: sl=ep*(1+30%)=130, tp=max(.05, ep*(1-50%))=50
    sl, tp = sl_tp_levels(100, "SELL", 30, 50, "PCT", "PCT")
    check("SELL PCT", sl == 130.0 and tp == 50.0, f"{sl}/{tp}")
    # SELL PTS
    sl, tp = sl_tp_levels(100, "SELL", 20, 30, "PTS", "PTS")
    check("SELL PTS", sl == 120.0 and tp == 70.0, f"{sl}/{tp}")
    # SELL ABS valid / invalid clamps
    sl, tp = sl_tp_levels(100, "SELL", 150, 40, "ABS", "ABS")
    check("SELL ABS valid", sl == 150.0 and tp == 40.0, f"{sl}/{tp}")
    sl, tp = sl_tp_levels(100, "SELL", 80, 120, "ABS", "ABS")
    check("SELL ABS wrong-side clamps OFF", sl is None and tp is None)
    # SELL TP floor
    _, tp = sl_tp_levels(100, "SELL", 0, 120, "PCT", "PTS")
    check("SELL TP floored at 0.05", tp == 0.05, f"{tp}")
    # zero = off
    sl, tp = sl_tp_levels(100, "SELL", 0, 0)
    check("0 = disabled", sl is None and tp is None)

    # BUY PCT — long: sl below, tp above
    sl, tp = sl_tp_levels(100, "BUY", 30, 50, "PCT", "PCT")
    check("BUY PCT", sl == 70.0 and tp == 150.0, f"{sl}/{tp}")
    # BUY PTS + over-deep SL clamps OFF (level <= 0)
    sl, tp = sl_tp_levels(100, "BUY", 20, 30, "PTS", "PTS")
    check("BUY PTS", sl == 80.0 and tp == 130.0, f"{sl}/{tp}")
    sl, _ = sl_tp_levels(10, "BUY", 15, 0, "PTS", "PCT")
    check("BUY PTS SL <= 0 clamps OFF", sl is None)
    # BUY ABS valid / invalid
    sl, tp = sl_tp_levels(100, "BUY", 60, 180, "ABS", "ABS")
    check("BUY ABS valid", sl == 60.0 and tp == 180.0, f"{sl}/{tp}")
    sl, tp = sl_tp_levels(100, "BUY", 120, 80, "ABS", "ABS")
    check("BUY ABS wrong-side clamps OFF", sl is None and tp is None)
    # BUY PCT SL floor
    sl, _ = sl_tp_levels(100, "BUY", 100, 0, "PCT", "PCT")
    check("BUY PCT SL floored at 0.05", sl == 0.05, f"{sl}")


# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_stack_detection()
    test_transitions()
    test_warmup_and_session()
    test_xover_exit()
    test_xover_exit_ref()
    test_exit_ref_custom()
    test_entry_gates()
    test_sl_tp_levels()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print("\nALL TMA_V2 ENGINE TESTS PASSED")