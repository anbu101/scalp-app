# backend/app/recorder/test_seam.py — ── SEAM_CHECK_20261031 ── ── RECORDED_SOURCE_20261031 ──
#   cd backend && PYTHONPATH=$PWD python3 app/recorder/test_seam.py
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


os.environ["HOME"] = tempfile.mkdtemp(prefix="seam_")
IST = timezone(timedelta(hours=5, minutes=30))
from app.utils.app_paths import APP_HOME                    # noqa: E402
from app.event_bus.audit_logger import audit_muted          # noqa: E402
import app.backtest.backfill.kite_backfill as KB            # noqa: E402
KB.THROTTLE_S = 0.0
from app.backtest.replay.replay_synth import build          # noqa: E402
from app.recorder import rec_db as RD                       # noqa: E402
from app.recorder import rec_pull as RP                     # noqa: E402
from app.recorder import rec_seam as SM                     # noqa: E402

MAIN = APP_HOME / "backtest" / "backtest.db"
MAIN.parent.mkdir(parents=True, exist_ok=True)
build(str(MAIN), date(2025, 1, 1), date(2025, 2, 28))
src = sqlite3.connect(str(MAIN))
c = RD.connect()
RD.set_meta(c, "cutover", "2025-02-27")
c.close()
RP.now_ts = lambda: int(datetime(2025, 3, 3, 18, 0, tzinfo=IST).timestamp())


class FakeKite:
    def __init__(self, shift=0, drop=0, bump=0.0):
        self.shift, self.drop, self.bump = shift, drop, bump

    def historical_data(self, instrument_token, from_date, to_date, interval, oi=False):
        lo = int(datetime.fromisoformat(from_date).replace(tzinfo=IST).timestamp())
        hi = int(datetime.fromisoformat(to_date).replace(tzinfo=IST).timestamp())
        rows = src.execute("SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE instrument_type='SPOT' "
                           "AND ts>=? AND ts<=? ORDER BY ts", (lo, hi)).fetchall()
        rows = rows[self.drop:]
        return [{"date": datetime.fromtimestamp(t + self.shift * 60, IST), "open": o, "high": h, "low": l,
                 "close": cl + self.bump, "volume": 0} for t, o, h, l, cl in rows]


print("── 1. seam check")
r = SM.run(3, kite=FakeKite(), main=MAIN)
check("identical candles → pass: 375 common minutes, shift 0, Δclose 0", r["ok"] and all(
    d["common"] == 375 and d["shift_min"] == 0 and d["mean_abs_close"] == 0 for d in r["days"]) and len(r["days"]) == 3, r["days"])
check("only Dhan-era sessions are sampled (before the cutover)", all(d["day"] < "2025-02-27" for d in r["days"]), r["days"])
check("verdict stored for the Run tab", SM.last()["ok"] is True)
r = SM.run(2, kite=FakeKite(shift=1), main=MAIN)
check("Kite stamped one minute later → found as shift +1, fails", (not r["ok"]) and all(d["shift_min"] == 1 for d in r["days"]), r["days"])
r = SM.run(2, kite=FakeKite(drop=30), main=MAIN)
check("30 minutes missing on the Kite side → fails (< 370 common), reported per side",
      (not r["ok"]) and all(d["common"] == 345 and d["only_corpus"] == 30 for d in r["days"]), r["days"])
r = SM.run(2, kite=FakeKite(bump=1.25), main=MAIN)
check("closes ₹1.25 apart → fails on mean |Δclose|", (not r["ok"]) and all(d["mean_abs_close"] == 1.25 for d in r["days"]), r["days"])
_rk = RP.resolve_kite
RP.resolve_kite = lambda: None
check("no Kite session → clear error, nothing stored", SM.run(2, main=MAIN).get("error", "").startswith("no Kite session"))
RP.resolve_kite = _rk
SM.run(3, kite=FakeKite(), main=MAIN)

print("── 2. recorded data source")
from app.backtest import queue_worker as QW                 # noqa: E402
from app.config.strategy_loader import load_strategy_config  # noqa: E402
cfg = {**load_strategy_config("EMC_V1"), "data_source": "recorded"}
c = RD.connect()
RD.set_meta(c, "seam_check", json.dumps({"ok": False}))
c.close()
with audit_muted():
    r = QW._dispatch_run_impl(strategy_id="EMC_V1", underlying="NIFTY", df=date(2025, 2, 24), dt=date(2025, 2, 27),
                              config=cfg, progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)
check("without a passed seam check → refused with the reason", r.get("aborted") and "seam check" in r["reason"], r.get("reason"))
SM.run(3, kite=FakeKite(), main=MAIN)
with audit_muted():
    r = QW._dispatch_run_impl(strategy_id="EMC_V1", underlying="NIFTY", df=date(2025, 2, 24), dt=date(2025, 2, 27),
                              config=cfg, progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)
check("after a pass → the run reads the pack (corpus ∪ recorder) and trades", not r.get("aborted") and len(r.get("trades") or []) > 0,
      (r.get("reason"), len(r.get("trades") or [])))
check("overrides are reset after the run", QW.DB_OVERRIDE.get() is None)
with audit_muted():
    r = QW._dispatch_run_impl(strategy_id="EMC_V1", underlying="NIFTY", df=date(2024, 6, 1), dt=date(2025, 2, 27),
                              config=cfg, progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)
check("ranges over 120 days are refused", r.get("aborted") and "120" in r["reason"], r.get("reason"))
with audit_muted():
    r2 = QW._dispatch_run_impl(strategy_id="EMC_V1", underlying="NIFTY", df=date(2025, 2, 24), dt=date(2025, 2, 27),
                               config={k: v for k, v in cfg.items() if k != "data_source"},
                               progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)
check("a normal run (no data_source) is unchanged", not r2.get("aborted") and len(r2.get("trades") or []) > 0)

print("── 3. coverage reports the toggle state")
import app.api.backtest_routes as BRT                       # noqa: E402
info = BRT._recorded_info()
check("coverage info: seam passed, recorded_from from the recorder's clean days", info["seam_ok"] is True and "recorded_from" in info, info)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL SEAM / RECORDED_SOURCE CHECKS PASSED")
