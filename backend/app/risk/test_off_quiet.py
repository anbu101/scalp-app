# backend/app/risk/test_off_quiet.py
#
# ── OFF_QUIET_20261030 ── a strategy set to OFF that holds no position never
# raises bell/Telegram alerts for data hiccups it can't act on; real modes
# keep their alerts and now carry the right PAPER/LIVE tag.
#
#   cd backend && PYTHONPATH=$PWD python3 app/risk/test_off_quiet.py

import sys
import types
from pathlib import Path

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


APP = Path(__file__).resolve().parents[1]

print("── 1. EMC: OFF + flat → audit only")
import app.engine.emc.emc_manager as MG
ALERTS = []
MG.record_alert = lambda code, msg, **k: ALERTS.append((code, k.get("mode"), k.get("severity")))


def mgr(mode, pos=None):
    m = MG.EmcManager.__new__(MG.EmcManager)
    m.pos = pos
    m.cfg = lambda: {"trade_execution_mode": mode}
    return m


ALERTS.clear(); mgr("OFF")._alert("SPOT_MISSING", "no spot candle", "error")
check("OFF + flat: data alert not raised", ALERTS == [], ALERTS)
ALERTS.clear(); mgr("OFF")._alert("CHAIN_FAIL", "x", "error")
check("OFF + flat: chain failure not raised", ALERTS == [])
ALERTS.clear(); mgr("OFF", pos=object())._alert("SPOT_MISSING", "x", "error")
check("OFF with an open position: still alerts", ALERTS and ALERTS[0][0] == "SPOT_MISSING", ALERTS)
ALERTS.clear(); mgr("OFF")._alert("STALE_ROW", "old row", "error")
check("OFF: stale-row (position bookkeeping) still alerts", ALERTS and ALERTS[0][0] == "STALE_ROW")
ALERTS.clear(); mgr("OFF")._alert("SELL_FAIL", "x", "critical")
check("OFF: sell failure always alerts", bool(ALERTS))

print("── 2. EMC: correct mode tag (was always LIVE)")
ALERTS.clear(); mgr("PAPER")._alert("SPOT_MISSING", "x", "error")
check("PAPER tagged paper", ALERTS and ALERTS[0][1] == "paper", ALERTS)
ALERTS.clear(); mgr("LIVE")._alert("SPOT_MISSING", "x", "error")
check("LIVE tagged live", ALERTS and ALERTS[0][1] == "live", ALERTS)
ALERTS.clear(); mgr("PAPER_LIVE")._alert("SPOT_MISSING", "x", "error")
check("PAPER+LIVE tagged live", ALERTS and ALERTS[0][1] == "live", ALERTS)
ALERTS.clear(); mgr("OFF", pos=object())._alert("SPOT_MISSING", "x", "error")
check("OFF with position tagged off", ALERTS and ALERTS[0][1] == "off", ALERTS)

print("── 3. EMC: one transient spot-fetch failure no longer pages")
src = (APP / "engine/emc/emc_engine.py").read_text()
check("SPOT_FETCH is audit-only", '"SPOT_FETCH"' not in src and "[EMC][SPOT_FETCH]" in src)
check("SPOT_MISSING (data really late) still alerts", '"SPOT_MISSING"' in src)

print("── 4. SCALP_V1 skew gate")
try:
    import app.marketdata.zerodha_tick_engine as TE
except ModuleNotFoundError as e:
    TE = None
    print(f"  SKIP  tick engine import ({e}) — run on the build Mac")
if TE is not None:
    TG, BELL = [], []
    import app.api.telegram_api as T
    import app.event_bus.inapp_events as IE
    T.notify_critical = lambda d: TG.append(d)
    IE.record_alert = lambda code, msg, **k: BELL.append((code, k.get("mode")))

    def gate_err(mode):
        TG.clear(); BELL.clear()
        TE._strategy_mode = lambda sid: mode
        fake = types.SimpleNamespace()
        TE.ZerodhaTickEngine._skew_error_block(fake, "[SCALP_V1][SKEW] ATM quote failed (ReadTimeout)")
        return list(TG), list(BELL)

    tg, bell = gate_err("OFF")
    check("OFF: no Telegram page (was paged: OFF != PAPER)", tg == [], tg)
    tg, bell = gate_err("PAPER")
    check("PAPER: bell only", tg == [] and bell and bell[0][1] == "paper", (tg, bell))
    tg, bell = gate_err("LIVE")
    check("LIVE: bell + Telegram", bool(tg) and bell and bell[0][1] == "live", (tg, bell))
    tg, bell = gate_err("PAPER_LIVE")
    check("PAPER+LIVE: bell + Telegram", bool(tg), tg)

    import app.risk.execution_modes as XM
    orig = XM.resolve_execution_plan
    XM.resolve_execution_plan = lambda sid: ("OFF", False)
    check("helper: clean OFF read → off", TE._strategy_is_off("SCALP_V1") is True)
    XM.resolve_execution_plan = lambda sid: ("PAPER", True)
    check("helper: degraded read → not off (gate still runs)", TE._strategy_is_off("SCALP_V1") is False)
    XM.resolve_execution_plan = lambda sid: ("PAPER", False)
    check("helper: PAPER → not off", TE._strategy_is_off("SCALP_V1") is False)
    XM.resolve_execution_plan = orig

    tsrc = (APP / "marketdata/zerodha_tick_engine.py").read_text()
    i_off = tsrc.find("_strategy_is_off(self.strategy_id)")
    i_gate = tsrc.find("if not self._atm_skew_ok(symbol):")
    check("OFF drop sits before the skew gate's REST quote", 0 < i_off < i_gate, (i_off, i_gate))

print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")
