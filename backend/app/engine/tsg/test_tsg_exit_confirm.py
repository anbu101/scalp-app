# backend/app/engine/tsg/test_tsg_exit_confirm.py
#
# ── TSG_EXIT_CONFIRM_20261026 ── TSG_V1 LIVE basket exits against the
# Kite-shaped fake broker from tma2/test_tma2_exit_paths.py (resting limits
# keep working until cancelled; selling a hedge while a short is open is
# rejected on margin). Every scenario FAILS on the pre-fix manager.
# Run standalone:  python3 test_tsg_exit_confirm.py
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.engine.tma2 import test_tma2_exit_paths as T            # noqa: E402
from app.engine.tsg import tsg_manager as TM                     # noqa: E402
from app.engine.tsg.tsg_live_core import (                       # noqa: E402
    TsgDayCore, TsgLeg, L_OPEN, L_CLOSED, D_OPEN)

FAILS = []
ALERTS = []
SYM = {"L1": "NIFTY26OCT24500CE", "L2": "NIFTY26OCT23500PE",
       "L3": "NIFTY26OCT25500CE", "L4": "NIFTY26OCT22500PE"}
PX = {SYM["L1"]: 80.0, SYM["L2"]: 70.0, SYM["L3"]: 3.0, SYM["L4"]: 3.0}


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def mk(px_fn=lambda s, e: PX[s], state_dir=None):
    d = Path(state_dir or tempfile.mkdtemp(prefix="tsg_"))
    TM.STATE_DIR, TM.SESSION_PATH = d, d / "TSG_V1_session.json"
    TM.TsgManager._sweep_stale_live_rows = lambda self: None
    TM.record_alert = lambda *a, **k: None
    TM.write_audit_log = lambda m: None
    TM.TsgManager._alert = lambda self, code, msg, **k: ALERTS.append((code, msg))
    TM._now_ist = lambda: datetime(2026, 10, 28, 11, 0, tzinfo=TM.IST)
    m = TM.TsgManager()
    clock = T.Clock()
    br = T.FakeBroker(clock, px_fn)
    br.stale_ltp = {}
    clock.broker = br
    m.executor, m._sleep, m._clock = br, clock.sleep, clock
    if m._core is None:
        core = TsgDayCore()
        for lid, (act, ot) in {"L1": ("SELL", "CE"), "L2": ("SELL", "PE"),
                               "L3": ("BUY", "CE"), "L4": ("BUY", "PE")}.items():
            core.legs[lid] = TsgLeg(leg_id=lid, action=act, opt_type=ot,
                                    symbol=SYM[lid], qty=65, state=L_OPEN,
                                    entry_price=PX[SYM[lid]])
        core.state = D_OPEN
        core.entry_date = "2026-10-28"
        m._core, m._paper, m._entry_date = core, False, "2026-10-28"
        br.net = {SYM["L1"]: -65, SYM["L2"]: -65, SYM["L3"]: 65, SYM["L4"]: 65}
    return m, br


def gapped(s, e):
    return 120.0 if s == SYM["L1"] else PX[s]


def test_stuck_short_holds_its_hedge_and_retries():
    ALERTS.clear()
    m, br = mk(gapped)
    br.modify_ok = False
    br.stale_ltp = {SYM["L1"]: 80.0}                 # buy-back limit 80.80 rests
    m._execute_exits(m._core.open_ids(), "MTM_SL")
    L = m._core.legs
    check("T1: unfilled L1 buy-back → L1 NOT booked", L["L1"].state == L_OPEN)
    check("T1: L1's hedge L3 HELD (not sold while L1 short is open)",
          L["L3"].state == L_OPEN and br.net[SYM["L3"]] == 65)
    check("T1: the other pair closed normally (L2 then L4)",
          L["L2"].state == L_CLOSED and L["L4"].state == L_CLOSED)
    check("T1: the resting L1 order was CANCELLED (no stray fill later)",
          not br.open_orders())
    check("T1: alert names the open short and the manual order",
          any(c == "TSG_EXIT_STUCK" and "FIRST" in msg for c, msg in ALERTS))
    br.modify_ok = True                               # market cooperates
    m.on_minute(datetime(2026, 10, 28, 11, 1, tzinfo=TM.IST))
    check("T1: next minute retries even though the day is CLOSING",
          L["L1"].state == L_CLOSED and L["L3"].state == L_CLOSED)
    check("T1: booked at the broker fill, not the mark",
          L["L1"].exit_price == 120.0, L["L1"].exit_price)
    check("T1: broker flat", all(q == 0 for q in br.net.values()), str(br.net))


def test_unconfirmed_cancel_never_doubles_and_survives_restart():
    ALERTS.clear()
    d = tempfile.mkdtemp(prefix="tsg_")
    m, br = mk(gapped, state_dir=d)
    br.modify_ok, br.cancel_ok = False, False
    br.stale_ltp = {SYM["L1"]: 80.0}
    m._execute_exits(["L1", "L3"], "MANUAL")
    check("T2: unresolved order remembered", m._exit_working.get("L1"))
    check("T2: alert says cancel it in Kite",
          any("CANCEL IT in Kite" in msg for _, msg in ALERTS))
    # backend restarts here — same broker, fresh manager from the session file
    m2, _ = mk(gapped, state_dir=d)
    clock = br.clock
    m2.executor, m2._sleep, m2._clock = br, clock.sleep, clock
    check("T2: restart restores the pending exit + working order",
          m2._pending_exits.get("L1") and m2._exit_working.get("L1"))
    br.modify_ok, br.cancel_ok = True, True
    m2.on_minute(datetime(2026, 10, 28, 11, 2, tzinfo=TM.IST))
    buys = [o for o in br.orders if o["tradingsymbol"] == SYM["L1"]
            and o["transaction_type"] == "BUY"]
    check("T2: the SAME order was driven — exactly one L1 buy-back ever",
          len(buys) == 1 and buys[0]["status"] == "COMPLETE", str(buys))
    check("T2: then the held hedge was sold",
          m2._core.legs["L3"].state == L_CLOSED and br.net[SYM["L3"]] == 0)


if __name__ == "__main__":
    test_stuck_short_holds_its_hedge_and_retries()
    test_unconfirmed_cancel_never_doubles_and_survives_restart()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print("\nALL TSG_V1 EXIT CONFIRM TESTS PASSED")
