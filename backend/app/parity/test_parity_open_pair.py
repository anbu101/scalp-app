# backend/app/parity/test_parity_open_pair.py
#
# ── PARITY_OPEN_PAIR_20261102 ── a leg open on one side and closed on the other
# is the SAME leg. Replays Icarus (IC_V2) 2026-11-02, paper book, as the Parity
# page showed it: 4 matched · 2 missed · 2 extra · "trades differ".
#   cd backend && PYTHONPATH=$PWD python3 app/parity/test_parity_open_pair.py
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["HOME"] = tempfile.mkdtemp(prefix="parity_open_")
from app.parity import parity_core as PC                     # noqa: E402

FAILS, PASSES = [], [0]
IST = timezone(timedelta(hours=5, minutes=30))


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:400]}]")


def T(hh, mm, ss=0):
    return int(datetime(2026, 11, 2, hh, mm, ss, tzinfo=IST).timestamp())


P = "NIFTY26N03"


def leg(sym, side, ets, ent, xts=None, ext=None, reason=None, qty=65):
    return {"symbol": P + sym, "side": side, "type": sym[-2:], "strike": float(sym[:5]), "qty": qty,
            "entry_ts": ets, "exit_ts": xts, "entry": ent, "exit": ext, "reason": reason, "synthetic": False}


BT = [leg("23300CE", "SELL", T(9, 18), 76.70, T(9, 45), 108.91, "SL"),
      leg("23200PE", "SELL", T(9, 18), 83.45, T(13, 9), 83.45, "MTC_COST"),
      leg("23350CE", "BUY", T(9, 46), 82.10, T(11, 46), 57.47, "SL"),
      leg("23200PE", "BUY", T(13, 10), 87.75, T(14, 14), 61.42, "SL"),
      leg("23750CE", "BUY", T(9, 18), 3.20),                        # wings: backtest carries them
      leg("22400PE", "BUY", T(9, 18), 3.60)]
APP = [leg("23300CE", "SELL", T(9, 18, 13), 75.25, T(9, 45, 14), 107.80, "SL", 650),
       leg("23200PE", "SELL", T(9, 18, 13), 83.85, T(13, 9, 7), 87.45, "MTC_COST", 650),
       leg("23350CE", "BUY", T(9, 46, 29), 80.15, T(11, 48, 7), 55.85, "SL", 650),
       leg("23200PE", "BUY", T(13, 10, 24), 88.30, T(13, 59, 24), 71.55, "MANUAL", 650),
       leg("23750CE", "BUY", T(9, 18, 13), 3.20, T(13, 59, 25), 2.85, "MANUAL", 650),
       leg("22400PE", "BUY", T(9, 18, 13), 3.70, T(13, 59, 31), 2.40, "MANUAL", 650)]


def run(bt, app):
    """run_day's matching, without the replay."""
    bt_closed = [b for b in bt if b["exit_ts"]]
    bt_open = [dict(b, open=True) for b in bt if not b["exit_ts"]]
    rows = PC.match(bt_closed, [a for a in app if a["exit_ts"]])
    for r in PC.match(bt_open, [dict(a, open=True) for a in app if not a["exit_ts"]]):
        r["open"] = True
        rows.append(r)
    return rows


print("── 1. before: what the page showed")
old = run([dict(b) for b in BT], [dict(a) for a in APP])
s0 = PC.summarise(old)
check("old matching: 4 matched · 2 missed · 2 extra · trades differ",
      (s0["matched"], s0["missed"], s0["extra"], s0["verdict"]) == (4, 2, 2, "trades differ"), s0)

print("── 2. after: wings paired across open/closed")
rows = PC.pair_across_open(run([dict(b) for b in BT], [dict(a) for a in APP]))
s = PC.summarise(rows)
check("6 matched · 0 missed · 0 extra", (s["matched"], s["missed"], s["extra"]) == (6, 0, 0), s)
wings = [r for r in rows if r.get("open_side")]
check("the two wings carry open_side='bt' (backtest holds, app closed)",
      sorted(r["bt"]["symbol"][-7:] for r in wings) == ["22400PE", "23750CE"]
      and all(r["open_side"] == "bt" for r in wings), wings)
