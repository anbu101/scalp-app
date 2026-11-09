# backend/app/backtest/tma/test_tma2_entry_slip.py
# ── TMA2_ENTRY_SLIP_20261109 ── adverse entry fill on Timberwolf's main leg.
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/tma/test_tma2_entry_slip.py
import os, sys, tempfile
from datetime import date
os.environ["HOME"] = tempfile.mkdtemp(prefix="tma2_slip_")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from app.backtest.replay.replay_synth import build                    # noqa: E402
from app.backtest.tma import backtest_tma_v2_runner as TR               # noqa: E402
from app.config.strategy_loader import load_strategy_config             # noqa: E402
from app.parity import parity_core as PC                                # noqa: E402

OK, FAILS = [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


db = os.path.join(os.environ["HOME"], "corpus.db")
build(db, date(2025, 1, 1), date(2025, 3, 14))
CFG = PC.to_runner_config("TMA_V2", dict(load_strategy_config("TMA_V2")), date(2025, 2, 3))[0]
D0, D1 = date(2025, 1, 20), date(2025, 3, 14)


def run(**kw):
    return TR.run_tma_v2_backtest(db_path=db, strategy_id="TMA_V2", underlying="NIFTY", date_from=D0, date_to=D1,
                                  config_override=dict(CFG, **kw))


def g(t, *n):
    for x in n:
        v = t.get(x) if isinstance(t, dict) else getattr(t, x, None)
        if v is not None:
            return v


def legs(res, direction):
    return [t for t in res["trades"] if str(g(t, "direction", "trade_direction", "side_action") or "").upper().startswith(direction)
            or str(g(t, "action") or "").upper() == direction]


base = run()
same = run(entry_slip_pts=0, entry_slip_pct=0)
key = lambda r: [(g(t, "symbol"), g(t, "entry_ts"), g(t, "exit_ts"), round(float(g(t, "entry_price")), 2),  # noqa: E731
                  g(t, "exit_reason")) for t in r["trades"]]
check(f"0 / 0 → identical trades ({len(base['trades'])})", key(base) == key(same) and len(base["trades"]) > 0)
check("…and no slip keys in the diagnostics", "entry_slip" not in str((base.get("summary") or {})))
s5 = run(entry_slip_pts=5)
bmap = {(g(t, "symbol"), g(t, "entry_ts")): t for t in base["trades"]}
pairs = [(bmap[(g(t, "symbol"), g(t, "entry_ts"))], t) for t in s5["trades"] if (g(t, "symbol"), g(t, "entry_ts")) in bmap]
sells = [(a, b) for a, b in pairs if str(g(a, "direction", "trade_direction") or "").upper() in ("SELL", "SHORT")]
buys = [(a, b) for a, b in pairs if str(g(a, "direction", "trade_direction") or "").upper() in ("BUY", "LONG")]
check("SELL main legs fill 5 pts LOWER", sells and all(abs(float(g(a, "entry_price")) - 5 - float(g(b, "entry_price"))) < 0.011
                                                        for a, b in sells), sells[:2])
check("hedge (BUY) legs are not slipped", buys and all(abs(float(g(a, "entry_price")) - float(g(b, "entry_price"))) < 0.011
                                                       for a, b in buys), buys[:2])
sl_ok = [abs(float(g(b, "sl", "sl_price")) - float(g(b, "entry_price")) * (1 + float(CFG["s1"]["main"]["sl_pct"]) / 100)) < 0.02
         for a, b in sells if g(b, "sl", "sl_price") and str(CFG["s1"]["main"].get("sl_unit", "PCT")).upper() == "PCT"]
check("the %-SL is anchored on the slipped fill", sl_ok and all(sl_ok), sl_ok[:5])
p2 = run(entry_slip_pct=2)
pp = [(bmap[(g(t, "symbol"), g(t, "entry_ts"))], t) for t in p2["trades"] if (g(t, "symbol"), g(t, "entry_ts")) in bmap]
ps = [(a, b) for a, b in pp if str(g(a, "direction", "trade_direction") or "").upper() in ("SELL", "SHORT")]
check("2% → a SELL fills 2% of the modelled fill lower", ps and all(
    abs(float(g(a, "entry_price")) * 0.98 - float(g(b, "entry_price"))) < 0.011 for a, b in ps), ps[:2])
sl_n = lambda r: sum(1 for t in r["trades"] if str(g(t, "exit_reason")) == "SL")    # noqa: E731
hi = run(entry_slip_pts=15)
check("more slippage never means fewer stops (0 / 5 / 15 pts)", sl_n(base) <= sl_n(s5) <= sl_n(hi), (sl_n(base), sl_n(s5), sl_n(hi)))
diag = str(hi.get("summary") or {})
check("diagnostics say how much was slipped", "entry_slip" in diag and "entry_slip_pts_total" in diag, diag[:300])
print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL TMA2 ENTRY-SLIP CHECKS PASSED")
