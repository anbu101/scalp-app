# backend/app/backtest/tma/test_tma2_prerange_warmup.py
#
# ── TMA2_PRERANGE_WARMUP_20261102 / PARITY_TW_20261102 ── the TMA_V2 backtest
# warms its EMAs on the 5 sessions BEFORE the range, exactly like live, so a
# one-day replay sees the signals the app saw. Synthetic corpus, throw-away HOME.
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/tma/test_tma2_prerange_warmup.py
import os
import sqlite3
import sys
import tempfile
from datetime import date

os.environ["HOME"] = tempfile.mkdtemp(prefix="tma2_warm_")
from app.backtest.replay.replay_synth import build                       # noqa: E402
from app.backtest.tma import backtest_tma_v2_runner as R                  # noqa: E402
from app.backtest.tma.tma_v2_engine import build_signals_v2, compute_state_v2, warmup_bars  # noqa: E402
from app.backtest.pst.pst_indicators import aggregate                    # noqa: E402
from app.config.strategy_loader import load_strategy_config              # noqa: E402
from app.parity import parity_core as PC                                 # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


from app.utils.app_paths import APP_HOME                                 # noqa: E402
DB = str(APP_HOME / "backtest" / "backtest.db")                          # the parity pack is cut from here
os.makedirs(os.path.dirname(DB), exist_ok=True)
DAYS = build(DB, date(2025, 1, 1), date(2025, 2, 28))
CFG = dict(load_strategy_config("TMA_V2"))
CFG["mode"] = "SELL"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def spot(d):
    ds = R._day_start_epoch(d)
    return [dict(r) for r in con.execute(
        "SELECT ts,open,high,low,close FROM backtest_candles_1m WHERE underlying='NIFTY' "
        "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts", (ds, ds + 86400))], ds


def live_signals(d):
    """What the live engine computes: the prior 5 sessions + the day (same engine calls)."""
    i = DAYS.index(d)
    w5 = warmup_bars([spot(x) for x in DAYS[max(0, i - 5):i]], 5)
    s, ds = spot(d)
    bars = w5 + [b for b in aggregate(s, 5, ds) if b["complete"]]
    r = build_signals_v2(bars, len(w5), ds + 555 * 60, ds + 555 * 60, ds + 900 * 60, tf_s=300,
                         state=compute_state_v2(bars),
                         max_extension_pct=float(CFG.get("max_extension_pct") or 0),
                         slope_gate=bool(CFG.get("ema144_slope_gate")))
    return [x["ts"] for x in r["signals"]]


def run(d0, d1, **over):
    res = R.run_tma_v2_backtest(db_path=DB, strategy_id="TMA_V2", underlying="NIFTY",
                                date_from=d0, date_to=d1, config_override=dict(CFG, **over))
    return res, ((res.get("summary") or {}).get("diag_tma2") or {})


def entries(res):
    return sorted({(t.entry_ts if hasattr(t, "entry_ts") else t["entry_ts"])
                   for t in res.get("trades") or []})


print("── 1. one-day replay ≡ live's signals")
picked = [d for d in DAYS[6:] if live_signals(d)][:3]
check("found signal days in the synthetic corpus", len(picked) == 3, picked)
old_missed = 0
for d in picked:
    ls = live_signals(d)
    res, dg = run(d, d)
    check(f"{d}: 5 prior sessions seeded", dg.get("prerange_warm_sessions") == 5, dg.get("prerange_warm_sessions"))
    check(f"{d}: same signals as live ({len(ls)})", dg.get("signals_total") == len(ls), (dg.get("signals_total"), ls))
    check(f"{d}: first entry fills at the minute after live's signal",
          entries(res)[:1] == [ls[0] + 60], (entries(res), ls))
    i = DAYS.index(d)
    _, dg_old = run(DAYS[i - 1], d, pre_range_warmup=False)       # the old 2-session parity replay
    old_missed += int((dg_old.get("signals_total") or 0) < len(ls))
check("the old 2-session replay missed live's signals (the 02 Nov shape)", old_missed >= 2, old_missed)

