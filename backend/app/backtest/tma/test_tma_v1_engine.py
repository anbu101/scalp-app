# backend/app/backtest/tma/test_tma_v1_engine.py — v2 SPREAD engine suite.
# Run: cd backend && python3 -m app.backtest.tma.test_tma_v1_engine
# (or copy pst_indicators.py beside for standalone)
import sys

try:
    from app.backtest.tma.tma_v1_engine import (
        build_signals, compute_state, ema_series, monitor_position_day,
        xover_exit_ts,
    )
except ImportError:
    sys.path.insert(0, ".")
    sys.path.insert(0, "../pst")
    from tma_v1_engine import (  # type: ignore
        build_signals, compute_state, ema_series, monitor_position_day,
        xover_exit_ts,
    )

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    PASS += cond; FAIL += (not cond)
    print(f"  {'ok ' if cond else 'FAIL'}  {name}")

S0 = 1_700_000_000
def bar(i, close): return {"ts": S0 + i * 300, "close": close, "complete": True}
def opt(ts, o, h, l, c): return {"ts": ts, "open": o, "high": h, "low": l, "close": c}

# ── C1 signals (C2 machinery deleted) ─────────────────────────────────
print("── build_signals: C1 only ──")
# 100 warm bars flat, then both EMAs cross above e89 today
bars = [bar(i, 100.0) for i in range(100)] + [bar(100 + i, 100.0 + i * 3) for i in range(20)]
res = build_signals(bars, 100, S0 + 100 * 300, S0, S0 + 10**7)
sigs = res["signals"]
check("C1 CE emitted", any(s["side"] == "CE" and s["cond"] == "C1" for s in sigs))
check("no C2 anywhere", all(s["cond"] == "C1" for s in sigs))
check("diag has no c2 keys", not any(k.startswith("c2") for k in res["diag"]))

# stale: cross began before today → suppressed
bars2 = [bar(i, 100.0 + max(0, i - 50) * 3) for i in range(120)]
res2 = build_signals(bars2, 100, S0 + 100 * 300, S0, S0 + 10**7)
check("stale cross suppressed", res2["diag"]["c1_stale"] >= 0
      and all(s["cond"] == "C1" for s in res2["signals"]))

# xover: trend-side reversal exits
st = compute_state(bars)
x = xover_exit_ts(bars, st, "C1", "CE", after_ts=S0 + 119 * 300 + 300)
check("no exit while trend holds", x is None)

# ── monitor_position_day: SELL semantics ──────────────────────────────
print("── monitor: SELL leg ──")
D = S0 + 86400
def spos(**kw):
    b = {"side": "PE", "action": "SELL", "entry_ts": D, "entry_price": 100.0,
         "sl_price": 130.0, "tp_price": 50.0, "watch_from": 0,
         "last_close": 100.0, "last_ts": D}
    b.update(kw); return b

r = monitor_position_day(spos(), [opt(D, 100, 131, 99, 120)], None, None)
check("SELL SL when premium RISES, at level", r["exit_reason"] == "SL"
      and abs(r["exit_price"] - 130.0) < 1e-9)
r = monitor_position_day(spos(), [opt(D, 140, 145, 139, 141)], None, None)
check("SELL gap SL fills at open", r["exit_reason"] == "SL"
      and abs(r["exit_price"] - 140.0) < 1e-9)
r = monitor_position_day(spos(), [opt(D, 100, 101, 49, 60)], None, None)
check("SELL TP when premium FALLS, at level", r["exit_reason"] == "TP"
      and abs(r["exit_price"] - 50.0) < 1e-9)
r = monitor_position_day(spos(), [opt(D, 40, 45, 39, 42)], None, None)
check("SELL gap TP fills at open", r["exit_reason"] == "TP"
      and abs(r["exit_price"] - 40.0) < 1e-9)
r = monitor_position_day(spos(), [opt(D, 100, 131, 49, 100)], None, None)
check("SELL SL wins + ambiguous", r["exit_reason"] == "SL" and r["ambiguous_fill"])
r = monitor_position_day(spos(sl_price=None, tp_price=None),
                         [opt(D, 100, 101, 99, 100),
                          opt(D + 300, 100, 101, 99, 96.5)],
                         xover_ts=D + 300, hard_close_ts=None)
check("SELL xover at close", r["exit_reason"] == "XOVER"
      and abs(r["exit_price"] - 96.5) < 1e-9)
check("result carries action", r["action"] == "SELL")

# MTM cut, short: negative = mark ABOVE entry
r = monitor_position_day(spos(sl_price=None, tp_price=None),
                         [opt(D + 60 * i, 104 + i, 105 + i, 103 + i, 104 + i)
                          for i in range(8)],
                         None, None, mtm_cut_ts=D + 300)
check("SELL negative MTM (mark>entry) cut", r is not None
      and r["exit_reason"] == "MTM_CUT" and r["exit_price"] > 100.0)
r = monitor_position_day(spos(sl_price=None, tp_price=None),
                         [opt(D + 60 * i, 96 - i, 97 - i, 95 - i, 96 - i)
                          for i in range(8)],
                         None, None, mtm_cut_ts=D + 300)
check("SELL positive MTM carries", r is None)

# BUY legacy semantics untouched
b = {"side": "CE", "entry_ts": D, "entry_price": 100.0, "sl_price": 85.0,
     "tp_price": 120.0, "watch_from": 0, "last_close": 100.0, "last_ts": D}
r = monitor_position_day(dict(b), [opt(D, 100, 101, 84, 90)], None, None)
check("BUY SL at level unchanged", r["exit_reason"] == "SL"
      and abs(r["exit_price"] - 85.0) < 1e-9 and r["action"] == "BUY")
r = monitor_position_day(dict(b), [opt(D, 70, 75, 68, 74)], None, None)
check("BUY gap SL at open unchanged", r["exit_reason"] == "SL"
      and abs(r["exit_price"] - 70.0) < 1e-9)

# hard closes unchanged
r = monitor_position_day(spos(sl_price=None, tp_price=None),
                         [opt(D, 100, 101, 99, 104), opt(D + 60, 104, 105, 103, 105)],
                         None, hard_close_ts=D + 60)
check("hard close before bound", r["exit_reason"] == "EOD"
      and abs(r["exit_price"] - 104.0) < 1e-9)
r = monitor_position_day(spos(sl_price=None, tp_price=None), [],
                         None, hard_close_ts=D + 60, hard_close_reason="EOR")
check("data-gap EOR fallback", r["exit_reason"] == "EOR"
      and abs(r["exit_price"] - 100.0) < 1e-9)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)