check("wing rows: entry deltas kept, no exit delta, app P/L kept",
      all(r["entry_delta_min"] is not None and r["exit_delta_min"] is None and r["app_pnl"] is not None
          for r in wings))
check("no row left flagged 'open · entry only'", not any(r.get("open") for r in rows))
check("verdict: timing differs — L1·ADJ's engine SL is 2.1 min apart; 3 manual exits counted",
      s["verdict"] == "same trades, timing differs" and s["manual_exits"] == 3, s)
res = {"ok": True, "name": "Icarus", "book": "PAPER", "summary": s, "rows": rows}
check("status line '⚠ exit 2.1 min apart · 3 manual exits' (was '✗ 2 missed, 2 extra')",
      PC.line_for(res) == "Icarus ⚠ exit 2.1 min apart · 3 manual exits", PC.line_for(res))
check("live book tagged", PC.line_for(dict(res, book="LIVE")) == "Icarus (live) ⚠ exit 2.1 min apart · 3 manual exits")
same = [dict(a) for a in APP]
same[2] = dict(same[2], exit_ts=T(11, 46, 20))                                   # engine exits all within the minute
r1 = PC.pair_across_open(run([dict(b) for b in BT], same))
s1 = PC.summarise(r1)
check("when every engine exit agrees and only a person's exits differ → 'app exited manually'",
      s1["verdict"] == "same trades, app exited manually", s1)
check("…status line '⚠ manual exit · 3 legs'",
      PC.line_for({"ok": True, "name": "Icarus", "summary": s1, "rows": r1}) == "Icarus ⚠ manual exit · 3 legs")
check("P/L totals unchanged by the pairing (backtest open wings contribute 0)",
      abs(s["app_pnl"] - s0["app_pnl"]) < 0.01 and abs(s["bt_pnl"] - s0["bt_pnl"]) < 0.01, (s, s0))

print("── 3. guards")
app_open = [dict(a) for a in APP]
app_open[4] = dict(app_open[4], exit_ts=None, exit=None, reason=None)          # app still holds a wing
bt_closed = [dict(b) for b in BT]
bt_closed[4] = dict(bt_closed[4], exit_ts=T(15, 0), exit=1.0, reason="SL")     # backtest closed it
r2 = PC.pair_across_open(run(bt_closed, app_open))
w = [r for r in r2 if r.get("open_side") == "app"]
check("reverse case (app open, backtest closed) pairs without error", len(w) == 1 and w[0]["exit_px_diff"] is None, r2)
check("…and is not called a manual exit", PC.summarise(r2)["verdict"] == "same trades, timing differs")
far = [dict(a) for a in APP]
far[4] = dict(far[4], entry_ts=T(11, 0))                                        # 2h apart → different trade
r3 = PC.pair_across_open(run([dict(b) for b in BT], far))
check("entries 2 h apart are NOT paired", PC.summarise(r3)["missed"] == 1 and PC.summarise(r3)["extra"] == 1)
other = [dict(a) for a in APP]
other[4] = dict(other[4], symbol=P + "23800CE", strike=23800.0)
r4 = PC.pair_across_open(run([dict(b) for b in BT], other))
check("a different strike is NOT paired across open/closed", PC.summarise(r4)["missed"] == 1)
algo = [dict(a) for a in APP]
algo[3] = dict(algo[3], reason="SL")                                            # L2·ADJ: app's own SL, 14.6 min early
r6 = PC.pair_across_open(run([dict(b) for b in BT], algo))
check("an engine exit 14.6 min apart → 'timing differs', not 'manual'",
      PC.summarise(r6)["verdict"] == "same trades, timing differs", PC.summarise(r6))
s7 = PC.summarise(r6)
check("its status line names the exit gap, not 'entry 0.4 min apart'",
      PC.line_for({"ok": True, "name": "Icarus", "summary": s7, "rows": r6}) == "Icarus ⚠ exit 14.6 min apart · 2 manual exits",
      PC.line_for({"ok": True, "name": "Icarus", "summary": s7, "rows": r6}))

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PARITY OPEN-PAIR CHECKS PASSED")