print("── 2. unchanged where there is nothing before the range")
a, da = run(DAYS[0], DAYS[12])
b, db_ = run(DAYS[0], DAYS[12], pre_range_warmup=False)
check("range at the corpus' first day: identical trades ON vs OFF", entries(a) == entries(b) and
      len(a.get("trades") or []) == len(b.get("trades") or []))
check("…and the diag keeps its shape (no prerange key)", "prerange_warm_sessions" not in da and da.keys() == db_.keys())

print("── 3. range guards")
res1, _ = run(picked[0], picked[0])
check("a 1-session range is accepted once prior sessions exist", not res1.get("aborted"), res1.get("reason"))
res0, _ = run(DAYS[0], DAYS[0])
check("a 1-session range with nothing before it still aborts", res0.get("aborted") is True, res0)

print("── 4. parity translation for Timberwolf")
out, notes = PC.to_runner_config("TMA_V2", {"s1": {}}, date(2026, 11, 2))
check("live config without 'mode' is replayed in SELL mode, with a note",
      out.get("mode") == "SELL" and any("SELL" in n for n in notes), (out, notes))
out2, notes2 = PC.to_runner_config("TMA_V2", {"mode": "SELL"}, date(2026, 11, 2))
check("an explicit SELL is kept without a note", out2["mode"] == "SELL" and not notes2)
check("Timberwolf no longer forces a 2-session replay", "TMA_V2" not in PC.MIN_REPLAY_SESSIONS, PC.MIN_REPLAY_SESSIONS)
sk = PC.skip_notes({"total_trades": 0, "diag_tma2": {"blocked_warmup": 3, "blocked_slope": 1, "signals_total": 0}})
check("no-trade notes now name the blocked signal gates", "blocked_warmup = 3" in sk and "blocked_slope = 1" in sk, sk)

print("── 5. Timberwolf parity end to end (one-day replay, SELL mode)")
import json                                                              # noqa: E402
from app.recorder import rec_db as RD                                    # noqa: E402
import app.api.fleet_today_routes as F                                   # noqa: E402
from app.event_bus.audit_logger import audit_muted                       # noqa: E402
D = picked[0]
live_cfg = {k: v for k, v in load_strategy_config("TMA_V2").items() if k != "mode"}   # as the recorder captures it
rc = RD.connect()
rc.execute("INSERT INTO session_config (day, strategy_id, mode, degraded, config_json, config_hash, captured_at) "
           "VALUES (?,?,?,?,?,?,?)", (D.isoformat(), "TMA_V2", "PAPER", 0, json.dumps(live_cfg), "x",
                                      PC.day_start(D) + 9 * 3600 + 14 * 60))
rc.commit()
rc.close()
pack, _ = PC.build_pack(D, DB)
with audit_muted():
    bt = PC.replay("TMA_V2", D, pack, PC.to_runner_config("TMA_V2", live_cfg, D)[0])
os.unlink(pack)
check("the replay trades a SELL + hedge spread", {b["side"] for b in bt} == {"SELL", "BUY"}, bt)
APP = [{"symbol": b["symbol"], "short": b["side"] == "SELL", "qty": b["qty"], "entry": b["entry"],
        "exit": b["exit"], "entry_ts": b["entry_ts"], "exit_ts": (int(b["exit_ts"]) if b["exit"] is not None
                                                                  and b["reason"] not in PC.OPEN_REASONS else None),
        "reason": b["reason"], "open": b["exit"] is None or b["reason"] in PC.OPEN_REASONS, "book": "PAPER"}
       for b in bt if b["entry_ts"] >= PC.day_start(D)]
F._legs_for = lambda sid, warns: [dict(x) for x in APP]
with audit_muted():
    r = PC.run_day("TMA_V2", D, main_path=DB)
sm = r.get("summary") or {}
check("run_day replays the day on its own (no 'replayed from' the session before)",
      r.get("ok") and r.get("replayed_from") == D.isoformat(), (r.get("error"), r.get("replayed_from"), r.get("notes")))
check("the app's legs all match — nothing missed, nothing extra",
      sm.get("missed") == 0 and sm.get("extra") == 0 and sm.get("matched") == len(APP) > 0, sm)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL TMA2 PRE-RANGE WARM-UP CHECKS PASSED")
