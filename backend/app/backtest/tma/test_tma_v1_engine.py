# backend/app/backtest/tma/test_tma_v1_engine.py — v2 SPREAD engine suite.
# Run: cd backend && python3 -m app.backtest.tma.test_tma_v1_engine
# (or copy pst_indicators.py beside for standalone)
import sys

try:
    from app.backtest.tma.tma_v1_engine import (
        build_signals, compute_state, ema_series, monitor_position_day,
        xover_exit_ts,
        open_minute_stop_fill, is_open_minute,    # ── OPEN_MIN_STOP_20261126 ──
    )
except ImportError:
    sys.path.insert(0, ".")
    sys.path.insert(0, "../pst")
    from tma_v1_engine import (  # type: ignore
        build_signals, compute_state, ema_series, monitor_position_day,
        xover_exit_ts,
        open_minute_stop_fill, is_open_minute,    # ── OPEN_MIN_STOP_20261126 ──
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

# ── OPEN_MIN_STOP_20261126 ── (DEF-080) TC-TMA2-0135…0142: stop touched inside the 09:15 candle
print("── opening-minute stop fill (DEF-080) ──")
import datetime as _dt
import os as _os
_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
def _t(d, hh, mm): return int(_dt.datetime(2026, 11, d, hh, mm, tzinfo=_IST).timestamp())
T16, T20, T16b = _t(16, 9, 15), _t(20, 9, 15), _t(16, 9, 16)
def _short(sl, entry=190.0, **kw):
    b = {"side": "CE", "action": "SELL", "entry_ts": T16 - 86400, "entry_price": entry, "sl_price": sl,
         "tp_price": None, "watch_from": 0, "last_close": entry, "last_ts": T16 - 86400}
    b.update(kw); return b
c16 = [opt(T16, 212.10, 290.60, 212.10, 288.80)]
r = monitor_position_day(_short(213.58), c16, None, None)
check("TC-TMA2-0141 monitor_position_day (the live parity reference) is unchanged: 16 Nov still fills AT the stop",
      r["exit_reason"] == "SL" and abs(r["exit_price"] - 213.58) < 1e-9)
f = open_minute_stop_fill(r, c16, True)
check("TC-TMA2-0135 16 Nov 25050CE: stop 213.58 touched in the 09:15 candle (high 290.60) → 252.09 (live 251.05)",
      f["exit_price"] == 252.09 and f["exit_reason"] == "SL" and f["exit_ts"] == T16 and f.get("open_minute_fill"))
c20 = [opt(T20, 180.00, 208.50, 165.55, 198.50)]
r = monitor_position_day(_short(186.14, entry=170.0, entry_ts=T20 - 86400, last_ts=T20 - 86400), c20, None, None)
check("TC-TMA2-0136 20 Nov 25450PE: stop 186.14, high 208.50 → 197.32 (paper 198.50)",
      open_minute_stop_fill(r, c20, True)["exit_price"] == 197.32)
cg = [opt(T16, 230.0, 290.6, 228.0, 288.8)]
r = monitor_position_day(_short(213.58), cg, None, None)
check("TC-TMA2-0137 a gap open through the stop keeps its fill at the OPEN (230.00)",
      r["exit_price"] == 230.0 and open_minute_stop_fill(r, cg, True) is r)
cl = [opt(T16b, 212.10, 290.60, 212.10, 288.80)]
r = monitor_position_day(_short(213.58), cl, None, None)
check("TC-TMA2-0138 the same candle at 09:16 keeps the stop price (only the opening minute changes)",
      not is_open_minute(T16b) and is_open_minute(T16) and open_minute_stop_fill(r, cl, True) is r
      and r["exit_price"] == 213.58)
lp = {"side": "CE", "action": "BUY", "entry_ts": T16 - 86400, "entry_price": 100.0, "sl_price": 80.0,
      "tp_price": None, "watch_from": 0, "last_close": 100.0, "last_ts": T16 - 86400}
cb = [opt(T16, 85.0, 86.0, 60.0, 62.0)]
r = monitor_position_day(lp, cb, None, None)
check("TC-TMA2-0139 a LONG stop 80 touched in the 09:15 candle (low 60) fills at the midpoint 70.00",
      r["exit_price"] == 80.0 and open_minute_stop_fill(r, cb, False)["exit_price"] == 70.0)
tp = {"side": "CE", "action": "SELL", "entry_ts": T16, "entry_price": 190.0, "sl_price": 300.0, "tp_price": 150.0,
      "exit_ts": T16, "exit_price": 150.0, "exit_reason": "TP"}
check("TC-TMA2-0140 not a stop (TP / XOVER / EOD), no candle for the exit minute, or no stop level → unchanged",
      open_minute_stop_fill(tp, c16, True) is tp
      and open_minute_stop_fill(dict(tp, exit_reason="SL", exit_price=300.0), [], True)["exit_price"] == 300.0
      and open_minute_stop_fill(dict(tp, exit_reason="SL", sl_price=None), c16, True)["exit_price"] == 150.0
      and open_minute_stop_fill(None, c16, True) is None)
_rd = _os.path.dirname(_os.path.abspath(__file__))
_n = [open(_os.path.join(_rd, f), encoding="utf-8").read().count("res = open_minute_stop_fill(res, ")
      for f in ("backtest_tma_runner.py", "backtest_tma_v2_runner.py")]
check("TC-TMA2-0142 both runners (Tomahawk, Timberwolf) apply it to the carried-day and entry-day exits", _n == [2, 2])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)