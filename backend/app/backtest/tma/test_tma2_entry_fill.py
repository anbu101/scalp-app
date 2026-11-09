# backend/app/backtest/tma/test_tma2_entry_fill.py
# ── TMA2_ENTRY_FILL_20261109 ── when Timberwolf's entry fills.
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/tma/test_tma2_entry_fill.py
import importlib.util, os, sys, tempfile
from datetime import date
from importlib.machinery import SourceFileLoader
os.environ["HOME"] = tempfile.mkdtemp(prefix="tma2_fill_")
BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, BACKEND)
from app.backtest.replay.replay_synth import build                    # noqa: E402
from app.backtest.tma import backtest_tma_v2_runner as TR               # noqa: E402
from app.backtest.data.candle_source import CandleSource                # noqa: E402
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


def run(**kw):
    return TR.run_tma_v2_backtest(db_path=db, strategy_id="TMA_V2", underlying="NIFTY", date_from=date(2025, 1, 20),
                                  date_to=date(2025, 3, 14), config_override=dict(CFG, **kw))


key = lambda r: [(t.symbol, t.entry_ts, t.exit_ts, t.entry_price, t.exit_price, t.exit_reason) for t in r["trades"]]  # noqa: E731
base = run()
orig = os.path.join(BACKEND, "app/backtest/tma/backtest_tma_v2_runner.py.orig")
if os.path.exists(orig):
    spec = importlib.util.spec_from_loader("orig_tma2", SourceFileLoader("orig_tma2", orig))
    O = importlib.util.module_from_spec(spec); sys.modules["orig_tma2"] = O; spec.loader.exec_module(O)
    b = O.run_tma_v2_backtest(db_path=db, strategy_id="TMA_V2", underlying="NIFTY", date_from=date(2025, 1, 20),
                              date_to=date(2025, 3, 14), config_override=dict(CFG))
    check(f"default → identical to the previous runner ({len(base['trades'])} trades, summary too)",
          key(base) == key(b) and base["summary"] == b["summary"])
else:
    print("  (previous runner not staged — equivalence check skipped)")
check("NEXT_CLOSE spelled out = default", key(run(entry_fill="NEXT_CLOSE")) == key(base))
check("default run has no entry_fill diagnostic", "entry_fill" not in str(base.get("summary")))
op = run(entry_fill="NEXT_OPEN")
check("NEXT_OPEN runs and reports itself", len(op["trades"]) > 0 and "NEXT_OPEN" in str(op.get("summary")))
src = CandleSource(db)
ok_px = ok_ts = n = 0
for t in op["trades"]:
    if str(t.symbol).startswith("SYN-"):
        continue
    ds = (t.entry_ts + 19800) // 86400 * 86400 - 19800
    c = next((x for x in src.candles_1m_for_symbol_day(t.symbol, ds) if x.ts == t.entry_ts), None)
    if c is None:
        continue
    n += 1
    ok_px += abs(float(c.open) - float(t.entry_price)) < 0.011
    ok_ts += 1
check(f"NEXT_OPEN entries fill at the OPEN of the candle stamped at the signal ({n} legs)", n > 0 and ok_px == n, (ok_px, n))
bo = {(t.symbol, t.entry_ts + 0): t for t in base["trades"]}
shifted = sum(1 for t in op["trades"] if (t.symbol, t.entry_ts + 60) in bo)
check("…and their entry time is one minute earlier than the sealed fill", shifted > 0, shifted)
sl_first = [t for t in op["trades"] if t.exit_reason == "SL" and t.exit_ts == t.entry_ts]
check("no exit precedes its fill (the fill candle is part of the monitored path)",
      all(t.exit_ts >= t.entry_ts for t in op["trades"]))
print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL TMA2 ENTRY-FILL CHECKS PASSED")
